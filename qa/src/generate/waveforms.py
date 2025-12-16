"""
Waveform generation for test signals.

Generates dual-channel pulse waveforms with configurable:
- Phase shift between channels
- Jitter (random timing variation)
- Drift (linear phase change over time)
"""

import numpy as np
from typing import Optional


def generate_dual_pulses(
    freq: float = 2000.0,
    sample_rate: float = 10e6,
    duration: float = 1.0,
    phase_shift_ns: float = 0.0,
    jitter_std_ns: float = 0.0,
    drift_ns_per_s: float = 0.0,
    pulse_width_samples: int = 50,
    amplitude: float = 1.0,
) -> np.ndarray:
    """
    Generate dual-channel pulse waveform with known phase/jitter.

    Creates a complex signal where:
    - Real (I) = Channel A pulses
    - Imag (Q) = Channel B pulses (with phase shift, jitter, drift)

    Args:
        freq: Pulse frequency in Hz (default: 2000)
        sample_rate: Sample rate in Hz (default: 10 MSps)
        duration: Duration in seconds (default: 1.0)
        phase_shift_ns: Fixed phase shift B-A in nanoseconds (default: 0)
        jitter_std_ns: Gaussian jitter std dev in nanoseconds (default: 0)
        drift_ns_per_s: Linear drift rate in ns/s (default: 0)
        pulse_width_samples: Width of each pulse in samples (default: 50)
        amplitude: Pulse amplitude 0.0-1.0 (default: 1.0)

    Returns:
        Complex64 array with dual-channel pulses
    """
    n_samples = int(duration * sample_rate)
    period_samples = sample_rate / freq

    # Convert time offsets to samples
    phase_shift_samples = phase_shift_ns * 1e-9 * sample_rate
    jitter_std_samples = jitter_std_ns * 1e-9 * sample_rate
    drift_samples_per_sample = drift_ns_per_s * 1e-9  # ns/s * 1e-9 * rate / rate

    # Initialize output
    chan_a = np.zeros(n_samples, dtype=np.float32)
    chan_b = np.zeros(n_samples, dtype=np.float32)

    # Generate pulse positions for channel A
    n_pulses = int(duration * freq) + 1
    pulse_times_a = np.arange(n_pulses) * period_samples

    # Generate channel B times with phase shift, jitter, and drift
    if jitter_std_samples > 0:
        jitter = np.random.normal(0, jitter_std_samples, n_pulses)
    else:
        jitter = np.zeros(n_pulses)

    # Drift: increases linearly with time
    drift = pulse_times_a * drift_ns_per_s * 1e-9 * sample_rate

    pulse_times_b = pulse_times_a + phase_shift_samples + jitter + drift

    # Create pulse shape (raised cosine for smooth edges)
    half_width = pulse_width_samples // 2
    pulse_shape = _raised_cosine_pulse(pulse_width_samples, amplitude)

    # Place pulses in channels
    for t_a in pulse_times_a:
        idx = int(round(t_a))
        if idx - half_width >= 0 and idx + half_width < n_samples:
            start = idx - half_width
            end = start + pulse_width_samples
            chan_a[start:end] += pulse_shape

    for t_b in pulse_times_b:
        idx = int(round(t_b))
        if idx - half_width >= 0 and idx + half_width < n_samples:
            start = idx - half_width
            end = start + pulse_width_samples
            chan_b[start:end] += pulse_shape

    # Combine into complex signal (I=A, Q=B)
    return (chan_a + 1j * chan_b).astype(np.complex64)


def _raised_cosine_pulse(width: int, amplitude: float) -> np.ndarray:
    """Generate a raised cosine pulse shape."""
    t = np.linspace(-np.pi, np.pi, width)
    return (amplitude * 0.5 * (1 + np.cos(t))).astype(np.float32)


def generate_square_pulses(
    freq: float = 2000.0,
    sample_rate: float = 10e6,
    duration: float = 1.0,
    phase_shift_ns: float = 0.0,
    jitter_std_ns: float = 0.0,
    duty_cycle: float = 0.05,
    amplitude: float = 0.8,
    rise_time_samples: int = 1,
) -> np.ndarray:
    """
    Generate dual-channel square wave pulses with sub-sample timing precision.

    More realistic simulation of actual FTS pulse signals. Uses interpolation
    to preserve sub-sample timing precision for phase shifts and jitter.

    Pulses start at half-period offset to ensure clean buffer boundaries for
    seamless looping. Jitter is clamped to ±10% of period.

    Args:
        freq: Pulse frequency in Hz
        sample_rate: Sample rate in Hz
        duration: Duration in seconds
        phase_shift_ns: Fixed phase shift B-A in nanoseconds
        jitter_std_ns: Gaussian jitter std dev in nanoseconds
        duty_cycle: Duty cycle (0.0 to 1.0)
        amplitude: Pulse amplitude
        rise_time_samples: Number of samples for rise/fall edges

    Returns:
        Complex64 array with dual-channel square waves
    """
    n_samples = int(duration * sample_rate)
    period_samples = sample_rate / freq
    high_samples = int(period_samples * duty_cycle)

    # Convert to samples
    phase_shift_samples = phase_shift_ns * 1e-9 * sample_rate
    jitter_std_samples = jitter_std_ns * 1e-9 * sample_rate

    # Number of complete pulses that fit (with half-period offset at start)
    # Half-period offset means last pulse ends at (n-0.5)*period + high_samples
    # which leaves half-period margin at buffer end for jitter
    n_pulses = int(duration * freq)

    # Initialize buffer
    chan_a = np.zeros(n_samples, dtype=np.float32)
    chan_b = np.zeros(n_samples, dtype=np.float32)

    # Generate jitter and clamp to ±10% of period
    if jitter_std_samples > 0:
        jitter = np.random.normal(0, jitter_std_samples, n_pulses)
        max_jitter_samples = 0.1 * period_samples
        jitter = np.clip(jitter, -max_jitter_samples, max_jitter_samples)
    else:
        jitter = np.zeros(n_pulses)

    # Create oversampled edge for sub-sample interpolation
    oversample = 100  # 100x oversampling for ~1% of sample precision
    edge_fine = np.linspace(0, 1, rise_time_samples * oversample).astype(np.float32)

    for i in range(n_pulses):
        # Start at half-period offset for clean buffer boundaries
        exact_a = (i + 0.5) * period_samples
        exact_b = (i + 0.5) * period_samples + phase_shift_samples + jitter[i]

        # Place pulses with sub-sample precision
        _place_pulse_subsample(chan_a, exact_a, high_samples, rise_time_samples,
                               edge_fine, oversample, amplitude, n_samples)
        _place_pulse_subsample(chan_b, exact_b, high_samples, rise_time_samples,
                               edge_fine, oversample, amplitude, n_samples)

    return (chan_a + 1j * chan_b).astype(np.complex64)


def _place_pulse_subsample(
    chan: np.ndarray,
    exact_start: float,
    high_samples: int,
    rise_time_samples: int,
    edge_fine: np.ndarray,
    oversample: int,
    amplitude: float,
    n_samples: int,
) -> None:
    """
    Place a pulse with sub-sample timing precision using interpolation.

    The key insight: for a fractional start position, we interpolate the edge
    shape so that the threshold crossing occurs at the correct sub-sample time.

    For a linear edge 0->1 over 5 samples:
    - At frac=0.0: sample 2 is exactly at 0.5 (midpoint)
    - At frac=0.3: sample 2 should be at 0.5 - 0.3*slope = lower value
      This makes the 0.5 crossing happen 0.3 samples later

    Args:
        chan: Output channel array
        exact_start: Exact fractional start position
        high_samples: Duration of high portion in samples
        rise_time_samples: Number of samples for rise/fall edges
        edge_fine: Oversampled edge shape for interpolation
        oversample: Oversampling factor (e.g., 100)
        amplitude: Pulse amplitude
        n_samples: Total samples in output array
    """
    # Integer and fractional parts
    int_start = int(np.floor(exact_start))
    frac = exact_start - int_start

    if int_start < 0 or int_start + high_samples > n_samples:
        return

    # Fractional offset in oversampled space
    # frac > 0 means edge should appear LATER, so we're EARLIER in the ramp
    # We subtract the fractional offset from our position in the edge
    frac_idx = int(round(frac * oversample))

    # Rising edge with interpolation
    for j in range(rise_time_samples):
        idx = int_start + j
        if 0 <= idx < n_samples:
            # Position in oversampled edge, shifted back by fractional amount
            fine_idx = j * oversample - frac_idx
            if fine_idx < 0:
                # Before edge starts - output 0
                chan[idx] = 0
            elif fine_idx < len(edge_fine):
                chan[idx] = edge_fine[fine_idx] * amplitude
            else:
                # Past the edge - full amplitude
                chan[idx] = amplitude

    # High portion
    h_start = int_start + rise_time_samples
    h_end = int_start + high_samples - rise_time_samples
    if h_start < n_samples and h_end > h_start:
        chan[h_start:min(h_end, n_samples)] = amplitude

    # Falling edge with interpolation (mirror of rising)
    # The falling edge goes from amplitude -> 0
    # We traverse edge_fine in reverse: at j=0, we want high values; at j=rise_time-1, low values
    f_start = int_start + high_samples - rise_time_samples
    for j in range(rise_time_samples):
        idx = f_start + j
        if 0 <= idx < n_samples:
            # For falling edge: position in edge goes from end toward start
            # j=0 should be near amplitude, j=rise_time-1 should be near 0
            # edge_fine[499] = 1.0, edge_fine[0] = 0.0
            fine_idx = (rise_time_samples - 1 - j) * oversample + frac_idx
            if fine_idx >= len(edge_fine):
                # Past the edge (still high)
                chan[idx] = amplitude
            elif fine_idx >= 0:
                chan[idx] = edge_fine[fine_idx] * amplitude
            else:
                # Before edge_fine[0] = 0 (already low)
                chan[idx] = 0


def simulate_ac_coupled(
    waveform: np.ndarray,
    tau_samples: int = 1000,
    gain: float = 10.0,
) -> np.ndarray:
    """
    Simulate AC coupling effect on a waveform.

    AC coupling causes the signal to decay to zero between pulses,
    making edges appear as positive/negative spikes.

    Uses a proper high-pass IIR filter (1st order).

    Args:
        waveform: Input complex waveform
        tau_samples: Time constant in samples
        gain: Output gain to scale spikes to measurable levels

    Returns:
        AC-coupled waveform
    """
    # High-pass filter coefficient
    alpha = tau_samples / (tau_samples + 1.0)

    chan_a = waveform.real.astype(np.float64)
    chan_b = waveform.imag.astype(np.float64)

    # High-pass filter: y[n] = alpha * (y[n-1] + x[n] - x[n-1])
    ac_a = np.zeros_like(chan_a)
    ac_b = np.zeros_like(chan_b)

    for i in range(1, len(chan_a)):
        ac_a[i] = alpha * (ac_a[i-1] + chan_a[i] - chan_a[i-1])
        ac_b[i] = alpha * (ac_b[i-1] + chan_b[i] - chan_b[i-1])

    # Scale output
    ac_a *= gain
    ac_b *= gain

    # Clip to reasonable range
    ac_a = np.clip(ac_a, -1.0, 1.0)
    ac_b = np.clip(ac_b, -1.0, 1.0)

    return (ac_a + 1j * ac_b).astype(np.complex64)
