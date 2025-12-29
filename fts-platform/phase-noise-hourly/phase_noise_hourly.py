#!/usr/bin/env python3
"""
Hourly Phase Noise Calculation Service.

Incrementally processes edge delay data from InfluxDB, computes FFT-based
phase noise with 1-hour windows, and stores results back to InfluxDB.

Benefits of 1-hour windows over 1-minute:
- Frequency resolution: 1/3600 ~ 0.0003 Hz (vs 1/60 ~ 0.017 Hz)
- Reliable measurement down to 0.01 Hz (360 cycles per hour)
- Excellent quality at 0.1 Hz (360 cycles vs 6 cycles in 1-min)
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass

import numpy as np
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

# Extended frequency bins for hourly analysis
# With 1-hour windows, we can reliably measure down to 0.01 Hz (360 cycles)
HOURLY_FREQ_BINS = np.array([
    0.01, 0.02, 0.05,      # Very low (needs 1hr window)
    0.1, 0.2, 0.5,         # Low frequency
    1.0, 2.0, 5.0,         # Medium
    10.0, 20.0, 50.0,      # High
    100.0, 200.0, 500.0    # Very high
])

# Measurement names
EDGES_MEASUREMENT = "edges"
PHASE_NOISE_MEASUREMENT = "phase_noise_hourly"
CHECKPOINT_MEASUREMENT = "_checkpoints"
CHECKPOINT_SERVICE = "phase_noise_hourly"

# Expected pulse frequency
PULSE_FREQ = 2000.0  # Hz

# Polling interval when waiting for data
POLL_INTERVAL_SECONDS = 60


@dataclass
class PhaseNoiseResult:
    """Container for phase noise measurement results."""
    frequencies: np.ndarray      # Frequency bins (Hz)
    l_f: np.ndarray             # L(f) values (dBc/Hz)
    sample_count: int           # Number of delay samples used
    duration_seconds: float     # Measurement duration
    pulse_freq: float           # Pulse frequency used
    rms_rad: float              # Integrated RMS phase noise (radians)
    rms_jitter_ns: float        # RMS timing jitter (nanoseconds)

    def to_dict(self) -> dict:
        """Convert to dictionary for InfluxDB fields."""
        result = {
            'sample_count': self.sample_count,
            'duration_seconds': self.duration_seconds,
            'pulse_freq': self.pulse_freq,
        }
        # Only include finite values
        if np.isfinite(self.rms_rad):
            result['rms_rad'] = float(self.rms_rad)
        if np.isfinite(self.rms_jitter_ns):
            result['rms_jitter_ns'] = float(self.rms_jitter_ns)

        # Add each frequency bin as a separate field
        for freq, lf in zip(self.frequencies, self.l_f):
            if not np.isfinite(lf):
                continue
            # Field name: f_0p01 for 0.01 Hz, f_1 for 1 Hz, etc.
            if freq < 1:
                # Format: 0.01 -> f_0p01, 0.1 -> f_0p1, 0.5 -> f_0p5
                frac = int(freq * 100)
                if frac % 10 == 0:
                    key = f"f_0p{frac // 10}"
                else:
                    key = f"f_0p{frac:02d}"
            else:
                key = f"f_{int(freq)}"
            result[key] = float(lf)
        return result


def compute_phase_noise(
    delays_seconds: np.ndarray,
    pulse_freq: float = 2000.0,
    freq_bins: Optional[np.ndarray] = None,
) -> Optional[PhaseNoiseResult]:
    """
    Compute single-sideband phase noise L(f) from delay measurements.

    Args:
        delays_seconds: Array of time delays in seconds (uniformly sampled at pulse_freq)
        pulse_freq: Pulse/sampling frequency in Hz (default 2000 Hz)
        freq_bins: Frequency bins to extract (default: HOURLY_FREQ_BINS)

    Returns:
        PhaseNoiseResult with L(f) at specified frequency bins, or None if insufficient data
    """
    if freq_bins is None:
        freq_bins = HOURLY_FREQ_BINS

    n_samples = len(delays_seconds)
    if n_samples < 2:
        return None

    duration = n_samples / pulse_freq

    # Need at least 10 cycles at lowest frequency for meaningful measurement
    min_freq = freq_bins[0]
    if duration < 10 / min_freq:
        # Filter out frequency bins we can't measure
        freq_bins = freq_bins[freq_bins >= 10 / duration]
        if len(freq_bins) == 0:
            return None

    # Convert delays to phase error (radians)
    # phi = 2*pi * f_pulse * delay
    phase_rad = 2.0 * np.pi * pulse_freq * delays_seconds

    # Remove DC (mean phase offset)
    phase_rad = phase_rad - np.mean(phase_rad)

    # Apply Hanning window to reduce spectral leakage
    window = np.hanning(n_samples)
    phase_windowed = phase_rad * window

    # Compute FFT
    fft_result = np.fft.rfft(phase_windowed)

    # Compute single-sided power spectral density
    # PSD = |FFT|^2 / (N * fs * S2)
    # where S2 = sum(window^2) / N is the window power correction
    s2 = np.mean(window ** 2)
    psd = (np.abs(fft_result) ** 2) / (n_samples * pulse_freq * s2)

    # Double for single-sided spectrum (except DC and Nyquist)
    psd[1:-1] *= 2

    # Frequency axis
    frequencies = np.fft.rfftfreq(n_samples, d=1.0/pulse_freq)

    # Extract L(f) at requested frequency bins via interpolation
    l_f = np.zeros(len(freq_bins))
    for i, f_target in enumerate(freq_bins):
        if f_target > frequencies[-1]:
            # Above Nyquist
            l_f[i] = np.nan
        elif f_target < frequencies[1]:
            # Below resolution
            l_f[i] = np.nan
        else:
            # Linear interpolation in log space
            idx = np.searchsorted(frequencies, f_target)
            if idx == 0:
                psd_interp = psd[0]
            elif idx >= len(frequencies):
                psd_interp = psd[-1]
            else:
                # Interpolate between adjacent bins
                f_lo, f_hi = frequencies[idx-1], frequencies[idx]
                p_lo, p_hi = psd[idx-1], psd[idx]
                alpha = (f_target - f_lo) / (f_hi - f_lo)
                psd_interp = p_lo + alpha * (p_hi - p_lo)

            # Convert to L(f) in dBc/Hz
            # L(f) = 10*log10(S_phi(f) / 2) for single-sideband
            if psd_interp > 0:
                l_f[i] = 10.0 * np.log10(psd_interp / 2.0)
            else:
                l_f[i] = np.nan

    # Compute integrated RMS phase noise over full bandwidth
    # Integrate PSD from first measurable bin to Nyquist
    df = frequencies[1] - frequencies[0]
    # Skip DC bin, integrate to Nyquist
    integrated_power = np.sum(psd[1:]) * df
    rms_rad = np.sqrt(integrated_power)

    # Convert to RMS timing jitter: jitter = phase / (2*pi*f)
    rms_jitter_seconds = rms_rad / (2.0 * np.pi * pulse_freq)
    rms_jitter_ns = rms_jitter_seconds * 1e9

    return PhaseNoiseResult(
        frequencies=freq_bins.copy(),
        l_f=l_f,
        sample_count=n_samples,
        duration_seconds=duration,
        pulse_freq=pulse_freq,
        rms_rad=rms_rad,
        rms_jitter_ns=rms_jitter_ns,
    )


class PhaseNoiseService:
    """Hourly phase noise calculation service."""

    def __init__(self):
        # InfluxDB configuration from environment
        self.influx_url = os.environ.get("INFLUX_URL", "http://influxdb:8086")
        self.influx_token = os.environ.get("INFLUX_TOKEN", "")
        self.influx_org = os.environ.get("INFLUX_ORG", "fts")
        self.influx_bucket = os.environ.get("INFLUX_BUCKET", "fts")

        if not self.influx_token:
            log.error("INFLUX_TOKEN environment variable is required")
            sys.exit(1)

        self.client = InfluxDBClient(
            url=self.influx_url,
            token=self.influx_token,
            org=self.influx_org,
        )
        self.query_api = self.client.query_api()
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)

        log.info(f"Connected to InfluxDB at {self.influx_url}")
        log.info(f"Organization: {self.influx_org}, Bucket: {self.influx_bucket}")

    def read_checkpoint(self) -> Optional[datetime]:
        """Read the last processed timestamp from InfluxDB."""
        query = f'''
        from(bucket: "{self.influx_bucket}")
            |> range(start: -30d)
            |> filter(fn: (r) => r._measurement == "{CHECKPOINT_MEASUREMENT}")
            |> filter(fn: (r) => r.service == "{CHECKPOINT_SERVICE}")
            |> filter(fn: (r) => r._field == "last_processed_time")
            |> last()
        '''
        try:
            tables = self.query_api.query(query)
            for table in tables:
                for record in table.records:
                    # The value is stored as RFC3339 string
                    timestamp_str = record.get_value()
                    if timestamp_str:
                        return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except Exception as e:
            log.warning(f"Error reading checkpoint: {e}")
        return None

    def write_checkpoint(self, timestamp: datetime) -> None:
        """Write the checkpoint timestamp to InfluxDB."""
        point = (
            Point(CHECKPOINT_MEASUREMENT)
            .tag("service", CHECKPOINT_SERVICE)
            .field("last_processed_time", timestamp.isoformat())
            .time(datetime.now(timezone.utc), WritePrecision.NS)
        )
        self.write_api.write(bucket=self.influx_bucket, record=point)
        log.info(f"Checkpoint updated to {timestamp.isoformat()}")

    def query_delays(self, start: datetime, end: datetime) -> np.ndarray:
        """Query delay_ns values from edges measurement."""
        query = f'''
        from(bucket: "{self.influx_bucket}")
            |> range(start: {start.isoformat()}, stop: {end.isoformat()})
            |> filter(fn: (r) => r._measurement == "{EDGES_MEASUREMENT}")
            |> filter(fn: (r) => r._field == "delay_ns")
            |> sort(columns: ["_time"])
        '''
        delays = []
        try:
            tables = self.query_api.query(query)
            for table in tables:
                for record in table.records:
                    value = record.get_value()
                    if value is not None:
                        delays.append(float(value))
        except Exception as e:
            log.error(f"Error querying delays: {e}")
            return np.array([])

        return np.array(delays)

    def get_latest_edge_time(self) -> Optional[datetime]:
        """Get the timestamp of the most recent edge."""
        query = f'''
        from(bucket: "{self.influx_bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r._measurement == "{EDGES_MEASUREMENT}")
            |> filter(fn: (r) => r._field == "delay_ns")
            |> last()
        '''
        try:
            tables = self.query_api.query(query)
            for table in tables:
                for record in table.records:
                    return record.get_time()
        except Exception as e:
            log.warning(f"Error getting latest edge time: {e}")
        return None

    def write_phase_noise(self, result: PhaseNoiseResult, window_start: datetime) -> None:
        """Write phase noise result to InfluxDB."""
        point = Point(PHASE_NOISE_MEASUREMENT)

        for key, value in result.to_dict().items():
            point = point.field(key, value)

        # Use window start time as the point timestamp
        point = point.time(window_start, WritePrecision.NS)

        self.write_api.write(bucket=self.influx_bucket, record=point)
        log.info(f"Wrote phase noise result for {window_start.isoformat()}: "
                 f"{result.sample_count} samples, rms_jitter={result.rms_jitter_ns:.3f}ns")

    def process_hour(self, window_start: datetime) -> bool:
        """
        Process one hour of data starting at window_start.

        Returns:
            True if successfully processed, False if insufficient data
        """
        window_end = window_start + timedelta(hours=1)

        log.info(f"Processing hour: {window_start.isoformat()} to {window_end.isoformat()}")

        # Query delays for this hour
        delays_ns = self.query_delays(window_start, window_end)

        if len(delays_ns) < 1000:
            log.warning(f"Insufficient data: only {len(delays_ns)} samples (need >1000)")
            return False

        # Convert ns to seconds for FFT
        delays_seconds = delays_ns * 1e-9

        # Compute phase noise
        result = compute_phase_noise(
            delays_seconds,
            pulse_freq=PULSE_FREQ,
            freq_bins=HOURLY_FREQ_BINS,
        )

        if result is None:
            log.warning("Phase noise computation failed")
            return False

        # Write result to InfluxDB
        self.write_phase_noise(result, window_start)

        return True

    def run(self) -> None:
        """Main processing loop."""
        log.info("Starting hourly phase noise calculation service")

        while True:
            try:
                # Read checkpoint (last processed time)
                checkpoint = self.read_checkpoint()

                if checkpoint is None:
                    # No checkpoint - find the oldest edge and start from there
                    # Round down to the hour
                    latest = self.get_latest_edge_time()
                    if latest is None:
                        log.info("No edge data found, waiting...")
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue

                    # Start from 2 hours before the latest data to ensure we have complete hours
                    checkpoint = latest.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
                    log.info(f"No checkpoint found, starting from {checkpoint.isoformat()}")

                # Get the latest edge timestamp
                latest_edge = self.get_latest_edge_time()
                if latest_edge is None:
                    log.info("No edge data available, waiting...")
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue

                # Calculate next window to process
                next_window_start = checkpoint.replace(minute=0, second=0, microsecond=0)
                next_window_end = next_window_start + timedelta(hours=1)

                # Check if we have enough data for the next hour
                if latest_edge < next_window_end:
                    time_remaining = (next_window_end - latest_edge).total_seconds()
                    log.info(f"Waiting for data: {time_remaining:.0f}s until hour complete "
                             f"(need data until {next_window_end.isoformat()})")
                    time.sleep(min(POLL_INTERVAL_SECONDS, max(10, time_remaining)))
                    continue

                # Process the hour
                if self.process_hour(next_window_start):
                    # Update checkpoint to the end of this window
                    self.write_checkpoint(next_window_end)
                else:
                    # Processing failed, still update checkpoint to avoid getting stuck
                    log.warning(f"Skipping hour {next_window_start.isoformat()} due to insufficient data")
                    self.write_checkpoint(next_window_end)

            except KeyboardInterrupt:
                log.info("Shutting down...")
                break
            except Exception as e:
                log.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(POLL_INTERVAL_SECONDS)

    def close(self) -> None:
        """Clean up resources."""
        self.client.close()


def main():
    service = PhaseNoiseService()
    try:
        service.run()
    finally:
        service.close()


if __name__ == "__main__":
    main()
