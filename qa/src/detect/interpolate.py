"""
Sub-sample timing refinement using parabolic interpolation.

Uses 3-point parabolic fit around each detected edge to achieve
sub-sample timing accuracy for nanosecond-level jitter measurements.
"""

import numpy as np
import numba as nb


@nb.njit
def parabolic_refine(
    x: np.ndarray,
    edge_indices: np.ndarray,
) -> np.ndarray:
    """
    Refine edge timing using 3-point parabolic interpolation.

    For each edge index, fits a parabola through the point and its
    neighbors to find the sub-sample peak/trough location.

    The vertex of a parabola through points (−1, y₋₁), (0, y₀), (1, y₁)
    is at x = 0.5 * (y₋₁ − y₁) / (y₋₁ − 2*y₀ + y₁)

    Args:
        x: Signal samples (real-valued)
        edge_indices: Integer indices of detected edges

    Returns:
        Float64 array of refined edge positions (in samples)
    """
    out = np.empty(len(edge_indices), dtype=np.float64)
    N = x.size

    for n in range(len(edge_indices)):
        i = edge_indices[n]

        # Boundary protection - can't interpolate at edges
        if i <= 0 or i >= N - 1:
            out[n] = float(i)
            continue

        y_m1 = x[i - 1]
        y_0 = x[i]
        y_p1 = x[i + 1]

        denom = y_m1 - 2.0 * y_0 + y_p1

        if denom == 0:
            # Flat region - no sub-sample refinement possible
            out[n] = float(i)
            continue

        # Standard 3-point parabolic vertex interpolation
        delta = 0.5 * (y_m1 - y_p1) / denom

        # Clamp delta to reasonable range (sanity check)
        if delta > 1.0 or delta < -1.0:
            out[n] = float(i)
        else:
            out[n] = i + delta

    return out


def refine_edges_dual(
    chan_a: np.ndarray,
    chan_b: np.ndarray,
    edges: dict,
) -> dict:
    """
    Refine all edge timings for dual-channel data.

    Args:
        chan_a: Channel A samples
        chan_b: Channel B samples
        edges: Dict from detect_edges_dual() with rising_a, falling_a, etc.

    Returns:
        Dict with refined float64 timestamps: rising_a, falling_a, rising_b, falling_b
    """
    return {
        'rising_a': parabolic_refine(chan_a, edges['rising_a']),
        'falling_a': parabolic_refine(chan_a, edges['falling_a']),
        'rising_b': parabolic_refine(chan_b, edges['rising_b']),
        'falling_b': parabolic_refine(chan_b, edges['falling_b']),
    }
