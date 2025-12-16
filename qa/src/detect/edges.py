"""
Edge detection for AC-coupled pulse signals.

Two detection methods:
1. Hysteresis threshold (legacy): detect_edges()
2. Peak-based with sub-sample interpolation (recommended): detect_peaks()

In AC-coupled signals:
- Rising edges (LOW→HIGH) appear as negative voltage spikes (troughs)
- Falling edges (HIGH→LOW) appear as positive voltage spikes (peaks)
"""

import numpy as np
import numba as nb

# Default thresholds (volts)
DEFAULT_NEG_EDGE = -0.6    # Rising edge threshold (negative spike)
DEFAULT_POS_EDGE = +0.6    # Falling edge threshold (positive spike)
DEFAULT_RESET_WIN = 0.2    # Must return to ±0.2V before next edge


@nb.njit
def detect_edges(
    x: np.ndarray,
    neg_edge: float = DEFAULT_NEG_EDGE,
    pos_edge: float = DEFAULT_POS_EDGE,
    reset_win: float = DEFAULT_RESET_WIN,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Detect rising and falling edges in an AC-coupled pulse signal.

    The signal is AC-coupled, so:
    - Rising edges (LOW→HIGH transitions) appear as negative voltage spikes
    - Falling edges (HIGH→LOW transitions) appear as positive voltage spikes

    Uses hysteresis to avoid multiple triggers from noise:
    - Edge detected when signal crosses threshold
    - Must return within reset_win of zero before next edge can be detected

    Args:
        x: Signal samples (real-valued)
        neg_edge: Threshold for rising edge detection (default -0.6V)
        pos_edge: Threshold for falling edge detection (default +0.6V)
        reset_win: Signal must return within ±reset_win before next edge (default 0.2V)

    Returns:
        Tuple of (rising_indices, falling_indices) as int64 arrays
    """
    N = x.size

    rising = []
    falling = []

    can_rise = True
    can_fall = True

    for i in range(N):
        v = x[i]

        # Reset when signal returns near zero
        if abs(v) < reset_win:
            can_rise = True
            can_fall = True

        # Rising edge (negative-going spike)
        if can_rise and v < neg_edge:
            rising.append(i)
            can_rise = False

        # Falling edge (positive-going spike)
        if can_fall and v > pos_edge:
            falling.append(i)
            can_fall = False

    return np.array(rising), np.array(falling)


def detect_edges_dual(
    chan_a: np.ndarray,
    chan_b: np.ndarray,
    neg_edge: float = DEFAULT_NEG_EDGE,
    pos_edge: float = DEFAULT_POS_EDGE,
    reset_win: float = DEFAULT_RESET_WIN,
) -> dict:
    """
    Detect edges on both channels of a dual-channel capture.

    Args:
        chan_a: Channel A samples (real-valued)
        chan_b: Channel B samples (real-valued)
        neg_edge: Threshold for rising edge detection
        pos_edge: Threshold for falling edge detection
        reset_win: Reset window threshold

    Returns:
        Dict with keys: rising_a, falling_a, rising_b, falling_b
    """
    rising_a, falling_a = detect_edges(chan_a, neg_edge, pos_edge, reset_win)
    rising_b, falling_b = detect_edges(chan_b, neg_edge, pos_edge, reset_win)

    return {
        'rising_a': rising_a,
        'falling_a': falling_a,
        'rising_b': rising_b,
        'falling_b': falling_b,
    }


# Default parameters for peak detection
DEFAULT_MIN_HEIGHT = 0.5   # Minimum peak amplitude (volts)
DEFAULT_MIN_DISTANCE = 100  # Minimum samples between peaks (~10us at 10MSps)

# Default parameters for zero-crossing detection
DEFAULT_THRESHOLD = 0.4    # Threshold for rising edge detection (volts)


@nb.njit
def _parabolic_vertex(y_m1: float, y_0: float, y_p1: float) -> float:
    """
    Compute sub-sample offset of parabola vertex.

    Given values at x=-1, 0, +1, finds the x-offset of the vertex.

    Returns:
        Offset from center point (between -0.5 and +0.5 typically)
    """
    denom = y_m1 - 2.0 * y_0 + y_p1
    if abs(denom) < 1e-10:
        return 0.0
    delta = 0.5 * (y_m1 - y_p1) / denom
    # Clamp to reasonable range
    if delta > 1.0:
        return 1.0
    if delta < -1.0:
        return -1.0
    return delta


@nb.njit
def detect_peaks(
    x: np.ndarray,
    min_height: float = DEFAULT_MIN_HEIGHT,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Detect peaks and troughs with sub-sample precision using parabolic interpolation.

    For AC-coupled signals:
    - Troughs (local minima below -min_height) = rising edges
    - Peaks (local maxima above +min_height) = falling edges

    Args:
        x: Signal samples (real-valued)
        min_height: Minimum absolute amplitude to consider (default 0.5V)
        min_distance: Minimum samples between consecutive peaks of same type

    Returns:
        Tuple of (rising_times, falling_times) as float64 arrays (in samples)
    """
    N = x.size
    if N < 3:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    rising = []   # troughs (negative peaks)
    falling = []  # peaks (positive peaks)

    last_rising = -min_distance - 1
    last_falling = -min_distance - 1

    for i in range(1, N - 1):
        v = x[i]
        v_prev = x[i - 1]
        v_next = x[i + 1]

        # Check for local maximum (falling edge in AC-coupled signal)
        if v > v_prev and v > v_next and v > min_height:
            if i - last_falling >= min_distance:
                # Parabolic interpolation for sub-sample precision
                delta = _parabolic_vertex(v_prev, v, v_next)
                falling.append(float(i) + delta)
                last_falling = i

        # Check for local minimum (rising edge in AC-coupled signal)
        elif v < v_prev and v < v_next and v < -min_height:
            if i - last_rising >= min_distance:
                # Parabolic interpolation for sub-sample precision
                delta = _parabolic_vertex(v_prev, v, v_next)
                rising.append(float(i) + delta)
                last_rising = i

    return np.array(rising, dtype=np.float64), np.array(falling, dtype=np.float64)


def detect_peaks_dual(
    chan_a: np.ndarray,
    chan_b: np.ndarray,
    min_height: float = DEFAULT_MIN_HEIGHT,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> dict:
    """
    Detect peaks on both channels of a dual-channel capture.

    Args:
        chan_a: Channel A samples (real-valued)
        chan_b: Channel B samples (real-valued)
        min_height: Minimum peak amplitude
        min_distance: Minimum samples between peaks

    Returns:
        Dict with keys: rising_a, falling_a, rising_b, falling_b (float64 arrays)
    """
    rising_a, falling_a = detect_peaks(chan_a, min_height, min_distance)
    rising_b, falling_b = detect_peaks(chan_b, min_height, min_distance)

    return {
        'rising_a': rising_a,
        'falling_a': falling_a,
        'rising_b': rising_b,
        'falling_b': falling_b,
    }


@nb.njit
def detect_crossings(
    x: np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Detect threshold crossings with sub-sample precision using linear interpolation.

    More robust than peak detection for AC-coupled signals with asymmetric shapes.
    Uses linear interpolation on the rising/falling edges for sub-sample timing.

    For AC-coupled signals:
    - Rising edges cross -threshold from below (negative-going spike)
    - Falling edges cross +threshold from below (positive-going spike)

    Args:
        x: Signal samples (real-valued)
        threshold: Threshold for edge detection (default 0.4V)
        min_distance: Minimum samples between consecutive edges of same type

    Returns:
        Tuple of (rising_times, falling_times) as float64 arrays (in samples)
    """
    N = x.size
    if N < 2:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    rising = []   # negative threshold crossings (rising edges in original signal)
    falling = []  # positive threshold crossings (falling edges in original signal)

    last_rising = -min_distance - 1
    last_falling = -min_distance - 1

    neg_threshold = -threshold

    for i in range(1, N):
        v_prev = x[i - 1]
        v = x[i]

        # Rising edge: signal crosses negative threshold going down
        # (i.e., previous > neg_threshold and current <= neg_threshold)
        if v_prev > neg_threshold and v <= neg_threshold:
            if i - last_rising >= min_distance:
                # Linear interpolation for sub-sample precision
                denom = v_prev - v
                if abs(denom) > 1e-10:
                    frac = (v_prev - neg_threshold) / denom
                    crossing = (i - 1) + frac
                else:
                    crossing = float(i)
                rising.append(crossing)
                last_rising = i

        # Falling edge: signal crosses positive threshold going up
        # (i.e., previous < threshold and current >= threshold)
        if v_prev < threshold and v >= threshold:
            if i - last_falling >= min_distance:
                # Linear interpolation for sub-sample precision
                denom = v - v_prev
                if abs(denom) > 1e-10:
                    frac = (threshold - v_prev) / denom
                    crossing = (i - 1) + frac
                else:
                    crossing = float(i)
                falling.append(crossing)
                last_falling = i

    return np.array(rising, dtype=np.float64), np.array(falling, dtype=np.float64)


def detect_crossings_dual(
    chan_a: np.ndarray,
    chan_b: np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> dict:
    """
    Detect threshold crossings on both channels of a dual-channel capture.

    Args:
        chan_a: Channel A samples (real-valued)
        chan_b: Channel B samples (real-valued)
        threshold: Threshold for edge detection
        min_distance: Minimum samples between edges

    Returns:
        Dict with keys: rising_a, falling_a, rising_b, falling_b (float64 arrays)
    """
    rising_a, falling_a = detect_crossings(chan_a, threshold, min_distance)
    rising_b, falling_b = detect_crossings(chan_b, threshold, min_distance)

    return {
        'rising_a': rising_a,
        'falling_a': falling_a,
        'rising_b': rising_b,
        'falling_b': falling_b,
    }


# Streaming threshold crossing detection

@nb.njit
def detect_crossings_streaming(
    x: np.ndarray,
    last_rising_idx: int,
    last_falling_idx: int,
    tail: np.ndarray,
    sample_offset: int,
    threshold: float = DEFAULT_THRESHOLD,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> tuple[np.ndarray, np.ndarray, int, int, np.ndarray]:
    """
    Streaming threshold crossing detection with state preserved across buffer boundaries.

    Prepends tail sample from previous buffer to handle crossings at boundaries.
    Returns updated state for next buffer.

    Args:
        x: Current buffer samples (real-valued)
        last_rising_idx: Global sample index of last detected rising edge
        last_falling_idx: Global sample index of last detected falling edge
        tail: Last sample from previous buffer (empty for first call)
        sample_offset: Global sample offset for this buffer's start
        threshold: Threshold for edge detection
        min_distance: Minimum samples between edges

    Returns:
        Tuple of:
        - rising_times: Global sample times of rising edges (float64)
        - falling_times: Global sample times of falling edges (float64)
        - new_last_rising_idx: Updated last rising edge index
        - new_last_falling_idx: Updated last falling edge index
        - new_tail: Tail sample to preserve for next call
    """
    # Ensure x is float64 for consistent precision
    x64 = x.astype(np.float64)

    # Prepend tail from previous buffer if available
    if len(tail) > 0:
        x_full = np.concatenate((tail, x64))
        offset_adj = len(tail)
    else:
        x_full = x64
        offset_adj = 0

    N = x_full.size
    if N < 2:
        new_tail = x64[-1:] if len(x64) >= 1 else x64.copy()
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
            last_rising_idx,
            last_falling_idx,
            new_tail,
        )

    rising = []
    falling = []
    neg_threshold = -threshold

    for i in range(1, N):
        # Global index for this sample
        global_idx = sample_offset - offset_adj + i

        v_prev = x_full[i - 1]
        v = x_full[i]

        # Rising edge: signal crosses negative threshold going down
        if v_prev > neg_threshold and v <= neg_threshold:
            if global_idx - last_rising_idx >= min_distance:
                # Linear interpolation for sub-sample precision
                denom = v_prev - v
                if abs(denom) > 1e-10:
                    frac = (v_prev - neg_threshold) / denom
                    crossing = float(global_idx - 1) + frac
                else:
                    crossing = float(global_idx)
                rising.append(crossing)
                last_rising_idx = global_idx

        # Falling edge: signal crosses positive threshold going up
        if v_prev < threshold and v >= threshold:
            if global_idx - last_falling_idx >= min_distance:
                # Linear interpolation for sub-sample precision
                denom = v - v_prev
                if abs(denom) > 1e-10:
                    frac = (threshold - v_prev) / denom
                    crossing = float(global_idx - 1) + frac
                else:
                    crossing = float(global_idx)
                falling.append(crossing)
                last_falling_idx = global_idx

    # Preserve last sample for next buffer (use float64 for consistency)
    new_tail = x64[-1:].copy()

    return (
        np.array(rising, dtype=np.float64),
        np.array(falling, dtype=np.float64),
        last_rising_idx,
        last_falling_idx,
        new_tail,
    )


class StreamingCrossingDetector:
    """
    Stateful wrapper for streaming threshold crossing detection.

    Features:
    - Skip initial samples (default 100ms @ 10MSps)
    - Wait for signal to settle within reset window before detecting
    - Buffer boundary handling via tail preservation
    - Supports single channel (for per-channel processing)

    Usage:
        detector = StreamingCrossingDetector(threshold=0.4, min_distance=2000)
        for chunk in stream:
            edges = detector.process(chunk)
            # edges contains {'rising': [...], 'falling': [...]}
    """

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        min_distance: int = DEFAULT_MIN_DISTANCE,
        skip_samples: int = 1_000_000,  # 100ms @ 10MSps
        reset_window: float = DEFAULT_RESET_WIN,
        settle: bool = True,
    ):
        self.threshold = threshold
        self.min_distance = min_distance
        self.skip_samples = skip_samples
        self.reset_window = reset_window
        self.settle = settle
        self.reset()

    def reset(self):
        """Reset detector state."""
        self._sample_offset = 0
        self._last_rising = -self.min_distance - 1
        self._last_falling = -self.min_distance - 1
        self._tail = np.empty(0, dtype=np.float64)  # float64 for precision
        self._settled = False
        self._skipped = False
        self._jit_warm = False

    def process(self, x: np.ndarray) -> dict:
        """
        Process a chunk of samples and return detected edges.

        Args:
            x: Signal samples for this chunk (single channel)

        Returns:
            Dict with 'rising' and 'falling' (float64 arrays)
            Times are global sample indices (not relative to chunk)
        """
        chunk_len = len(x)

        # Phase 1: Skip initial samples (but use them for JIT warmup!)
        if not self._skipped:
            # Run detection to warm up JIT, but discard results
            if not self._jit_warm:
                detect_crossings_streaming(
                    x,
                    self._last_rising,
                    self._last_falling,
                    self._tail,
                    self._sample_offset,
                    self.threshold,
                    self.min_distance,
                )
                self._jit_warm = True

            if self._sample_offset + chunk_len <= self.skip_samples:
                # Entire chunk is within skip region
                self._sample_offset += chunk_len
                return {'rising': np.array([], dtype=np.float64),
                        'falling': np.array([], dtype=np.float64)}
            else:
                # Partial skip - trim the beginning
                skip_in_chunk = self.skip_samples - self._sample_offset
                if skip_in_chunk > 0:
                    x = x[skip_in_chunk:]
                    self._sample_offset = self.skip_samples
                self._skipped = True
                # Reset state for clean start after skip
                self._last_rising = -self.min_distance - 1
                self._last_falling = -self.min_distance - 1
                self._tail = np.empty(0, dtype=np.float64)

        # Phase 2: Wait for signal to settle (optional)
        if not self._settled:
            if self.settle:
                settle_idx = self._find_settle_point(x)
                if settle_idx is None:
                    # No settle point found in this chunk
                    self._sample_offset += len(x)
                    return {'rising': np.array([], dtype=np.float64),
                            'falling': np.array([], dtype=np.float64)}
                # Found settle point - trim and continue
                x = x[settle_idx:]
                self._sample_offset += settle_idx
            self._settled = True

        # Phase 3: Normal detection
        rising, falling, self._last_rising, self._last_falling, self._tail = \
            detect_crossings_streaming(
                x,
                self._last_rising,
                self._last_falling,
                self._tail,
                self._sample_offset,
                self.threshold,
                self.min_distance,
            )

        self._sample_offset += len(x)

        return {
            'rising': rising,
            'falling': falling,
        }

    def _find_settle_point(self, x: np.ndarray) -> int | None:
        """Find first sample within reset window (signal settled)."""
        # Use vectorized numpy instead of Python loop for speed
        mask = np.abs(x) < self.reset_window
        indices = np.where(mask)[0]
        return int(indices[0]) if len(indices) > 0 else None

    @property
    def samples_processed(self) -> int:
        """Total samples processed so far (including skipped)."""
        return self._sample_offset

    @property
    def is_settled(self) -> bool:
        """Whether the detector has found a settle point and is detecting."""
        return self._settled


# =============================================================================
# Linear Regression Edge Detection
# =============================================================================
# More robust than simple threshold crossing - uses multiple points on the
# rising/falling edge to fit a line and extrapolate the 50% crossing time.
# This is amplitude-independent and averages out noise.

DEFAULT_PEAK_SEARCH = 15    # Samples to search for peak after trigger
DEFAULT_LOW_PCT = 0.2       # Lower bound of edge (20% of peak)
DEFAULT_HIGH_PCT = 0.8      # Upper bound of edge (80% of peak)


@nb.njit
def _linreg_crossing(x_pts: np.ndarray, y_pts: np.ndarray, target_y: float) -> float:
    """
    Linear regression on points, return x where y = target_y.

    Uses least squares: y = mx + b, solve for x = (target_y - b) / m
    """
    n = len(x_pts)
    if n < 2:
        return x_pts[0] if n == 1 else 0.0

    sum_x = 0.0
    sum_y = 0.0
    sum_xy = 0.0
    sum_xx = 0.0

    for i in range(n):
        sum_x += x_pts[i]
        sum_y += y_pts[i]
        sum_xy += x_pts[i] * y_pts[i]
        sum_xx += x_pts[i] * x_pts[i]

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-10:
        return x_pts[n // 2]  # Fallback to middle point

    m = (n * sum_xy - sum_x * sum_y) / denom
    b = (sum_y - m * sum_x) / n

    if abs(m) < 1e-10:
        return x_pts[n // 2]  # Fallback if slope is zero

    return (target_y - b) / m


@nb.njit
def detect_edges_linreg(
    x: np.ndarray,
    trigger_threshold: float = DEFAULT_THRESHOLD,
    peak_search: int = DEFAULT_PEAK_SEARCH,
    low_pct: float = DEFAULT_LOW_PCT,
    high_pct: float = DEFAULT_HIGH_PCT,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Detect edges using linear regression on the rising/falling slope.

    Algorithm:
    1. Trigger on threshold crossing (like before)
    2. Find peak within peak_search samples
    3. Collect points between low_pct and high_pct of peak amplitude
    4. Linear regression to find 50% crossing time

    This is more robust than simple threshold crossing because:
    - Uses multiple points, averaging out noise
    - Adaptive to pulse amplitude (uses % of peak, not fixed voltage)
    - Extrapolates to consistent 50% point

    Args:
        x: Signal samples (real-valued)
        trigger_threshold: Threshold for initial edge detection (default 0.4V)
        peak_search: Samples to search for peak after trigger (default 15)
        low_pct: Lower bound as fraction of peak (default 0.2)
        high_pct: Upper bound as fraction of peak (default 0.8)
        min_distance: Minimum samples between edges (default 100)

    Returns:
        Tuple of (rising_times, falling_times) as float64 arrays (in samples)
    """
    N = x.size
    if N < peak_search + 2:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

    rising = []   # negative spikes (rising edges in original signal)
    falling = []  # positive spikes (falling edges in original signal)

    last_rising = -min_distance - 1
    last_falling = -min_distance - 1

    # Pre-allocate arrays for edge points (max possible points)
    max_pts = peak_search + 5
    edge_x = np.empty(max_pts, dtype=np.float64)
    edge_y = np.empty(max_pts, dtype=np.float64)

    i = 1
    while i < N - peak_search:
        v_prev = x[i - 1]
        v = x[i]

        # Rising edge: crosses negative threshold going down
        if v_prev > -trigger_threshold and v <= -trigger_threshold:
            if i - last_rising >= min_distance:
                # Find peak (minimum) in next peak_search samples
                search_end = i + peak_search
                if search_end > N:
                    search_end = N

                peak_idx = i
                peak_val = x[i]
                for j in range(i, search_end):
                    if x[j] < peak_val:
                        peak_val = x[j]
                        peak_idx = j

                # Define levels as fraction of peak
                level_20 = low_pct * peak_val   # e.g., -0.16 if peak is -0.8
                level_80 = high_pct * peak_val  # e.g., -0.64 if peak is -0.8
                level_50 = 0.5 * peak_val

                # Collect points on falling edge between 20-80%
                # Search backwards from peak (10 samples back, can go before trigger)
                n_pts = 0
                for j in range(peak_idx, max(peak_idx - 10, 0) - 1, -1):
                    val = x[j]
                    # For negative peak: level_80 <= val <= level_20
                    # (level_80 is more negative, level_20 is less negative)
                    if level_80 <= val <= level_20:
                        edge_x[n_pts] = float(j)
                        edge_y[n_pts] = val
                        n_pts += 1
                        if n_pts >= max_pts:
                            break

                if n_pts >= 2:
                    crossing = _linreg_crossing(edge_x[:n_pts], edge_y[:n_pts], level_50)
                    rising.append(crossing)
                    last_rising = i
                elif n_pts == 1:
                    # Single point - use it directly
                    rising.append(edge_x[0])
                    last_rising = i
                # else: not enough points, skip this edge

        # Falling edge: crosses positive threshold going up
        if v_prev < trigger_threshold and v >= trigger_threshold:
            if i - last_falling >= min_distance:
                # Find peak (maximum) in next peak_search samples
                search_end = i + peak_search
                if search_end > N:
                    search_end = N

                peak_idx = i
                peak_val = x[i]
                for j in range(i, search_end):
                    if x[j] > peak_val:
                        peak_val = x[j]
                        peak_idx = j

                # Define levels as fraction of peak
                level_20 = low_pct * peak_val   # e.g., 0.16 if peak is 0.8
                level_80 = high_pct * peak_val  # e.g., 0.64 if peak is 0.8
                level_50 = 0.5 * peak_val

                # Collect points on rising edge between 20-80%
                # Search backwards from peak (10 samples back, can go before trigger)
                n_pts = 0
                for j in range(peak_idx, max(peak_idx - 10, 0) - 1, -1):
                    val = x[j]
                    # For positive peak: level_20 <= val <= level_80
                    if level_20 <= val <= level_80:
                        edge_x[n_pts] = float(j)
                        edge_y[n_pts] = val
                        n_pts += 1
                        if n_pts >= max_pts:
                            break

                if n_pts >= 2:
                    crossing = _linreg_crossing(edge_x[:n_pts], edge_y[:n_pts], level_50)
                    falling.append(crossing)
                    last_falling = i
                elif n_pts == 1:
                    falling.append(edge_x[0])
                    last_falling = i

        i += 1

    return np.array(rising, dtype=np.float64), np.array(falling, dtype=np.float64)


def detect_edges_linreg_dual(
    chan_a: np.ndarray,
    chan_b: np.ndarray,
    trigger_threshold: float = DEFAULT_THRESHOLD,
    peak_search: int = DEFAULT_PEAK_SEARCH,
    low_pct: float = DEFAULT_LOW_PCT,
    high_pct: float = DEFAULT_HIGH_PCT,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> dict:
    """
    Detect edges on both channels using linear regression method.

    Args:
        chan_a: Channel A samples (real-valued)
        chan_b: Channel B samples (real-valued)
        trigger_threshold: Threshold for initial edge detection
        peak_search: Samples to search for peak after trigger
        low_pct: Lower bound as fraction of peak
        high_pct: Upper bound as fraction of peak
        min_distance: Minimum samples between edges

    Returns:
        Dict with keys: rising_a, falling_a, rising_b, falling_b (float64 arrays)
    """
    rising_a, falling_a = detect_edges_linreg(
        chan_a, trigger_threshold, peak_search, low_pct, high_pct, min_distance
    )
    rising_b, falling_b = detect_edges_linreg(
        chan_b, trigger_threshold, peak_search, low_pct, high_pct, min_distance
    )

    return {
        'rising_a': rising_a,
        'falling_a': falling_a,
        'rising_b': rising_b,
        'falling_b': falling_b,
    }


# Streaming version with state preservation

LINREG_TAIL_SIZE = 20  # Need more tail for peak search


@nb.njit
def detect_edges_linreg_streaming(
    x: np.ndarray,
    last_rising_idx: int,
    last_falling_idx: int,
    tail: np.ndarray,
    sample_offset: int,
    trigger_threshold: float = DEFAULT_THRESHOLD,
    peak_search: int = DEFAULT_PEAK_SEARCH,
    low_pct: float = DEFAULT_LOW_PCT,
    high_pct: float = DEFAULT_HIGH_PCT,
    min_distance: int = DEFAULT_MIN_DISTANCE,
) -> tuple[np.ndarray, np.ndarray, int, int, np.ndarray]:
    """
    Streaming linear regression edge detection.

    Same algorithm as detect_edges_linreg but handles buffer boundaries.
    Works with float32 input (from complex64.real) - no type conversion needed.
    """
    # Prepend tail from previous buffer - both are float32, no conversion
    if len(tail) > 0:
        x_full = np.concatenate((tail, x))
        offset_adj = len(tail)
    else:
        x_full = x
        offset_adj = 0

    N = x_full.size
    if N < peak_search + 2:
        new_tail = x[-LINREG_TAIL_SIZE:].copy() if len(x) >= LINREG_TAIL_SIZE else x.copy()
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
            last_rising_idx,
            last_falling_idx,
            new_tail,
        )

    rising = []
    falling = []

    max_pts = peak_search + 5
    edge_x = np.empty(max_pts, dtype=np.float64)
    edge_y = np.empty(max_pts, dtype=np.float64)

    i = 1
    while i < N - peak_search:
        global_idx = sample_offset - offset_adj + i

        v_prev = x_full[i - 1]
        v = x_full[i]

        # Rising edge
        if v_prev > -trigger_threshold and v <= -trigger_threshold:
            if global_idx - last_rising_idx >= min_distance:
                search_end = min(i + peak_search, N)

                peak_idx = i
                peak_val = x_full[i]
                for j in range(i, search_end):
                    if x_full[j] < peak_val:
                        peak_val = x_full[j]
                        peak_idx = j

                level_20 = low_pct * peak_val
                level_80 = high_pct * peak_val
                level_50 = 0.5 * peak_val

                # Collect points on edge (10 samples back from peak, can go before trigger)
                n_pts = 0
                for j in range(peak_idx, max(peak_idx - 10, 0) - 1, -1):
                    val = x_full[j]
                    if level_80 <= val <= level_20:
                        edge_x[n_pts] = float(sample_offset - offset_adj + j)
                        edge_y[n_pts] = val
                        n_pts += 1
                        if n_pts >= max_pts:
                            break

                if n_pts >= 2:
                    crossing = _linreg_crossing(edge_x[:n_pts], edge_y[:n_pts], level_50)
                    rising.append(crossing)
                    last_rising_idx = global_idx
                elif n_pts == 1:
                    rising.append(edge_x[0])
                    last_rising_idx = global_idx

        # Falling edge
        if v_prev < trigger_threshold and v >= trigger_threshold:
            if global_idx - last_falling_idx >= min_distance:
                search_end = min(i + peak_search, N)

                peak_idx = i
                peak_val = x_full[i]
                for j in range(i, search_end):
                    if x_full[j] > peak_val:
                        peak_val = x_full[j]
                        peak_idx = j

                level_20 = low_pct * peak_val
                level_80 = high_pct * peak_val
                level_50 = 0.5 * peak_val

                # Collect points on edge (10 samples back from peak, can go before trigger)
                n_pts = 0
                for j in range(peak_idx, max(peak_idx - 10, 0) - 1, -1):
                    val = x_full[j]
                    if level_20 <= val <= level_80:
                        edge_x[n_pts] = float(sample_offset - offset_adj + j)
                        edge_y[n_pts] = val
                        n_pts += 1
                        if n_pts >= max_pts:
                            break

                if n_pts >= 2:
                    crossing = _linreg_crossing(edge_x[:n_pts], edge_y[:n_pts], level_50)
                    falling.append(crossing)
                    last_falling_idx = global_idx
                elif n_pts == 1:
                    falling.append(edge_x[0])
                    last_falling_idx = global_idx

        i += 1

    # Preserve tail for next buffer - keep as float32
    new_tail = x[-LINREG_TAIL_SIZE:].copy() if len(x) >= LINREG_TAIL_SIZE else x.copy()

    return (
        np.array(rising, dtype=np.float64),
        np.array(falling, dtype=np.float64),
        last_rising_idx,
        last_falling_idx,
        new_tail,
    )


class StreamingLinregDetector:
    """
    Stateful wrapper for streaming linear regression edge detection.

    More robust than StreamingCrossingDetector - uses multiple points on
    each edge to fit a line and find the 50% crossing time.

    Key feature: JIT warmup happens DURING the skip phase, not before.
    This eliminates startup delay while still allowing signal to settle.

    Usage:
        detector = StreamingLinregDetector(trigger_threshold=0.4)
        for chunk in stream:
            edges = detector.process(chunk)
            # edges contains {'rising': [...], 'falling': [...]}
    """

    def __init__(
        self,
        trigger_threshold: float = DEFAULT_THRESHOLD,
        peak_search: int = DEFAULT_PEAK_SEARCH,
        low_pct: float = DEFAULT_LOW_PCT,
        high_pct: float = DEFAULT_HIGH_PCT,
        min_distance: int = DEFAULT_MIN_DISTANCE,
        skip_samples: int = 1_000_000,
        reset_window: float = DEFAULT_RESET_WIN,
        settle: bool = True,
    ):
        self.trigger_threshold = trigger_threshold
        self.peak_search = peak_search
        self.low_pct = low_pct
        self.high_pct = high_pct
        self.min_distance = min_distance
        self.skip_samples = skip_samples
        self.reset_window = reset_window
        self.settle = settle
        self.reset()

    def reset(self):
        """Reset detector state."""
        self._sample_offset = 0
        self._last_rising = -self.min_distance - 1
        self._last_falling = -self.min_distance - 1
        self._tail = np.empty(0, dtype=np.float64)  # float64 for precision
        self._settled = False
        self._skipped = False
        self._jit_warm = False

    def process(self, x: np.ndarray) -> dict:
        """
        Process a chunk of samples and return detected edges.

        Args:
            x: Signal samples for this chunk (single channel)

        Returns:
            Dict with 'rising' and 'falling' (float64 arrays)
            Times are global sample indices
        """
        chunk_len = len(x)

        # Phase 1: Skip initial samples (but use them for JIT warmup!)
        if not self._skipped:
            # Run detection to warm up JIT, but discard results
            if not self._jit_warm:
                # First call compiles Numba - run detection on actual data
                detect_edges_linreg_streaming(
                    x,
                    self._last_rising,
                    self._last_falling,
                    self._tail,
                    self._sample_offset,
                    self.trigger_threshold,
                    self.peak_search,
                    self.low_pct,
                    self.high_pct,
                    self.min_distance,
                )
                self._jit_warm = True

            if self._sample_offset + chunk_len <= self.skip_samples:
                self._sample_offset += chunk_len
                return {'rising': np.array([], dtype=np.float64),
                        'falling': np.array([], dtype=np.float64)}
            else:
                skip_in_chunk = self.skip_samples - self._sample_offset
                if skip_in_chunk > 0:
                    x = x[skip_in_chunk:]
                    self._sample_offset = self.skip_samples
                self._skipped = True
                # Reset state for clean start after skip
                self._last_rising = -self.min_distance - 1
                self._last_falling = -self.min_distance - 1
                self._tail = np.empty(0, dtype=np.float64)

        # Phase 2: Wait for signal to settle (optional)
        if not self._settled:
            if self.settle:
                settle_idx = self._find_settle_point(x)
                if settle_idx is None:
                    self._sample_offset += len(x)
                    return {'rising': np.array([], dtype=np.float64),
                            'falling': np.array([], dtype=np.float64)}
                x = x[settle_idx:]
                self._sample_offset += settle_idx
            self._settled = True

        # Phase 3: Detection
        rising, falling, self._last_rising, self._last_falling, self._tail = \
            detect_edges_linreg_streaming(
                x,
                self._last_rising,
                self._last_falling,
                self._tail,
                self._sample_offset,
                self.trigger_threshold,
                self.peak_search,
                self.low_pct,
                self.high_pct,
                self.min_distance,
            )

        self._sample_offset += len(x)

        return {
            'rising': rising,
            'falling': falling,
        }

    def _find_settle_point(self, x: np.ndarray) -> int | None:
        """Find first sample within reset window."""
        # Use vectorized numpy instead of Python loop for speed
        mask = np.abs(x) < self.reset_window
        indices = np.where(mask)[0]
        return int(indices[0]) if len(indices) > 0 else None

    @property
    def samples_processed(self) -> int:
        """Total samples processed so far (including skipped)."""
        return self._sample_offset

    @property
    def is_settled(self) -> bool:
        """Whether the detector has found a settle point and is detecting."""
        return self._settled


