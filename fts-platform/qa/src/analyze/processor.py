"""
Unified chunk processing: detection + collection + status.

Wraps StreamingCrossingDetector pair + EdgeCollector.
"""

from typing import Callable, Optional

import numpy as np

from analyze.collector import EdgeCollector, MinuteBucket, CollectorStatus
from analyze.stats import JitterStats, compute_stats
from detect import StreamingCrossingDetector


class ChunkProcessor:
    """
    Unified chunk processing for USRP streaming.

    Wraps:
    - Pair of StreamingCrossingDetector (one per channel)
    - EdgeCollector (minute-aligned bucketing + background processing)
    - Status printing

    Usage:
        processor = ChunkProcessor(sample_rate, pulse_freq, threshold, on_minute_stats)
        capture.stream(callback=lambda data, t: processor.process(data, t) or True, ...)
    """

    def __init__(
        self,
        sample_rate: float,
        pulse_freq: float,
        threshold: float,
        on_minute_stats: Callable[[MinuteBucket, JitterStats], None],
        on_edge: Optional[Callable[[float, Optional[float], Optional[int], Optional[int]], None]] = None,
    ):
        """
        Initialize chunk processor.

        Args:
            sample_rate: Sample rate in Hz
            pulse_freq: Expected pulse frequency in Hz
            threshold: Edge detection threshold
            on_minute_stats: Called from processing thread with (bucket, stats) for each minute
            on_edge: Optional callback for real-time edge publishing (MQTT)
                     Called with (gpsdo_time, delay_ns or None, ch_a_ns or None, ch_b_ns or None)
        """
        self._sample_rate = sample_rate
        self._pulse_freq = pulse_freq
        self._on_minute_stats = on_minute_stats

        # Detector setup
        samples_per_period = sample_rate / pulse_freq
        min_distance = int(samples_per_period * 0.4)

        self._detector_a = StreamingCrossingDetector(
            threshold=threshold,
            min_distance=min_distance,
            skip_samples=int(0.1 * sample_rate),
        )
        self._detector_b = StreamingCrossingDetector(
            threshold=threshold,
            min_distance=min_distance,
            skip_samples=int(0.1 * sample_rate),
        )

        # Collector with bucket processing
        self._collector = EdgeCollector(
            sample_rate=sample_rate,
            on_bucket_complete=self._process_bucket,
            on_edge=on_edge,
        )

        self._overflow_count = 0

    def process(self, data: np.ndarray, chunk_time: float) -> None:
        """
        Process one chunk of IQ data.

        Called from USRP streaming callback.

        Args:
            data: Complex IQ data (real=chan_a, imag=chan_b)
            chunk_time: GPSDO timestamp of first sample in chunk
        """
        # Detect edges on both channels
        edges_a = self._detector_a.process(data.real)
        edges_b = self._detector_b.process(data.imag)

        # Extract falling edges (type == 1)
        falling_a = edges_a[edges_a[:, 1] == 1, 0] if len(edges_a) > 0 else np.array([])
        falling_b = edges_b[edges_b[:, 1] == 1, 0] if len(edges_b) > 0 else np.array([])

        # Add to collector (handles matching, bucketing, callbacks)
        self._collector.add_edges(
            falling_a, falling_b, chunk_time,
            self._detector_a.samples_processed - len(data),
            self._pulse_freq,
        )
        self._collector.update_samples(self._detector_a.samples_processed)
        self._collector.set_overflow_count(self._overflow_count)

    def _process_bucket(self, bucket: MinuteBucket) -> None:
        """Called from processing thread for completed minute."""
        from analyze.jitter import match_edges

        if len(bucket.edges_a) < 10 or len(bucket.edges_b) < 10:
            print(f"[MINUTE {bucket.minute_str}] Too few edges: A={len(bucket.edges_a)} B={len(bucket.edges_b)}")
            return

        # Match edges and compute stats
        times_a = np.array(bucket.edges_a)
        times_b = np.array(bucket.edges_b)
        result = match_edges(times_a, times_b, sample_rate=1.0, pulse_freq=self._pulse_freq)

        if len(result.delays) < 10:
            print(f"[MINUTE {bucket.minute_str}] Too few matches: {len(result.delays)}")
            return

        stats = compute_stats(result.delays, self._pulse_freq)
        self._on_minute_stats(bucket, stats)

    def set_overflow_count(self, count: int) -> None:
        """Update overflow count from USRP capture."""
        self._overflow_count = count

    def get_status(self) -> CollectorStatus:
        """Get current status for console output."""
        return self._collector.get_status()

    def print_status(self, elapsed: float, last_status: CollectorStatus) -> CollectorStatus:
        """
        Print 10-second status line with deltas.

        Args:
            elapsed: Seconds since start
            last_status: Status from previous call

        Returns:
            Current status (for next call)
        """
        status = self.get_status()

        # Compute deltas since last report
        delta_a = status.edges_a_total - last_status.edges_a_total
        delta_b = status.edges_b_total - last_status.edges_b_total
        delta_matched = status.matched_total - last_status.matched_total
        delta_unmatched = (status.unmatched_a_total - last_status.unmatched_a_total +
                          status.unmatched_b_total - last_status.unmatched_b_total)

        print(f"[{elapsed:5.1f}s] {status.samples_processed/1e6:.1f}M samples | "
              f"A={delta_a} B={delta_b} | "
              f"matched={delta_matched} unmatched={delta_unmatched} | "
              f"mins={status.minutes_processed} | ovf={status.overflow_count}")

        return status

    def print_final_stats(self) -> None:
        """Print final statistics summary."""
        status = self.get_status()
        print("\n--- Final Statistics ---")
        print(f"Total samples: {status.samples_processed:,}")
        print(f"Total edges: A={status.edges_a_total} B={status.edges_b_total}")
        print(f"Total matched: {status.matched_total}")
        print(f"Total unmatched: A={status.unmatched_a_total} B={status.unmatched_b_total}")
        print(f"Minutes processed: {status.minutes_processed}")
        print(f"UHD overflows: {status.overflow_count}")

    def flush(self) -> None:
        """Flush current bucket (for clean shutdown)."""
        self._collector.flush()

    def stop(self) -> None:
        """Stop processing thread."""
        self._collector.stop()
