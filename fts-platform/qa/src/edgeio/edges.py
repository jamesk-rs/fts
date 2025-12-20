"""
Edge file I/O for streaming capture and analysis.

File format:
- Binary file per channel: edges_ch{N}.bin
- Each record: (float64 time_samples, uint8 edge_type) = 16 bytes (padded)
  - time_samples: Edge time in samples (sub-sample precision)
  - edge_type: 0 = rising, 1 = falling
- Metadata file: edges_meta.json

Edge type constants:
- EDGE_RISING = 0
- EDGE_FALLING = 1
"""

import json
import struct
from pathlib import Path
from datetime import datetime
from typing import Iterator, Optional
import numpy as np

# Edge type constants
EDGE_RISING = 0
EDGE_FALLING = 1

# Record format: float64 time + uint8 type + 7 bytes padding = 16 bytes
RECORD_FORMAT = '<dB7x'  # little-endian: double, unsigned char, 7 padding bytes
RECORD_SIZE = 16


def write_metadata(
    output_dir: Path,
    sample_rate: float,
    threshold: float,
    channel_count: int,
    pulse_freq: Optional[float] = None,
    **extra_fields,
) -> Path:
    """
    Write metadata JSON file for edge capture.

    Args:
        output_dir: Directory containing edge files
        sample_rate: Sample rate in Hz
        threshold: Threshold used for edge detection
        channel_count: Number of channels
        pulse_freq: Expected pulse frequency in Hz (optional)
        **extra_fields: Additional metadata fields

    Returns:
        Path to metadata file
    """
    meta = {
        'version': 1,
        'sample_rate': sample_rate,
        'threshold': threshold,
        'channel_count': channel_count,
        'start_time': datetime.now().isoformat(),
        'record_format': 'float64_time + uint8_type + 7_padding',
        'record_size': RECORD_SIZE,
    }
    if pulse_freq is not None:
        meta['pulse_freq'] = pulse_freq
    meta.update(extra_fields)

    meta_path = output_dir / 'edges_meta.json'
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    return meta_path


def read_metadata(input_dir: Path) -> dict:
    """
    Read metadata JSON file.

    Args:
        input_dir: Directory containing edge files

    Returns:
        Metadata dictionary
    """
    meta_path = input_dir / 'edges_meta.json'
    with open(meta_path, 'r') as f:
        return json.load(f)


class EdgeFileWriter:
    """
    Append-only binary writer for edge data.

    Usage:
        writer = EdgeFileWriter(output_dir, channel=0)
        writer.write_edges(rising_times, EDGE_RISING)
        writer.write_edges(falling_times, EDGE_FALLING)
        writer.close()
    """

    def __init__(self, output_dir: Path, channel: int):
        """
        Initialize edge file writer.

        Args:
            output_dir: Directory to write edge files
            channel: Channel number (0, 1, ...)
        """
        self.output_dir = Path(output_dir)
        self.channel = channel
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.file_path = self.output_dir / f'edges_ch{channel}.bin'
        self._file = open(self.file_path, 'ab')  # Append binary mode
        self._count = 0
        self._last_time = -1.0  # Track last written time for monotonicity check

    def write_edges(self, times: np.ndarray, edge_type: int) -> int:
        """
        Write edge times to file.

        Args:
            times: Array of edge times (in samples, float64)
            edge_type: EDGE_RISING (0) or EDGE_FALLING (1)

        Returns:
            Number of edges written

        Raises:
            ValueError: If timestamps are not monotonically increasing
        """
        for t in times:
            if t < self._last_time:
                raise ValueError(
                    f"Edge timestamp went backwards: {t} < {self._last_time} "
                    f"(ch{self.channel}, edge #{self._count}). "
                    f"This indicates data corruption - check if edge files were cleared before capture."
                )
            record = struct.pack(RECORD_FORMAT, float(t), edge_type)
            self._file.write(record)
            self._last_time = t
            self._count += 1
        return len(times)

    def write_edge(self, time: float, edge_type: int):
        """Write a single edge.

        Raises:
            ValueError: If timestamp is less than the last written timestamp
        """
        if time < self._last_time:
            raise ValueError(
                f"Edge timestamp went backwards: {time} < {self._last_time} "
                f"(ch{self.channel}, edge #{self._count}). "
                f"This indicates data corruption - check if edge files were cleared before capture."
            )
        record = struct.pack(RECORD_FORMAT, time, edge_type)
        self._file.write(record)
        self._last_time = time
        self._count += 1

    def flush(self):
        """Flush buffered data to disk."""
        self._file.flush()

    def close(self):
        """Close the file."""
        self._file.close()

    @property
    def edge_count(self) -> int:
        """Number of edges written."""
        return self._count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class EdgeFileReader:
    """
    Memory-efficient reader for edge files with live tailing support.

    Usage:
        reader = EdgeFileReader(input_dir, channel=0)
        for batch in reader.iter_batches(batch_size=10000):
            # batch is array of (time, type) records
            times = batch['time']
            types = batch['type']

        # For live tailing:
        for batch in reader.tail(batch_size=10000, poll_interval=0.1):
            # Yields new batches as they become available
    """

    # Numpy dtype for records
    DTYPE = np.dtype([('time', '<f8'), ('type', '<u1'), ('_pad', '<u1', 7)])

    def __init__(self, input_dir: Path, channel: int):
        """
        Initialize edge file reader.

        Args:
            input_dir: Directory containing edge files
            channel: Channel number (0, 1, ...)
        """
        self.input_dir = Path(input_dir)
        self.channel = channel
        self.file_path = self.input_dir / f'edges_ch{channel}.bin'
        self._position = 0  # Current file position in bytes

    def _file_size(self) -> int:
        """Get current file size."""
        return self.file_path.stat().st_size if self.file_path.exists() else 0

    def edge_count(self) -> int:
        """Total number of edges in file."""
        return self._file_size() // RECORD_SIZE

    def read_all(self) -> np.ndarray:
        """
        Read all edges from file.

        Returns:
            Structured array with 'time' and 'type' fields
        """
        if not self.file_path.exists():
            return np.array([], dtype=self.DTYPE)
        return np.fromfile(self.file_path, dtype=self.DTYPE)

    def iter_batches(
        self,
        batch_size: int = 10000,
        start_edge: int = 0,
    ) -> Iterator[np.ndarray]:
        """
        Iterate over edges in batches (memory-efficient).

        Args:
            batch_size: Number of edges per batch
            start_edge: Starting edge index (0-based)

        Yields:
            Structured arrays with 'time' and 'type' fields
        """
        if not self.file_path.exists():
            return

        with open(self.file_path, 'rb') as f:
            # Seek to start position
            f.seek(start_edge * RECORD_SIZE)

            while True:
                data = f.read(batch_size * RECORD_SIZE)
                if not data:
                    break

                # Parse records
                n_records = len(data) // RECORD_SIZE
                batch = np.frombuffer(data[:n_records * RECORD_SIZE], dtype=self.DTYPE)
                yield batch

    def tail(
        self,
        batch_size: int = 10000,
        poll_interval: float = 0.1,
        timeout: Optional[float] = None,
    ) -> Iterator[np.ndarray]:
        """
        Live tail the edge file, yielding new batches as they arrive.

        Args:
            batch_size: Number of edges per batch
            poll_interval: Seconds between polls for new data
            timeout: Stop after this many seconds (None = forever)

        Yields:
            Structured arrays with 'time' and 'type' fields
        """
        import time

        start_time = time.time()
        position = 0

        while True:
            # Check timeout
            if timeout is not None and time.time() - start_time > timeout:
                break

            # Check file size
            file_size = self._file_size()
            if file_size <= position:
                # No new data - wait and retry
                time.sleep(poll_interval)
                continue

            # Read new data
            with open(self.file_path, 'rb') as f:
                f.seek(position)
                # Read up to batch_size records
                bytes_to_read = min(batch_size * RECORD_SIZE, file_size - position)
                data = f.read(bytes_to_read)

            if data:
                n_records = len(data) // RECORD_SIZE
                actual_bytes = n_records * RECORD_SIZE
                batch = np.frombuffer(data[:actual_bytes], dtype=self.DTYPE)
                position += actual_bytes
                yield batch

    def read_range(self, start_edge: int, end_edge: int) -> np.ndarray:
        """
        Read a range of edges.

        Args:
            start_edge: Starting edge index (inclusive)
            end_edge: Ending edge index (exclusive)

        Returns:
            Structured array with 'time' and 'type' fields
        """
        if not self.file_path.exists():
            return np.array([], dtype=self.DTYPE)

        n_edges = end_edge - start_edge
        if n_edges <= 0:
            return np.array([], dtype=self.DTYPE)

        with open(self.file_path, 'rb') as f:
            f.seek(start_edge * RECORD_SIZE)
            data = f.read(n_edges * RECORD_SIZE)

        n_records = len(data) // RECORD_SIZE
        return np.frombuffer(data[:n_records * RECORD_SIZE], dtype=self.DTYPE)


# =============================================================================
# V2 Format: Variable-length records with edge points for deferred regression
# =============================================================================
#
# Format per edge:
#   trigger_idx: int64 (8 bytes) - sample index where threshold crossed
#   edge_type: uint8 (1 byte) - 0=rising, 1=falling
#   n_points: uint8 (1 byte) - number of edge points (typically 3-8)
#   peak_val: float32 (4 bytes) - peak amplitude (for computing 20/50/80% levels)
#   points: n_points × (int16 offset, float32 value) = n_points × 6 bytes
#
# Header: 14 bytes, then n_points × 6 bytes
# Typical edge: 14 + 5×6 = 44 bytes

V2_HEADER_FORMAT = '<qBBf'  # int64 trigger, uint8 type, uint8 n_points, float32 peak
V2_HEADER_SIZE = 14
V2_POINT_FORMAT = '<hf'  # int16 offset, float32 value
V2_POINT_SIZE = 6


def write_metadata_v2(
    output_dir: Path,
    sample_rate: float,
    threshold: float,
    channel_count: int,
    pulse_freq: Optional[float] = None,
    low_pct: float = 0.2,
    high_pct: float = 0.8,
    **extra_fields,
) -> Path:
    """
    Write V2 metadata JSON file for edge capture with deferred regression.

    Args:
        output_dir: Directory containing edge files
        sample_rate: Sample rate in Hz
        threshold: Threshold used for trigger detection
        channel_count: Number of channels
        pulse_freq: Expected pulse frequency in Hz (optional)
        low_pct: Low percentage for edge point collection (default 0.2)
        high_pct: High percentage for edge point collection (default 0.8)
        **extra_fields: Additional metadata fields

    Returns:
        Path to metadata file
    """
    meta = {
        'version': 2,
        'sample_rate': sample_rate,
        'threshold': threshold,
        'channel_count': channel_count,
        'start_time': datetime.now().isoformat(),
        'format': 'v2_variable_length',
        'header_size': V2_HEADER_SIZE,
        'point_size': V2_POINT_SIZE,
        'low_pct': low_pct,
        'high_pct': high_pct,
    }
    if pulse_freq is not None:
        meta['pulse_freq'] = pulse_freq
    meta.update(extra_fields)

    meta_path = output_dir / 'edges_meta.json'
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)
    return meta_path


class EdgeFileWriterV2:
    """
    Variable-length binary writer for edge data with regression points.

    Stores trigger index, peak value, and edge slope points for deferred
    linear regression during analysis phase.

    Usage:
        writer = EdgeFileWriterV2(output_dir, channel=0)
        writer.write_edge(trigger_idx, edge_type, peak_val, points_x, points_y)
        writer.close()
    """

    def __init__(self, output_dir: Path, channel: int):
        """
        Initialize V2 edge file writer.

        Args:
            output_dir: Directory to write edge files
            channel: Channel number (0, 1, ...)
        """
        self.output_dir = Path(output_dir)
        self.channel = channel
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.file_path = self.output_dir / f'edges_v2_ch{channel}.bin'
        self._file = open(self.file_path, 'ab')  # Append binary mode
        self._count = 0

    def write_edge(
        self,
        trigger_idx: int,
        edge_type: int,
        peak_val: float,
        points_x: np.ndarray,
        points_y: np.ndarray,
    ) -> None:
        """
        Write a single edge with its regression points.

        Args:
            trigger_idx: Sample index of threshold crossing
            edge_type: EDGE_RISING (0) or EDGE_FALLING (1)
            peak_val: Peak amplitude value
            points_x: Array of sample indices (int) for edge points
            points_y: Array of amplitude values (float) for edge points
        """
        n_points = len(points_x)
        if n_points > 255:
            n_points = 255
            points_x = points_x[:255]
            points_y = points_y[:255]

        # Write header
        header = struct.pack(V2_HEADER_FORMAT, trigger_idx, edge_type, n_points, peak_val)
        self._file.write(header)

        # Write points as (offset from trigger, value) pairs
        # Using int16 offset to save space (±32767 samples from trigger is plenty)
        for i in range(n_points):
            offset = int(points_x[i]) - trigger_idx
            offset = max(-32768, min(32767, offset))  # Clamp to int16 range
            point = struct.pack(V2_POINT_FORMAT, offset, float(points_y[i]))
            self._file.write(point)

        self._count += 1

    def write_edges_batch(
        self,
        trigger_indices: np.ndarray,
        edge_types: np.ndarray,
        peak_vals: np.ndarray,
        points_x_list: list,
        points_y_list: list,
    ) -> int:
        """
        Write multiple edges at once.

        Args:
            trigger_indices: Array of trigger sample indices
            edge_types: Array of edge types (0=rising, 1=falling)
            peak_vals: Array of peak values
            points_x_list: List of point x arrays (one per edge)
            points_y_list: List of point y arrays (one per edge)

        Returns:
            Number of edges written
        """
        for i in range(len(trigger_indices)):
            self.write_edge(
                trigger_indices[i],
                edge_types[i],
                peak_vals[i],
                points_x_list[i],
                points_y_list[i],
            )
        return len(trigger_indices)

    def flush(self):
        """Flush buffered data to disk."""
        self._file.flush()

    def close(self):
        """Close the file."""
        self._file.close()

    @property
    def edge_count(self) -> int:
        """Number of edges written."""
        return self._count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class EdgeFileReaderV2:
    """
    Reader for V2 variable-length edge files.

    Usage:
        reader = EdgeFileReaderV2(input_dir, channel=0)
        for edge in reader.iter_edges():
            # edge is dict with trigger_idx, edge_type, peak_val, points_x, points_y
            ...
    """

    def __init__(self, input_dir: Path, channel: int):
        """
        Initialize V2 edge file reader.

        Args:
            input_dir: Directory containing edge files
            channel: Channel number (0, 1, ...)
        """
        self.input_dir = Path(input_dir)
        self.channel = channel
        self.file_path = self.input_dir / f'edges_v2_ch{channel}.bin'

    def iter_edges(self) -> Iterator[dict]:
        """
        Iterate over all edges in the file.

        Yields:
            Dict with keys: trigger_idx, edge_type, peak_val, points_x, points_y
        """
        if not self.file_path.exists():
            return

        with open(self.file_path, 'rb') as f:
            while True:
                # Read header
                header_data = f.read(V2_HEADER_SIZE)
                if len(header_data) < V2_HEADER_SIZE:
                    break

                trigger_idx, edge_type, n_points, peak_val = struct.unpack(
                    V2_HEADER_FORMAT, header_data
                )

                # Read points
                points_data = f.read(n_points * V2_POINT_SIZE)
                if len(points_data) < n_points * V2_POINT_SIZE:
                    break

                points_x = np.zeros(n_points, dtype=np.int64)
                points_y = np.zeros(n_points, dtype=np.float32)

                for i in range(n_points):
                    offset_bytes = points_data[i * V2_POINT_SIZE : i * V2_POINT_SIZE + 2]
                    value_bytes = points_data[i * V2_POINT_SIZE + 2 : (i + 1) * V2_POINT_SIZE]
                    offset = struct.unpack('<h', offset_bytes)[0]
                    value = struct.unpack('<f', value_bytes)[0]
                    points_x[i] = trigger_idx + offset
                    points_y[i] = value

                yield {
                    'trigger_idx': trigger_idx,
                    'edge_type': edge_type,
                    'peak_val': peak_val,
                    'points_x': points_x,
                    'points_y': points_y,
                }

    def read_all(self) -> list:
        """
        Read all edges into a list.

        Returns:
            List of edge dicts
        """
        return list(self.iter_edges())

    def count_edges(self) -> int:
        """
        Count total edges in file (requires full scan due to variable length).

        Returns:
            Number of edges
        """
        count = 0
        for _ in self.iter_edges():
            count += 1
        return count
