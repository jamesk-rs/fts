"""
Statistical analysis of jitter measurements.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class JitterStats:
    """Container for jitter statistics."""
    count: int
    mean_ns: float
    std_ns: float
    min_ns: float
    max_ns: float
    p50_ns: float
    p95_ns: float
    p99_ns: float
    p999_ns: float  # 99.9th percentile
    phase_mean_deg: Optional[float] = None
    phase_std_deg: Optional[float] = None

    def __str__(self) -> str:
        lines = [
            f"Jitter Statistics (n={self.count})",
            f"  Mean:   {self.mean_ns:+.3f} ns",
            f"  Std:    {self.std_ns:.3f} ns",
            f"  Min:    {self.min_ns:+.3f} ns",
            f"  Max:    {self.max_ns:+.3f} ns",
            f"  P50:    ±{self.p50_ns:.3f} ns",
            f"  P95:    ±{self.p95_ns:.3f} ns",
            f"  P99:    ±{self.p99_ns:.3f} ns",
            f"  P99.9:  ±{self.p999_ns:.3f} ns",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'count': self.count,
            'mean_ns': self.mean_ns,
            'std_ns': self.std_ns,
            'min_ns': self.min_ns,
            'max_ns': self.max_ns,
            'p50_ns': self.p50_ns,
            'p95_ns': self.p95_ns,
            'p99_ns': self.p99_ns,
            'p999_ns': self.p999_ns,
            'phase_mean_deg': self.phase_mean_deg,
            'phase_std_deg': self.phase_std_deg,
        }


def compute_stats(
    delays_seconds: np.ndarray,
    pulse_freq: Optional[float] = None,
) -> JitterStats:
    """
    Compute jitter statistics from delay measurements.

    Args:
        delays_seconds: Array of time delays in seconds
        pulse_freq: Optional pulse frequency for phase calculations

    Returns:
        JitterStats dataclass with all statistics
    """
    delays_ns = delays_seconds * 1e9
    mean_ns = float(np.mean(delays_ns))

    # Compute percentiles on absolute deviation from mean (true jitter)
    deviation_ns = np.abs(delays_ns - mean_ns)

    phase_mean = None
    phase_std = None
    if pulse_freq is not None:
        phase_deg = delays_seconds * pulse_freq * 360.0
        phase_mean = float(np.mean(phase_deg))
        phase_std = float(np.std(phase_deg))

    return JitterStats(
        count=len(delays_ns),
        mean_ns=mean_ns,
        std_ns=float(np.std(delays_ns)),
        min_ns=float(np.min(delays_ns)),
        max_ns=float(np.max(delays_ns)),
        p50_ns=float(np.percentile(deviation_ns, 50)),
        p95_ns=float(np.percentile(deviation_ns, 95)),
        p99_ns=float(np.percentile(deviation_ns, 99)),
        p999_ns=float(np.percentile(deviation_ns, 99.9)),
        phase_mean_deg=phase_mean,
        phase_std_deg=phase_std,
    )


def compute_running_stats(
    delays_seconds: np.ndarray,
    window_size: int = 1000,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute running mean and std over a sliding window.

    Useful for visualizing jitter drift over time.

    Args:
        delays_seconds: Array of time delays in seconds
        window_size: Number of samples per window

    Returns:
        Tuple of (running_mean_ns, running_std_ns)
    """
    delays_ns = delays_seconds * 1e9
    n = len(delays_ns)

    if n < window_size:
        return np.array([np.mean(delays_ns)]), np.array([np.std(delays_ns)])

    n_windows = n // window_size
    means = np.zeros(n_windows)
    stds = np.zeros(n_windows)

    for i in range(n_windows):
        start = i * window_size
        end = start + window_size
        window = delays_ns[start:end]
        means[i] = np.mean(window)
        stds[i] = np.std(window)

    return means, stds


@dataclass
class PeriodStats:
    """Container for period/frequency statistics."""
    count: int
    mean_us: float      # Mean period in microseconds
    std_us: float       # Std of period
    min_us: float       # Min period in microseconds
    max_us: float       # Max period in microseconds
    freq_hz: float      # Derived frequency
    freq_ppm_error: float  # Error from nominal in PPM

    def __str__(self) -> str:
        return (
            f"Period: {self.mean_us:.3f} ± {self.std_us:.3f} µs "
            f"[{self.min_us:.3f}, {self.max_us:.3f}] "
            f"({self.freq_hz:.6f} Hz, {self.freq_ppm_error:+.1f} ppm)"
        )


def compute_periods(
    edge_times: np.ndarray,
    sample_rate: float,
) -> np.ndarray:
    """
    Compute periods between consecutive edges.

    Args:
        edge_times: Edge times in samples
        sample_rate: Sample rate in Hz

    Returns:
        Array of periods in seconds
    """
    if len(edge_times) < 2:
        return np.array([])
    return np.diff(edge_times) / sample_rate


def compute_period_stats(
    periods_seconds: np.ndarray,
    nominal_freq: float = 2000.0,
) -> PeriodStats:
    """
    Compute period statistics.

    Args:
        periods_seconds: Array of periods in seconds
        nominal_freq: Expected frequency in Hz

    Returns:
        PeriodStats dataclass
    """
    periods_us = periods_seconds * 1e6
    mean_period = np.mean(periods_seconds)
    measured_freq = 1.0 / mean_period
    nominal_period = 1.0 / nominal_freq
    freq_error_ppm = (mean_period - nominal_period) / nominal_period * 1e6

    return PeriodStats(
        count=len(periods_seconds),
        mean_us=float(np.mean(periods_us)),
        std_us=float(np.std(periods_us)),
        min_us=float(np.min(periods_us)),
        max_us=float(np.max(periods_us)),
        freq_hz=float(measured_freq),
        freq_ppm_error=float(-freq_error_ppm),  # Negative because longer period = lower freq
    )


def compute_frequency_skew(
    periods_a: np.ndarray,
    periods_b: np.ndarray,
) -> tuple[float, float]:
    """
    Compute frequency skew between two channels.

    Args:
        periods_a: Periods from channel A in seconds
        periods_b: Periods from channel B in seconds

    Returns:
        Tuple of (skew_ppm, skew_ns_per_second)
        - skew_ppm: Frequency difference in PPM (B relative to A)
        - skew_ns_per_second: Drift rate in nanoseconds per second
    """
    n = min(len(periods_a), len(periods_b))
    if n == 0:
        return 0.0, 0.0

    mean_a = np.mean(periods_a[:n])
    mean_b = np.mean(periods_b[:n])

    # Skew in PPM (positive = B is faster)
    skew_ppm = (mean_a - mean_b) / mean_a * 1e6

    # Drift rate: how much delay accumulates per second
    # If B is faster by X ppm, delay decreases by X ns per second
    skew_ns_per_sec = skew_ppm * 1000  # 1 ppm = 1 ns per ms = 1000 ns per s

    return float(skew_ppm), float(skew_ns_per_sec)
