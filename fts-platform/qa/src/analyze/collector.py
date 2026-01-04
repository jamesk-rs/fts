"""
Edge collection with minute-aligned buckets.

Real-time edge matching with minute-based statistics:
- Edges matched immediately as they arrive (for real-time MQTT publishing)
- Delays stored in minute buckets for stats computation
- Unmatched edges tracked with grace window at minute boundaries
"""

import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import numpy as np


@dataclass
class MinuteBucket:
    """Edges collected during one complete minute."""

    minute_epoch: int  # GPS minute (unix timestamp // 60)
    edges_a: list[float] = field(default_factory=list)  # Edge times (relative seconds from stream start)
    edges_b: list[float] = field(default_factory=list)
    delays: list[float] = field(default_factory=list)  # Matched delays in seconds
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
    Real-time edge matching with minute-aligned statistics.

    Edges are matched immediately as they arrive, with on_edge callback
    for real-time MQTT publishing. Delays are accumulated in minute buckets
    for stats computation.

    Main thread:
    - Receives edges via add_edges()
    - Matches edges in real-time, calls on_edge()
    - Stores delays in current bucket
    - On minute boundary: enqueues bucket for stats processing

    Processing thread:
    - Dequeues completed buckets
    - Calls on_bucket_complete callback
    """

    def __init__(
        self,
        sample_rate: float,
        pulse_freq: float,
        on_bucket_complete: Callable[[MinuteBucket], None],
        on_edge: Optional[Callable[[float, float, Optional[float], Optional[float]], None]] = None,
    ):
        self._sample_rate = sample_rate
        self._pulse_freq = pulse_freq
        self._on_bucket_complete = on_bucket_complete
        self._on_edge = on_edge

        # Matching parameters
        self._max_match_delay = 0.1 / pulse_freq  # 10% of period
        self._safety_buffer = 0.5 / pulse_freq    # Wait for late edges
        self._grace = 1.0 / pulse_freq            # Boundary grace for unmatched

        # Edge matching queues (deque for O(1) popleft)
        self._queue_a: deque[float] = deque()
        self._queue_b: deque[float] = deque()

        # Current bucket
        self._current_bucket: Optional[MinuteBucket] = None
        self._gpsdo_start: Optional[float] = None
        self._last_completed_minute: int = -1
        self._first_minute_dropped = False

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
        Add edges from current chunk with real-time matching.
        """
        # Initialize GPSDO start time from first chunk
        if self._gpsdo_start is None:
            self._gpsdo_start = chunk_gpsdo_time - (chunk_sample_offset / self._sample_rate)

        # Convert sample indices to RELATIVE times (seconds from stream start)
        # Using relative times avoids float precision loss with large GPS timestamps
        # (GPS time ~1.77e9 would lose nanosecond precision in float64)
        times_a = falling_a / self._sample_rate
        times_b = falling_b / self._sample_rate

        self._edges_a_total += len(times_a)
        self._edges_b_total += len(times_b)

        # Add to matching queues (relative times for precision)
        self._queue_a.extend(times_a.tolist())
        self._queue_b.extend(times_b.tolist())

        # Real-time matching
        self._match_edges()

        # Add edges to buckets and handle minute transitions
        for t in times_a:
            self._add_edge_to_bucket(t, 'a')
        for t in times_b:
            self._add_edge_to_bucket(t, 'b')

    def _add_edge_to_bucket(self, rel_time: float, channel: str) -> None:
        """Add edge to appropriate bucket, handling minute transitions.

        Args:
            rel_time: Relative time (seconds from stream start)
            channel: 'a' or 'b'
        """
        # Convert to GPS time only for minute calculation
        gps_time = self._gpsdo_start + rel_time
        edge_minute = int(gps_time) // 60

        # Initialize on first edge
        if self._last_completed_minute == -1:
            self._last_completed_minute = edge_minute - 1

        # Handle bucket transition
        if self._current_bucket is not None:
            if edge_minute > self._current_bucket.minute_epoch:
                # Minute boundary crossed - finalize current bucket
                self._finalize_bucket()

        # Create new bucket if needed
        if self._current_bucket is None:
            if not self._first_minute_dropped:
                self._first_minute_dropped = True
                start_second = int(self._gpsdo_start) % 60
                print(f"  Dropping incomplete minute (starting at :{start_second:02d}s)")
                # Still need to create bucket to track edges, just won't publish it
            self._current_bucket = MinuteBucket(
                minute_epoch=edge_minute,
                sample_rate=self._sample_rate,
            )

        # Add edge to current bucket (store relative time for precision)
        if channel == 'a':
            self._current_bucket.edges_a.append(rel_time)
        else:
            self._current_bucket.edges_b.append(rel_time)

    def _match_edges(self) -> None:
        """Match edges using two-pointer algorithm with real-time publishing."""
        if not self._queue_a or not self._queue_b:
            return

        # Safety buffer: don't match edges too close to newest data
        newest = max(
            self._queue_a[-1] if self._queue_a else 0,
            self._queue_b[-1] if self._queue_b else 0
        )
        safe_cutoff = newest - self._safety_buffer

        while self._queue_a and self._queue_b:
            a = self._queue_a[0]
            b = self._queue_b[0]

            # Don't process edges too close to newest data
            if a > safe_cutoff or b > safe_cutoff:
                break

            diff = b - a  # positive = B is later

            if abs(diff) <= self._max_match_delay:
                # Match found
                self._queue_a.popleft()
                self._queue_b.popleft()
                self._matched_total += 1

                # Real-time callback (delays computed at minute end in processor)
                # a, b are relative times; convert to GPS time for timestamp
                if self._on_edge and self._gpsdo_start is not None:
                    gps_time = self._gpsdo_start + a
                    ch_a_ns = int(a * 1e9)  # Relative ns from stream start
                    ch_b_ns = int(b * 1e9)
                    self._on_edge(gps_time, diff * 1e9, ch_a_ns, ch_b_ns)

            elif diff > 0:
                # B is later - A missed its match
                self._queue_a.popleft()
                self._unmatched_a_total += 1
                if self._on_edge and self._gpsdo_start is not None:
                    gps_time = self._gpsdo_start + a
                    ch_a_ns = int(a * 1e9)
                    self._on_edge(gps_time, None, ch_a_ns, None)
            else:
                # A is later - B missed its match
                self._queue_b.popleft()
                self._unmatched_b_total += 1
                if self._on_edge and self._gpsdo_start is not None:
                    gps_time = self._gpsdo_start + b
                    ch_b_ns = int(b * 1e9)
                    self._on_edge(gps_time, None, None, ch_b_ns)

    def _finalize_bucket(self) -> None:
        """Finalize current bucket and enqueue for stats processing."""
        if self._current_bucket is None:
            return

        # Drain remaining edges in queue for this minute (bypass safety buffer)
        # Match any edge A that belongs to this minute, even if B is in next minute
        bucket_minute = self._current_bucket.minute_epoch
        # Convert bucket_end to relative time for comparison with queue values
        bucket_end_gps = (bucket_minute + 1) * 60
        bucket_end_rel = bucket_end_gps - self._gpsdo_start

        while self._queue_a and self._queue_b:
            a = self._queue_a[0]  # Relative time
            b = self._queue_b[0]

            # Stop if A edge is in the next minute (nothing left for this bucket)
            if a >= bucket_end_rel:
                break

            diff = b - a

            if abs(diff) <= self._max_match_delay:
                self._queue_a.popleft()
                self._queue_b.popleft()
                self._matched_total += 1

                if self._on_edge and self._gpsdo_start is not None:
                    gps_time = self._gpsdo_start + a
                    ch_a_ns = int(a * 1e9)
                    ch_b_ns = int(b * 1e9)
                    self._on_edge(gps_time, diff * 1e9, ch_a_ns, ch_b_ns)
            elif diff > 0:
                self._queue_a.popleft()
                self._unmatched_a_total += 1
            else:
                self._queue_b.popleft()
                self._unmatched_b_total += 1

        # Skip the first (incomplete) minute
        is_first_minute = self._last_completed_minute == int(self._gpsdo_start) // 60 - 1
        if not is_first_minute:
            self._bucket_queue.put(self._current_bucket)

        self._last_completed_minute = self._current_bucket.minute_epoch
        self._current_bucket = None

    def update_samples(self, samples: int) -> None:
        """Update samples processed count."""
        self._samples_processed = samples

    def set_overflow_count(self, count: int) -> None:
        """Update overflow count."""
        self._overflow_count = count

    def get_status(self) -> CollectorStatus:
        """Get current status for console output."""
        minute_str = None
        if self._current_bucket is not None:
            minute_str = self._current_bucket.minute_str

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
            current_minute=minute_str,
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
        self._bucket_queue.put(None)
        self._processing_thread.join(timeout=2.0)
