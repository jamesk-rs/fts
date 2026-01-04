"""
Edge collection with minute-aligned buckets.

Simplified architecture:
- EdgeBuffer: stores edges with GPS timestamps
- MinuteAggregator: matches edges when minute is complete
- EdgeCollector: coordinates buffering and aggregation

Processing is offloaded to a background thread.
"""

import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import numpy as np


@dataclass
class MinuteBucket:
    """Edges collected during one complete minute."""

    minute_epoch: int  # GPS minute (unix timestamp // 60)
    edges_a: list[float] = field(default_factory=list)  # Edge GPS times
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


class EdgeBuffer:
    """
    Simple edge storage with minute-based retrieval.

    Stores edges as (gps_time, channel) and allows extracting
    all edges for a completed minute.
    """

    def __init__(self):
        # Separate lists for efficiency (avoid channel checks during iteration)
        self._edges_a: list[float] = []
        self._edges_b: list[float] = []

    def add_edges(self, gps_times_a: np.ndarray, gps_times_b: np.ndarray) -> None:
        """Store edges with their GPS timestamps."""
        self._edges_a.extend(gps_times_a.tolist())
        self._edges_b.extend(gps_times_b.tolist())

    def pop_minute(self, minute_epoch: int) -> tuple[list[float], list[float]]:
        """Extract and remove all edges belonging to a specific minute."""
        start = minute_epoch * 60
        end = start + 60

        # Extract edges in this minute
        edges_a = [t for t in self._edges_a if start <= t < end]
        edges_b = [t for t in self._edges_b if start <= t < end]

        # Remove extracted edges (keep edges outside this minute)
        self._edges_a = [t for t in self._edges_a if not (start <= t < end)]
        self._edges_b = [t for t in self._edges_b if not (start <= t < end)]

        return edges_a, edges_b

    def clear_before(self, gps_time: float) -> None:
        """Remove all edges before a given time (cleanup)."""
        self._edges_a = [t for t in self._edges_a if t >= gps_time]
        self._edges_b = [t for t in self._edges_b if t >= gps_time]


class MinuteAggregator:
    """
    Match edges and compute stats for completed minutes.

    Uses two-pointer algorithm on sorted edge lists.
    Only counts unmatched edges in the "core" region (not near boundaries).
    """

    def __init__(self, pulse_freq: float):
        self._pulse_freq = pulse_freq
        self._max_delay = 0.1 / pulse_freq  # 10% of period for matching
        self._grace = 1.0 / pulse_freq       # 1 period grace at boundaries

    def process_minute(
        self, minute_epoch: int, edges_a: list[float], edges_b: list[float]
    ) -> tuple[MinuteBucket, int, int]:
        """
        Match edges and create bucket with delays.

        Returns:
            (bucket, unmatched_a_core, unmatched_b_core)

        Unmatched counts only include edges in the "core" region
        (not within 1 period of minute boundaries) to avoid false alarms.
        """
        start = minute_epoch * 60
        end = start + 60
        core_start = start + self._grace
        core_end = end - self._grace

        # Sort both lists
        edges_a = sorted(edges_a)
        edges_b = sorted(edges_b)

        # Two-pointer matching
        delays = []
        matched_a = set()
        matched_b = set()
        i, j = 0, 0

        while i < len(edges_a) and j < len(edges_b):
            diff = edges_b[j] - edges_a[i]
            if abs(diff) <= self._max_delay:
                delays.append(diff)
                matched_a.add(i)
                matched_b.add(j)
                i += 1
                j += 1
            elif diff > 0:
                # B is later - A has no match
                i += 1
            else:
                # A is later - B has no match
                j += 1

        # Count unmatched in core region only (avoid boundary false alarms)
        unmatched_a = sum(
            1 for idx, t in enumerate(edges_a)
            if idx not in matched_a and core_start <= t < core_end
        )
        unmatched_b = sum(
            1 for idx, t in enumerate(edges_b)
            if idx not in matched_b and core_start <= t < core_end
        )

        bucket = MinuteBucket(
            minute_epoch=minute_epoch,
            edges_a=edges_a,
            edges_b=edges_b,
            delays=delays,
        )
        return bucket, unmatched_a, unmatched_b


class EdgeCollector:
    """
    Coordinates edge buffering and minute-based aggregation.

    Main thread:
    - Receives edges via add_edges()
    - Buffers in EdgeBuffer
    - On minute completion: extracts edges, runs aggregation, enqueues result

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
        """
        Initialize edge collector.

        Args:
            sample_rate: Sample rate in Hz
            pulse_freq: Pulse frequency in Hz (for matching threshold)
            on_bucket_complete: Called from processing thread when minute complete
            on_edge: Optional callback for batched edge publishing at minute end
                     (gpsdo_time, delay_ns or None, ch_a_ns or None, ch_b_ns or None)
        """
        self._sample_rate = sample_rate
        self._pulse_freq = pulse_freq
        self._on_bucket_complete = on_bucket_complete
        self._on_edge = on_edge

        # Components
        self._buffer = EdgeBuffer()
        self._aggregator = MinuteAggregator(pulse_freq)

        # State
        self._gpsdo_start: Optional[float] = None
        self._last_completed_minute: int = -1
        self._first_minute_dropped = False
        self._current_minute_epoch: Optional[int] = None

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
        pulse_freq: float,  # Kept for API compatibility, but we use self._pulse_freq
    ) -> None:
        """
        Add edges from current chunk.

        Args:
            falling_a: Falling edge sample indices (cumulative from stream start)
            falling_b: Falling edge sample indices (cumulative from stream start)
            chunk_gpsdo_time: GPSDO time of first sample in this chunk
            chunk_sample_offset: Sample index of first sample in this chunk
            pulse_freq: Pulse frequency (unused, kept for compatibility)
        """
        # Initialize GPSDO start time from first chunk
        if self._gpsdo_start is None:
            self._gpsdo_start = chunk_gpsdo_time - (chunk_sample_offset / self._sample_rate)

        # Convert sample indices to GPS times
        gps_a = self._gpsdo_start + falling_a / self._sample_rate
        gps_b = self._gpsdo_start + falling_b / self._sample_rate

        self._edges_a_total += len(gps_a)
        self._edges_b_total += len(gps_b)

        # Buffer edges
        self._buffer.add_edges(gps_a, gps_b)

        # Determine current minute from latest edge
        if len(gps_a) > 0 or len(gps_b) > 0:
            latest_gps = max(
                gps_a[-1] if len(gps_a) > 0 else 0,
                gps_b[-1] if len(gps_b) > 0 else 0
            )
            current_minute = int(latest_gps) // 60
            self._current_minute_epoch = current_minute

            # Process any completed minutes (all minutes before current)
            while self._last_completed_minute < current_minute - 1:
                minute_to_process = self._last_completed_minute + 1

                if not self._first_minute_dropped:
                    # Drop first incomplete minute
                    self._first_minute_dropped = True
                    start_second = int(self._gpsdo_start) % 60
                    print(f"  Dropping incomplete minute (starting at :{start_second:02d}s)")
                else:
                    self._complete_minute(minute_to_process)

                self._last_completed_minute = minute_to_process

    def _complete_minute(self, minute_epoch: int) -> None:
        """Finalize a completed minute."""
        # Extract edges for this minute
        edges_a, edges_b = self._buffer.pop_minute(minute_epoch)

        # Match and aggregate
        bucket, unmatched_a, unmatched_b = self._aggregator.process_minute(
            minute_epoch, edges_a, edges_b
        )

        # Update stats
        self._matched_total += len(bucket.delays)
        self._unmatched_a_total += unmatched_a
        self._unmatched_b_total += unmatched_b

        # Emit per-edge events if callback provided (batched)
        if self._on_edge:
            for i, delay in enumerate(bucket.delays):
                # Use approximate GPS time (edge_a time)
                gps_time = edges_a[i] if i < len(edges_a) else minute_epoch * 60
                self._on_edge(gps_time, delay * 1e9, None, None)

        # Enqueue for background processing
        self._bucket_queue.put(bucket)

    def update_samples(self, samples: int) -> None:
        """Update samples processed count."""
        self._samples_processed = samples

    def set_overflow_count(self, count: int) -> None:
        """Update overflow count."""
        self._overflow_count = count

    def get_status(self) -> CollectorStatus:
        """Get current status for console output."""
        minute_str = None
        if self._current_minute_epoch is not None:
            minute_str = datetime.utcfromtimestamp(
                self._current_minute_epoch * 60
            ).strftime("%H:%M")

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
        """Flush current minute (for clean shutdown)."""
        if self._current_minute_epoch is not None:
            # Process current (possibly incomplete) minute
            edges_a, edges_b = self._buffer.pop_minute(self._current_minute_epoch)
            if edges_a or edges_b:
                bucket, _, _ = self._aggregator.process_minute(
                    self._current_minute_epoch, edges_a, edges_b
                )
                self._matched_total += len(bucket.delays)
                self._bucket_queue.put(bucket)

    def stop(self) -> None:
        """Stop processing thread."""
        self._stop_event.set()
        self._bucket_queue.put(None)  # Sentinel to wake up thread
        self._processing_thread.join(timeout=2.0)
