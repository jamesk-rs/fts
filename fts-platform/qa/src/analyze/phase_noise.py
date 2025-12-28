"""
Phase noise analysis via FFT.

Computes single-sideband phase noise L(f) from delay measurements.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional


# Standard logarithmic frequency bins (Hz)
# Note: 0.1 Hz requires >100s of data (we use 60s windows)
# Note: 1000 Hz is exactly Nyquist for 2kHz pulse rate (edge case)
LOG_FREQ_BINS = np.array([
    0.2, 0.5,
    1.0, 2.0, 5.0,
    10.0, 20.0, 50.0,
    100.0, 200.0, 500.0
])


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
        """Convert to dictionary for JSON serialization."""
        result = {
            'sample_count': self.sample_count,
            'duration_seconds': self.duration_seconds,
            'pulse_freq': self.pulse_freq,
            'rms_rad': float(self.rms_rad) if np.isfinite(self.rms_rad) else None,
            'rms_jitter_ns': float(self.rms_jitter_ns) if np.isfinite(self.rms_jitter_ns) else None,
        }
        # Add each frequency bin as a separate field
        for freq, lf in zip(self.frequencies, self.l_f):
            # Field name: f_0p1 for 0.1 Hz, f_1 for 1 Hz, f_1000 for 1000 Hz
            if freq < 1:
                key = f"f_0p{int(freq * 10)}"
            else:
                key = f"f_{int(freq)}"
            result[key] = float(lf) if np.isfinite(lf) else None
        return result

    def __str__(self) -> str:
        lines = [
            f"Phase Noise (n={self.sample_count}, T={self.duration_seconds:.1f}s)",
            f"  RMS: {self.rms_rad*1000:.3f} mrad ({self.rms_jitter_ns:.3f} ns)",
        ]
        for freq, lf in zip(self.frequencies, self.l_f):
            if np.isfinite(lf):
                lines.append(f"  L({freq:>6.1f} Hz) = {lf:>7.1f} dBc/Hz")
        return "\n".join(lines)


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
        freq_bins: Frequency bins to extract (default: LOG_FREQ_BINS)

    Returns:
        PhaseNoiseResult with L(f) at specified frequency bins, or None if insufficient data
    """
    if freq_bins is None:
        freq_bins = LOG_FREQ_BINS

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


def compute_rms_phase_noise(
    delays_seconds: np.ndarray,
    pulse_freq: float = 2000.0,
    f_low: float = 1.0,
    f_high: float = 100.0,
) -> Optional[float]:
    """
    Compute integrated RMS phase noise over a frequency range.

    Args:
        delays_seconds: Array of time delays in seconds
        pulse_freq: Pulse frequency in Hz
        f_low: Lower integration bound (Hz)
        f_high: Upper integration bound (Hz)

    Returns:
        RMS phase noise in radians, or None if insufficient data
    """
    n_samples = len(delays_seconds)
    if n_samples < 2:
        return None

    # Convert to phase
    phase_rad = 2.0 * np.pi * pulse_freq * delays_seconds
    phase_rad = phase_rad - np.mean(phase_rad)

    # Apply window
    window = np.hanning(n_samples)
    phase_windowed = phase_rad * window
    s2 = np.mean(window ** 2)

    # FFT and PSD
    fft_result = np.fft.rfft(phase_windowed)
    psd = (np.abs(fft_result) ** 2) / (n_samples * pulse_freq * s2)
    psd[1:-1] *= 2
    frequencies = np.fft.rfftfreq(n_samples, d=1.0/pulse_freq)

    # Integrate PSD over frequency range
    mask = (frequencies >= f_low) & (frequencies <= f_high)
    if not np.any(mask):
        return None

    df = frequencies[1] - frequencies[0]  # Frequency resolution
    integrated = np.sum(psd[mask]) * df

    return np.sqrt(integrated)
