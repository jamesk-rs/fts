"""
Parser for FTM CSV data from idf.py monitor logs.

Expected log format (with timestamps from ts_us.py):
    [2025-12-16 10:30:45.123456] FTM,session,status,entries,rtt_avg_ns,rtt_min_ns,rtt_max_ns,rssi_avg,rssi_min,rssi_max

Also handles:
    - Without microseconds: [2025-12-16 10:30:45]
    - idf.py --timestamps format: [10:30:45.123]
    - No timestamp prefix (uses None for timestamp)
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Number of FTM frames per burst (ESP32 FTM configuration)
FTM_BURST_SIZE = 64


@dataclass
class FTMSession:
    """Single FTM session result."""
    timestamp: Optional[datetime]  # Wall-clock time (None if no timestamp)
    session: int                   # Session number
    status: int                    # 0=success, non-zero=error
    entries: int                   # Number of FTM entries
    rtt_avg_ns: Optional[float]    # RTT average in nanoseconds
    rtt_min_ns: Optional[float]    # RTT minimum
    rtt_max_ns: Optional[float]    # RTT maximum
    rssi_avg: Optional[int]        # RSSI average
    rssi_min: Optional[int]        # RSSI minimum
    rssi_max: Optional[int]        # RSSI maximum

    @property
    def success(self) -> bool:
        """True if session was successful."""
        return self.status == 0 and self.entries > 0


# Regex patterns for timestamp extraction
# Full datetime with optional microseconds: [2025-12-16 10:30:45.123456] or [2025-12-16 10:30:45]
TIMESTAMP_FULL_RE = re.compile(
    r'^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*'
)

# Time only (idf.py --timestamps): [10:30:45.123]
TIMESTAMP_TIME_RE = re.compile(
    r'^\[(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*'
)

# FTM CSV line pattern
FTM_CSV_RE = re.compile(
    r'FTM,(\d+),(\d+),(\d+),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,\s]*)'
)


def parse_timestamp(ts_str: str, base_date: Optional[datetime] = None) -> Optional[datetime]:
    """
    Parse timestamp string to datetime.

    Args:
        ts_str: Timestamp string (various formats)
        base_date: Base date for time-only timestamps

    Returns:
        datetime object or None if parsing fails
    """
    # Try full datetime with microseconds
    for fmt in [
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
    ]:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    # Try time-only formats (need base_date)
    if base_date:
        for fmt in ['%H:%M:%S.%f', '%H:%M:%S']:
            try:
                t = datetime.strptime(ts_str, fmt)
                return base_date.replace(
                    hour=t.hour, minute=t.minute, second=t.second,
                    microsecond=t.microsecond
                )
            except ValueError:
                continue

    return None


def parse_ftm_line(line: str, base_date: Optional[datetime] = None) -> Optional[FTMSession]:
    """
    Parse a single line that may contain FTM CSV data.

    Args:
        line: Log line (may or may not have timestamp prefix)
        base_date: Base date for time-only timestamps

    Returns:
        FTMSession if line contains FTM data, None otherwise
    """
    timestamp = None
    rest = line

    # Try to extract timestamp
    match = TIMESTAMP_FULL_RE.match(line)
    if match:
        timestamp = parse_timestamp(match.group(1))
        rest = line[match.end():]
    else:
        match = TIMESTAMP_TIME_RE.match(line)
        if match:
            timestamp = parse_timestamp(match.group(1), base_date)
            rest = line[match.end():]

    # Look for FTM CSV data
    match = FTM_CSV_RE.search(rest)
    if not match:
        return None

    # Parse CSV fields
    session = int(match.group(1))
    status = int(match.group(2))
    entries = int(match.group(3))

    # Parse optional numeric fields (may be empty on failure)
    def parse_float(s: str) -> Optional[float]:
        try:
            return float(s) if s.strip() else None
        except ValueError:
            return None

    def parse_int(s: str) -> Optional[int]:
        try:
            return int(s) if s.strip() else None
        except ValueError:
            return None

    return FTMSession(
        timestamp=timestamp,
        session=session,
        status=status,
        entries=entries,
        rtt_avg_ns=parse_float(match.group(4)),
        rtt_min_ns=parse_float(match.group(5)),
        rtt_max_ns=parse_float(match.group(6)),
        rssi_avg=parse_int(match.group(7)),
        rssi_min=parse_int(match.group(8)),
        rssi_max=parse_int(match.group(9)),
    )


def parse_ftm_log(
    log_path: str | Path,
    label: Optional[str] = None,
) -> dict:
    """
    Parse FTM log file and extract all sessions.

    Args:
        log_path: Path to log file
        label: Optional label for this log (defaults to filename stem)

    Returns:
        Dict with:
            'label': str - identifier for this log
            'sessions': list[FTMSession] - all parsed sessions
            'success_count': int - number of successful sessions
            'failure_count': int - number of failed sessions
    """
    log_path = Path(log_path)
    label = label or log_path.stem

    sessions = []
    base_date = None

    with open(log_path, 'r', errors='replace') as f:
        for line in f:
            session = parse_ftm_line(line, base_date)
            if session:
                sessions.append(session)
                # Use first timestamp as base_date for time-only formats
                if session.timestamp and base_date is None:
                    base_date = session.timestamp

    success_count = sum(1 for s in sessions if s.success)
    failure_count = len(sessions) - success_count

    return {
        'label': label,
        'sessions': sessions,
        'success_count': success_count,
        'failure_count': failure_count,
    }


def compute_ftm_stats(sessions: list[FTMSession]) -> dict:
    """
    Compute aggregate statistics from FTM sessions.

    Args:
        sessions: List of FTMSession objects

    Returns:
        Dict with statistics (RTT, RSSI, success rate, etc.)
    """
    import numpy as np

    successful = [s for s in sessions if s.success]

    if not sessions:
        return {
            'count': 0,
            'success_rate': 0.0,
            'entry_success_rate': 0.0,
        }

    # Entry success rate: total entries received / total possible entries
    total_entries = sum(s.entries for s in sessions)
    max_entries = FTM_BURST_SIZE * len(sessions)
    entry_success_rate = total_entries / max_entries if max_entries > 0 else 0.0

    rtt_values = [s.rtt_avg_ns for s in successful if s.rtt_avg_ns is not None]
    rssi_values = [s.rssi_avg for s in successful if s.rssi_avg is not None]

    stats = {
        'count': len(sessions),
        'success_count': len(successful),
        'success_rate': len(successful) / len(sessions) if sessions else 0.0,
        'entry_success_rate': entry_success_rate,
        'total_entries': total_entries,
        'max_entries': max_entries,
    }

    if rtt_values:
        rtt_arr = np.array(rtt_values)
        stats['rtt_mean_ns'] = float(np.mean(rtt_arr))
        stats['rtt_std_ns'] = float(np.std(rtt_arr))
        stats['rtt_min_ns'] = float(np.min(rtt_arr))
        stats['rtt_max_ns'] = float(np.max(rtt_arr))

    if rssi_values:
        rssi_arr = np.array(rssi_values)
        stats['rssi_mean'] = float(np.mean(rssi_arr))
        stats['rssi_std'] = float(np.std(rssi_arr))
        stats['rssi_min'] = int(np.min(rssi_arr))
        stats['rssi_max'] = int(np.max(rssi_arr))

    return stats
