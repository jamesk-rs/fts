"""
Jitter and delay calculations between channels.
"""

import numpy as np


def compute_delays(
    times_a: np.ndarray,
    times_b: np.ndarray,
    sample_rate: float,
) -> np.ndarray:
    """
    Compute time delays between corresponding edges on channels A and B.

    Assumes edges are already matched 1:1 (same number of edges, in order).

    Args:
        times_a: Edge times on channel A (in samples, float64)
        times_b: Edge times on channel B (in samples, float64)
        sample_rate: Sample rate in Hz (e.g., 10e6 for 10 MSps)

    Returns:
        Array of delays in seconds (B - A), positive means B is later
    """
    n = min(len(times_a), len(times_b))
    delays_samples = times_b[:n] - times_a[:n]
    return delays_samples / sample_rate


def compute_phase_error(
    delays: np.ndarray,
    pulse_freq: float,
) -> np.ndarray:
    """
    Convert delays to phase error at the pulse frequency.

    Args:
        delays: Time delays in seconds
        pulse_freq: Pulse frequency in Hz (e.g., 2000 for 2 kHz)

    Returns:
        Phase errors in degrees
    """
    return delays * pulse_freq * 360.0


class MatchResult:
    """Result of edge matching between two channels."""

    def __init__(
        self,
        matched_a: np.ndarray,
        matched_b: np.ndarray,
        delays: np.ndarray,
        total_a: int,
        total_b: int,
        unmatched_a_times: np.ndarray = None,
        unmatched_b_times: np.ndarray = None,
    ):
        self.matched_a = matched_a
        self.matched_b = matched_b
        self.delays = delays
        self.total_a = total_a
        self.total_b = total_b
        self.matched_count = len(matched_a)
        # Arrays of unmatched edge times
        self.unmatched_a_times = unmatched_a_times if unmatched_a_times is not None else np.array([])
        self.unmatched_b_times = unmatched_b_times if unmatched_b_times is not None else np.array([])
        # Counts for backwards compatibility
        self.unmatched_a = len(self.unmatched_a_times)
        self.unmatched_b = len(self.unmatched_b_times)


def match_edges(
    times_a: np.ndarray,
    times_b: np.ndarray,
    sample_rate: float,
    pulse_freq: float = 2000.0,
    max_delay_seconds: float = None,
) -> MatchResult:
    """
    Match edges between channels based on timing proximity.

    Handles cases where channels have different numbers of detected edges
    due to noise or missed detections. Uses closest-match algorithm to
    correctly handle dropped edges.

    Args:
        times_a: Edge times on channel A (in samples)
        times_b: Edge times on channel B (in samples)
        sample_rate: Sample rate in Hz
        pulse_freq: Pulse frequency in Hz (used to compute max delay if not specified)
        max_delay_seconds: Maximum allowed delay between matched edges.
                          If None, uses 10% of pulse period.

    Returns:
        MatchResult with matched edges, delays, and per-channel statistics
    """
    # Use 10% of period as max delay (same as StreamingMatcher)
    if max_delay_seconds is None:
        max_delay_seconds = 0.1 / pulse_freq

    max_delay_samples = max_delay_seconds * sample_rate

    matched_a = []
    matched_b = []
    matched_b_indices = set()  # Track which B edges are used
    unmatched_a = []  # A edges that didn't find a match

    j = 0
    for t_a in times_a:
        # Advance j to first candidate (skip B edges that are too early)
        while j < len(times_b) and times_b[j] < t_a - max_delay_samples:
            j += 1

        # Find the closest B edge (not just the first one in range)
        best_idx = None
        best_dist = float('inf')

        for k in range(j, len(times_b)):
            dist = abs(times_b[k] - t_a)
            if dist < best_dist:
                best_dist = dist
                best_idx = k
            elif dist > best_dist:
                # Past minimum - stop searching
                break

        # Only match if within max delay threshold
        if best_idx is not None and best_dist < max_delay_samples:
            matched_a.append(t_a)
            matched_b.append(times_b[best_idx])
            matched_b_indices.add(best_idx)
            j = best_idx + 1  # Move past matched B edge
        else:
            # This A edge didn't match any B edge
            unmatched_a.append(t_a)

    # Find B edges that were never matched
    unmatched_b = [times_b[i] for i in range(len(times_b)) if i not in matched_b_indices]

    matched_a = np.array(matched_a)
    matched_b = np.array(matched_b)
    delays = (matched_b - matched_a) / sample_rate

    return MatchResult(
        matched_a=matched_a,
        matched_b=matched_b,
        delays=delays,
        total_a=len(times_a),
        total_b=len(times_b),
        unmatched_a_times=np.array(unmatched_a),
        unmatched_b_times=np.array(unmatched_b),
    )
