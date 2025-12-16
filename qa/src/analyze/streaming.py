"""
Streaming analysis for long-duration edge data.

Components:
- StreamingMatcher: Match edges between channels with outlier rejection
- StreamingStats: Online statistics with Welford's algorithm
- DelayFileWriter: Write matched delays to binary file
"""

import struct
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple
import numpy as np


# Delay file format: (float64 time_samples, float64 delay_ns) = 16 bytes
DELAY_RECORD_FORMAT = '<dd'  # little-endian: two doubles
DELAY_RECORD_SIZE = 16


@dataclass
class MatchResult:
    """Result of matching edges between channels."""
    time_samples: float  # Reference edge time in samples
    delay_ns: float      # Delay in nanoseconds (target - reference)
    rejected: bool = False  # True if outlier


class StreamingMatcher:
    """
    Match edges between reference and target channels.

    Features:
    - Handles async starts (target channel starts later)
    - Outlier rejection (delays > max_delay_ns are rejected)
    - Tracks match and reject counts
    """

    def __init__(
        self,
        sample_rate: float,
        max_delay_ns: float = 50000.0,  # 50us default (10% of 2kHz period)
    ):
        """
        Initialize matcher.

        Args:
            sample_rate: Sample rate in Hz
            max_delay_ns: Maximum allowed delay in ns (outlier threshold)
        """
        self.sample_rate = sample_rate
        self.max_delay_ns = max_delay_ns
        self._ns_per_sample = 1e9 / sample_rate

        # State for streaming
        self._ref_buffer = []   # Buffered reference edges waiting for match
        self._target_idx = 0    # Current position in target edges

        # Statistics
        self.match_count = 0
        self.reject_count = 0

    def reset(self):
        """Reset matcher state."""
        self._ref_buffer = []
        self._target_idx = 0
        self.match_count = 0
        self.reject_count = 0

    def match(
        self,
        ref_times: np.ndarray,
        target_times: np.ndarray,
    ) -> Iterator[MatchResult]:
        """
        Match reference edges to target edges.

        Simple greedy algorithm: for each reference edge, find closest target edge.
        Assumes edges are sorted by time.

        Args:
            ref_times: Reference channel edge times (in samples)
            target_times: Target channel edge times (in samples)

        Yields:
            MatchResult for each reference edge
        """
        if len(ref_times) == 0 or len(target_times) == 0:
            return

        target_idx = 0

        for ref_time in ref_times:
            # Find closest target edge
            best_idx = None
            best_dist = float('inf')

            # Search forward from current position
            for i in range(target_idx, len(target_times)):
                dist = abs(target_times[i] - ref_time)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i
                elif dist > best_dist:
                    # Past minimum - stop searching
                    break

            if best_idx is None:
                continue

            # Compute delay
            delay_samples = target_times[best_idx] - ref_time
            delay_ns = delay_samples * self._ns_per_sample

            # Check for outlier
            rejected = abs(delay_ns) > self.max_delay_ns

            if rejected:
                self.reject_count += 1
            else:
                self.match_count += 1
                # Move target index forward to avoid re-matching
                target_idx = best_idx + 1

            yield MatchResult(
                time_samples=ref_time,
                delay_ns=delay_ns,
                rejected=rejected,
            )


class StreamingStats:
    """
    Online statistics using Welford's algorithm.

    Computes mean, variance, min, max without storing all values.
    Uses reservoir sampling for approximate percentiles.
    """

    def __init__(self, reservoir_size: int = 10000):
        """
        Initialize statistics collector.

        Args:
            reservoir_size: Size of reservoir for percentile estimation
        """
        self.reservoir_size = reservoir_size
        self.reset()

    def reset(self):
        """Reset all statistics."""
        self.count = 0
        self._mean = 0.0
        self._m2 = 0.0  # Sum of squared differences from mean
        self._min = float('inf')
        self._max = float('-inf')
        self._reservoir = []
        self._reservoir_count = 0

    def update(self, value: float):
        """Update statistics with a new value."""
        self.count += 1

        # Welford's online algorithm for mean and variance
        delta = value - self._mean
        self._mean += delta / self.count
        delta2 = value - self._mean
        self._m2 += delta * delta2

        # Min/max
        if value < self._min:
            self._min = value
        if value > self._max:
            self._max = value

        # Reservoir sampling for percentiles
        self._reservoir_count += 1
        if len(self._reservoir) < self.reservoir_size:
            self._reservoir.append(value)
        else:
            # Random replacement
            import random
            j = random.randint(0, self._reservoir_count - 1)
            if j < self.reservoir_size:
                self._reservoir[j] = value

    def update_batch(self, values: np.ndarray):
        """Update statistics with a batch of values."""
        for v in values:
            self.update(v)

    @property
    def mean(self) -> float:
        """Mean of all values."""
        return self._mean if self.count > 0 else 0.0

    @property
    def variance(self) -> float:
        """Population variance."""
        return self._m2 / self.count if self.count > 1 else 0.0

    @property
    def std(self) -> float:
        """Population standard deviation."""
        return np.sqrt(self.variance)

    @property
    def min(self) -> float:
        """Minimum value."""
        return self._min if self.count > 0 else 0.0

    @property
    def max(self) -> float:
        """Maximum value."""
        return self._max if self.count > 0 else 0.0

    def percentile(self, p: float) -> float:
        """
        Approximate percentile of absolute deviation from mean.

        This measures jitter as deviation from the mean offset,
        which is the standard definition of timing jitter.

        Args:
            p: Percentile (0-100)

        Returns:
            Approximate percentile of |value - mean|
        """
        if len(self._reservoir) == 0:
            return 0.0
        # Compute absolute deviation from mean
        deviations = [abs(v - self._mean) for v in self._reservoir]
        sorted_deviations = sorted(deviations)
        idx = int(len(sorted_deviations) * p / 100.0)
        idx = min(idx, len(sorted_deviations) - 1)
        return sorted_deviations[idx]

    def summary(self) -> dict:
        """Get summary statistics as dictionary."""
        return {
            'count': self.count,
            'mean': self.mean,
            'std': self.std,
            'min': self.min,
            'max': self.max,
            'p50': self.percentile(50),
            'p95': self.percentile(95),
            'p99': self.percentile(99),
        }

    def __str__(self) -> str:
        """Human-readable summary."""
        if self.count == 0:
            return "No data"
        return (
            f"n={self.count}, mean={self.mean:.2f}, std={self.std:.2f}, "
            f"min={self.min:.2f}, max={self.max:.2f}, "
            f"p50={self.percentile(50):.2f}, p95={self.percentile(95):.2f}"
        )


class DelayFileWriter:
    """
    Write matched delays to binary file.

    Format: (float64 time_samples, float64 delay_ns) = 16 bytes per record
    """

    def __init__(self, output_path: Path):
        """
        Initialize delay file writer.

        Args:
            output_path: Path to output file
        """
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.output_path, 'ab')
        self._count = 0

    def write(self, time_samples: float, delay_ns: float):
        """Write a single delay record."""
        record = struct.pack(DELAY_RECORD_FORMAT, time_samples, delay_ns)
        self._file.write(record)
        self._count += 1

    def write_batch(self, times: np.ndarray, delays: np.ndarray):
        """Write a batch of delay records."""
        for t, d in zip(times, delays):
            self.write(t, d)

    def flush(self):
        """Flush buffered data."""
        self._file.flush()

    def close(self):
        """Close the file."""
        self._file.close()

    @property
    def count(self) -> int:
        """Number of records written."""
        return self._count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class DelayFileReader:
    """
    Read delay records from binary file.
    """

    DTYPE = np.dtype([('time', '<f8'), ('delay', '<f8')])

    def __init__(self, input_path: Path):
        """
        Initialize delay file reader.

        Args:
            input_path: Path to delay file
        """
        self.input_path = Path(input_path)

    def read_all(self) -> np.ndarray:
        """Read all delay records."""
        if not self.input_path.exists():
            return np.array([], dtype=self.DTYPE)
        return np.fromfile(self.input_path, dtype=self.DTYPE)

    def iter_batches(self, batch_size: int = 10000) -> Iterator[np.ndarray]:
        """Iterate over delays in batches."""
        if not self.input_path.exists():
            return

        with open(self.input_path, 'rb') as f:
            while True:
                data = f.read(batch_size * DELAY_RECORD_SIZE)
                if not data:
                    break
                n_records = len(data) // DELAY_RECORD_SIZE
                batch = np.frombuffer(data[:n_records * DELAY_RECORD_SIZE], dtype=self.DTYPE)
                yield batch

    def count(self) -> int:
        """Number of records in file."""
        if not self.input_path.exists():
            return 0
        return self.input_path.stat().st_size // DELAY_RECORD_SIZE
