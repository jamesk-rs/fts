"""
Edge collection with minute-aligned buckets.

Collects edges into fixed 1-minute windows aligned to GPS minute boundaries.
Processing is offloaded to a background thread.
"""

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import numpy as np


@dataclass
class MinuteBucket:
    """Edges collected during one complete minute."""

    minute_epoch: int  # GPS minute (unix timestamp // 60)
    edges_a: list[float] = field(default_factory=list)  # Edge times (GPSDO seconds)
    edges_b: list[float] = field(default_factory=list)
    sample_rate: float = 10e6

    @property
    def start_time(self) -> float:
        """Start time of this minute (GPSDO seconds)."""
        return self.minute_epoch * 60

    @property
    def end_time(self) -> float:
        """End time of this minute (GPSDO seconds)."""
        return (self.minute_epoch + 1) * 60

    @property
    def minute_str(self) -> str:
        """Human-readable minute timestamp."""
        return datetime.utcfromtimestamp(self.start_time).strftime("%H:%M")


@dataclass
class CollectorStatus:
    """Current status for console output."""

    samples_processed: int = 0
    edges_a_total: int = 0
    edges_b_total: int = 0
    matched_total: int = 0
    unmatched_a_total: int = 0
    unmatched_b_total: int = 0
    minutes_queued: int = 0
    minutes_processed: int = 0
    overflow_count: int = 0
    current_minute: Optional[str] = None


class EdgeCollector:
    """
    Collects edges into minute-aligned buckets with background processing.

    Main thread:
    - Receives edges via add_edges()
    - Buffers into current MinuteBucket
    - On minute boundary: enqueues bucket for processing

    Processing thread:
    - Dequeues completed buckets
    - Calls on_bucket_complete callback
    """

    def __init__(
        self,
        sample_rate: float,
        on_bucket_complete: Callable[[MinuteBucket], None],
        on_edge: Optional[Callable[[float, float, Optional[float], Optional[float]], None]] = None,
    ):
        """
        Initialize edge collector.

        Args:
            sample_rate: Sample rate in Hz
            on_bucket_complete: Called from processing thread when minute complete
            on_edge: Called from main thread for each matched/unmatched edge
                     (gpsdo_time, delay_ns or None, ch_a_ns or None, ch_b_ns or None)
        """
        self._sample_rate = sample_rate
        self._on_bucket_complete = on_bucket_complete
        self._on_edge = on_edge

        # Current bucket (None until first edge after minute boundary)
        self._current_bucket: Optional[MinuteBucket] = None
        self._first_minute_dropped = False
        self._gpsdo_start_time: Optional[float] = None

        # Edge matching state (two-pointer merge)
        self._queue_a: list[float] = []  # Edge times in GPSDO seconds
        self._queue_b: list[float] = []

        # Stats tracking
        self._samples_processed = 0
        self._edges_a_total = 0
        self._edges_b_total = 0
        self._matched_total = 0
        self._unmatched_a_total = 0
        self._unmatched_b_total = 0
        self._minutes_processed = 0
        self._overflow_count = 0

        # Processing thread
        self._bucket_queue: queue.Queue[Optional[MinuteBucket]] = queue.Queue()
        self._stop_event = threading.Event()
        self._processing_thread = threading.Thread(
            target=self._processing_loop, daemon=True
        )
        self._processing_thread.start()

    def add_edges(
        self,
        falling_a: np.ndarray,
        falling_b: np.ndarray,
        chunk_gpsdo_time: float,
        chunk_sample_offset: int,
        pulse_freq: float,
    ) -> None:
        """
        Add edges from current chunk.

        Args:
            falling_a: Falling edge sample indices (cumulative from stream start)
            falling_b: Falling edge sample indices (cumulative from stream start)
            chunk_gpsdo_time: GPSDO time of first sample in this chunk
            chunk_sample_offset: Sample index of first sample in this chunk
            pulse_freq: Pulse frequency for edge matching threshold
        """
        # Initialize GPSDO start time from first chunk
        if self._gpsdo_start_time is None:
            self._gpsdo_start_time = chunk_gpsdo_time - (chunk_sample_offset / self._sample_rate)

        # Convert sample indices to relative times (seconds from stream start)
        # Using relative times avoids float precision loss with large GPSDO timestamps
        times_a = falling_a / self._sample_rate  # Relative to stream start
        times_b = falling_b / self._sample_rate

        self._edges_a_total += len(times_a)
        self._edges_b_total += len(times_b)

        # Add to matching queues
        self._queue_a.extend(times_a.tolist())
        self._queue_b.extend(times_b.tolist())

        # Match edges and optionally publish
        self._match_edges(pulse_freq)

        # Add to current bucket (or start new one)
        current_time = chunk_gpsdo_time
        current_minute = int(current_time) // 60

        # Handle minute boundary
        if self._current_bucket is not None:
            if current_minute > self._current_bucket.minute_epoch:
                # Minute boundary crossed - enqueue current bucket
                self._bucket_queue.put(self._current_bucket)
                self._current_bucket = None

        # Start new bucket if needed
        if self._current_bucket is None:
            if not self._first_minute_dropped:
                # Drop first incomplete minute
                self._first_minute_dropped = True
                print(f"  Dropping incomplete minute (starting at :{int(current_time) % 60:02d}s)")
            else:
                self._current_bucket = MinuteBucket(
                    minute_epoch=current_minute,
                    sample_rate=self._sample_rate,
                )

        # Add edges to current bucket
        if self._current_bucket is not None:
            # Convert bucket boundaries to relative times for comparison
            bucket_start_rel = self._current_bucket.start_time - self._gpsdo_start_time
            bucket_end_rel = self._current_bucket.end_time - self._gpsdo_start_time

            for t in times_a:
                if bucket_start_rel <= t < bucket_end_rel:
                    self._current_bucket.edges_a.append(t)
            for t in times_b:
                if bucket_start_rel <= t < bucket_end_rel:
                    self._current_bucket.edges_b.append(t)

    def _match_edges(self, pulse_freq: float) -> None:
        """Match edges using two-pointer merge algorithm."""
        if not self._queue_a or not self._queue_b:
            return

        # Matching threshold: 10% of period
        max_match_delay = 0.1 / pulse_freq
        # Safety buffer: don't process edges too close to newest data
        safety_buffer = 0.5 / pulse_freq

        newest = max(self._queue_a[-1] if self._queue_a else 0,
                     self._queue_b[-1] if self._queue_b else 0)
        safe_cutoff = newest - safety_buffer

        while self._queue_a and self._queue_b:
            a = self._queue_a[0]
            b = self._queue_b[0]

            # Don't process edges too close to newest data
            if a > safe_cutoff or b > safe_cutoff:
                break

            diff = b - a  # positive = B is later

            if abs(diff) <= max_match_delay:
                # Match
                self._queue_a.pop(0)
                self._queue_b.pop(0)
                self._matched_total += 1

                if self._on_edge:
                    delay_ns = diff * 1e9
                    # a, b are relative times; add gpsdo_start_time for absolute timestamp
                    gpsdo_time = self._gpsdo_start_time + a
                    ch_a_ns = int(a * 1e9)  # Relative ns from stream start
                    ch_b_ns = int(b * 1e9)
                    self._on_edge(gpsdo_time, delay_ns, ch_a_ns, ch_b_ns)

            elif diff > 0:
                # B is later - A missed its match
                self._queue_a.pop(0)
                self._unmatched_a_total += 1

                if self._on_edge:
                    gpsdo_time = self._gpsdo_start_time + a
                    ch_a_ns = int(a * 1e9)
                    self._on_edge(gpsdo_time, None, ch_a_ns, None)

            else:
                # A is later - B missed its match
                self._queue_b.pop(0)
                self._unmatched_b_total += 1

                if self._on_edge:
                    gpsdo_time = self._gpsdo_start_time + b
                    ch_b_ns = int(b * 1e9)
                    self._on_edge(gpsdo_time, None, None, ch_b_ns)

    def update_samples(self, samples: int) -> None:
        """Update samples processed count."""
        self._samples_processed = samples

    def set_overflow_count(self, count: int) -> None:
        """Update overflow count."""
        self._overflow_count = count

    def get_status(self) -> CollectorStatus:
        """Get current status for console output."""
        return CollectorStatus(
            samples_processed=self._samples_processed,
            edges_a_total=self._edges_a_total,
            edges_b_total=self._edges_b_total,
            matched_total=self._matched_total,
            unmatched_a_total=self._unmatched_a_total,
            unmatched_b_total=self._unmatched_b_total,
            minutes_queued=self._bucket_queue.qsize(),
            minutes_processed=self._minutes_processed,
            overflow_count=self._overflow_count,
            current_minute=self._current_bucket.minute_str if self._current_bucket else None,
        )

    def _processing_loop(self) -> None:
        """Background thread that processes completed buckets."""
        while not self._stop_event.is_set():
            try:
                bucket = self._bucket_queue.get(timeout=0.5)
                if bucket is None:
                    break
                self._on_bucket_complete(bucket)
                self._minutes_processed += 1
            except queue.Empty:
                continue

    def flush(self) -> None:
        """Flush current bucket (for clean shutdown)."""
        if self._current_bucket is not None:
            self._bucket_queue.put(self._current_bucket)
            self._current_bucket = None

    def stop(self) -> None:
        """Stop processing thread."""
        self._stop_event.set()
        self._bucket_queue.put(None)  # Sentinel to wake up thread
        self._processing_thread.join(timeout=2.0)
