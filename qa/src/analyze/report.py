"""
Report generation: CSV export, plotting, and HTML reports.
"""

import numpy as np
import csv
import json
import base64
from pathlib import Path
from typing import Optional
from datetime import datetime
from .stats import JitterStats


def save_csv(
    delays_seconds: np.ndarray,
    output_path: str | Path,
    times_a: Optional[np.ndarray] = None,
    sample_rate: Optional[float] = None,
) -> None:
    """
    Save delay measurements to CSV file.

    Args:
        delays_seconds: Array of time delays in seconds
        output_path: Path to output CSV file
        times_a: Optional edge times from channel A (in samples)
        sample_rate: Sample rate (needed if times_a provided, to convert to seconds)
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)

        if times_a is not None and sample_rate is not None:
            writer.writerow(['time_s', 'delay_ns'])
            for i, delay in enumerate(delays_seconds):
                time_s = times_a[i] / sample_rate if i < len(times_a) else i
                writer.writerow([f'{time_s:.9f}', f'{delay * 1e9:.3f}'])
        else:
            writer.writerow(['index', 'delay_ns'])
            for i, delay in enumerate(delays_seconds):
                writer.writerow([i, f'{delay * 1e9:.3f}'])


def save_summary(
    stats: JitterStats,
    output_path: str | Path,
    metadata: Optional[dict] = None,
) -> None:
    """
    Save statistics summary to JSON file.

    Args:
        stats: JitterStats object
        output_path: Path to output JSON file
        metadata: Optional additional metadata to include
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = stats.to_dict()
    if metadata:
        data['metadata'] = metadata

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)


def plot_histogram(
    delays_seconds: np.ndarray,
    output_path: Optional[str | Path] = None,
    title: str = "Delay Distribution",
    bins: int = 100,
    show_normal: bool = True,
) -> None:
    """
    Plot histogram of delay measurements with optional normal distribution overlay.

    Args:
        delays_seconds: Array of time delays in seconds
        output_path: Optional path to save figure (displays if None)
        title: Plot title
        bins: Number of histogram bins
        show_normal: If True, overlay fitted normal distribution curve
    """
    import matplotlib.pyplot as plt
    from scipy import stats as scipy_stats

    delays_ns = delays_seconds * 1e9

    fig, ax = plt.subplots(figsize=(8, 5))
    n, bin_edges, patches = ax.hist(delays_ns, bins=bins, color='steelblue', alpha=0.8, edgecolor='white')
    ax.set_xlabel("Delay (ns)")
    ax.set_ylabel("Count")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)

    # Add stats annotation
    mean = np.mean(delays_ns)
    std = np.std(delays_ns)
    ax.axvline(mean, color='red', linestyle='--', label=f'Mean: {mean:.2f} ns')
    ax.axvline(mean - std, color='orange', linestyle=':', alpha=0.7)
    ax.axvline(mean + std, color='orange', linestyle=':', alpha=0.7, label=f'±1σ: {std:.2f} ns')

    # Add normal distribution overlay
    if show_normal and std > 0:
        x = np.linspace(mean - 4*std, mean + 4*std, 200)
        y = scipy_stats.norm.pdf(x, mean, std)
        # Scale to histogram
        bin_width = bin_edges[1] - bin_edges[0]
        y_scaled = y * len(delays_ns) * bin_width
        ax.plot(x, y_scaled, 'r-', linewidth=2, alpha=0.8, label='Normal fit')

    ax.legend()

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        plt.close()
    else:
        plt.show()


def plot_timeseries(
    delays_seconds: np.ndarray,
    sample_rate: float,
    times_a: Optional[np.ndarray] = None,
    output_path: Optional[str | Path] = None,
    title: str = "Delay vs Time",
    downsample: int = 1,
) -> None:
    """
    Plot delay measurements over time.

    Args:
        delays_seconds: Array of time delays in seconds
        sample_rate: Sample rate in Hz
        times_a: Optional edge times from channel A (in samples)
        output_path: Optional path to save figure (displays if None)
        title: Plot title
        downsample: Plot every Nth point to reduce density
    """
    import matplotlib.pyplot as plt

    delays_ns = delays_seconds * 1e9

    if times_a is not None:
        time_axis = times_a / sample_rate
    else:
        # Estimate based on pulse rate
        pulse_period = np.median(np.diff(np.arange(len(delays_ns))))
        time_axis = np.arange(len(delays_ns)) * pulse_period / sample_rate

    # Downsample for plotting if needed
    if downsample > 1:
        delays_ns = delays_ns[::downsample]
        time_axis = time_axis[::downsample]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(time_axis, delays_ns, 'b.', markersize=1, alpha=0.5)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Delay (ns)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        plt.close()
    else:
        plt.show()


def plot_pulses(
    cfile_path: str | Path,
    sample_rate: float,
    edge_times: np.ndarray,
    output_path: Optional[str | Path] = None,
    threshold: float = 0.3,
    num_pulses: int = 3,
    window_before_us: float = 2.0,
    window_after_us: float = 10.0,
) -> None:
    """
    Plot sample pulses from the raw .cfile data.

    Args:
        cfile_path: Path to the .cfile
        sample_rate: Sample rate in Hz
        edge_times: Detected edge times (in samples) to center plots on
        output_path: Optional path to save figure
        threshold: Threshold used for edge detection (for reference line)
        num_pulses: Number of pulses to plot
        window_before_us: Microseconds to show before edge
        window_after_us: Microseconds to show after edge
    """
    import matplotlib.pyplot as plt

    cfile_path = Path(cfile_path)

    # Calculate window in samples
    window_before = int(window_before_us * 1e-6 * sample_rate)
    window_after = int(window_after_us * 1e-6 * sample_rate)
    window_size = window_before + window_after

    # Select pulses from beginning, middle, and end
    n_edges = len(edge_times)
    if n_edges < num_pulses:
        indices = list(range(n_edges))
    else:
        # Pick from start, middle, end
        indices = [
            0,
            n_edges // 2,
            n_edges - 1,
        ][:num_pulses]

    fig, axes = plt.subplots(num_pulses, 1, figsize=(10, 3 * num_pulses), sharex=True)
    if num_pulses == 1:
        axes = [axes]

    # Time axis in microseconds
    t_us = (np.arange(window_size) - window_before) / sample_rate * 1e6

    # Read data around each edge
    with open(cfile_path, 'rb') as f:
        for i, (ax, edge_idx) in enumerate(zip(axes, indices)):
            edge_sample = int(edge_times[edge_idx])
            start_sample = max(0, edge_sample - window_before)

            # Seek and read
            f.seek(start_sample * 8)  # complex64 = 8 bytes
            chunk = np.fromfile(f, dtype=np.complex64, count=window_size)

            if len(chunk) < window_size:
                # Pad if near end of file
                chunk = np.pad(chunk, (0, window_size - len(chunk)), mode='constant')

            # Adjust time axis if we started at 0
            actual_t_us = t_us.copy()
            if edge_sample < window_before:
                actual_t_us = actual_t_us[window_before - edge_sample:]

            # Plot both channels
            ax.plot(actual_t_us[:len(chunk)], chunk.real, 'b-', linewidth=0.8, label='Chan A', alpha=0.8)
            ax.plot(actual_t_us[:len(chunk)], chunk.imag, 'g-', linewidth=0.8, label='Chan B', alpha=0.8)

            # Reference lines
            ax.axhline(threshold, color='r', linestyle='--', alpha=0.5, linewidth=0.8)
            ax.axhline(-threshold, color='r', linestyle='--', alpha=0.5, linewidth=0.8)
            ax.axhline(0, color='gray', linestyle='-', alpha=0.3, linewidth=0.5)
            ax.axvline(0, color='orange', linestyle='-', alpha=0.5, linewidth=1, label='Edge')

            # Labels
            time_s = edge_times[edge_idx] / sample_rate
            position = ['Start', 'Middle', 'End'][i] if num_pulses == 3 else f'Pulse {i+1}'
            ax.set_title(f'{position} (t={time_s:.3f}s, edge #{edge_idx})', fontsize=10)
            ax.set_ylabel('Amplitude')
            ax.grid(True, alpha=0.3)
            ax.set_ylim(-1, 1)

            if i == 0:
                ax.legend(loc='upper right', fontsize=8)

    axes[-1].set_xlabel('Time (µs)')
    plt.suptitle('Sample Pulses', fontsize=12, y=1.02)
    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close()
    else:
        plt.show()


def plot_period_histogram(
    periods_a: np.ndarray,
    periods_b: np.ndarray,
    output_path: Optional[str | Path] = None,
    nominal_freq: float = 2000.0,
    bins: int = 100,
    split: bool = False,
) -> None:
    """
    Plot histogram of periods for both channels.

    Args:
        periods_a: Array of periods for channel A in seconds
        periods_b: Array of periods for channel B in seconds
        output_path: Optional path to save figure
        nominal_freq: Nominal pulse frequency in Hz
        bins: Number of histogram bins
        split: If True, plot channels in separate subplots
    """
    import matplotlib.pyplot as plt

    periods_a_us = periods_a * 1e6
    periods_b_us = periods_b * 1e6
    nominal_period_us = 1e6 / nominal_freq

    if split:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))

        # Channel A - independent bins
        ax1.hist(periods_a_us, bins=bins, alpha=0.8, color='blue', edgecolor='white')
        ax1.axvline(nominal_period_us, color='green', linestyle='--', linewidth=2, label=f'Nominal')
        ax1.axvline(np.mean(periods_a_us), color='blue', linestyle=':', linewidth=1.5, label='Mean')
        ax1.set_xlabel("Period (µs)")
        ax1.set_ylabel("Count")
        ax1.set_title(f"Channel A: {np.mean(periods_a_us):.3f} ± {np.std(periods_a_us):.3f} µs [min={np.min(periods_a_us):.3f}, max={np.max(periods_a_us):.3f}]")
        ax1.legend(loc='upper right')
        ax1.grid(True, alpha=0.3)

        # Channel B - independent bins
        ax2.hist(periods_b_us, bins=bins, alpha=0.8, color='red', edgecolor='white')
        ax2.axvline(nominal_period_us, color='green', linestyle='--', linewidth=2, label=f'Nominal')
        ax2.axvline(np.mean(periods_b_us), color='red', linestyle=':', linewidth=1.5, label='Mean')
        ax2.set_xlabel("Period (µs)")
        ax2.set_ylabel("Count")
        ax2.set_title(f"Channel B: {np.mean(periods_b_us):.3f} ± {np.std(periods_b_us):.3f} µs [min={np.min(periods_b_us):.3f}, max={np.max(periods_b_us):.3f}]")
        ax2.legend(loc='upper right')
        ax2.grid(True, alpha=0.3)
    else:
        fig, ax = plt.subplots(figsize=(10, 5))

        # Plot both histograms
        ax.hist(periods_a_us, bins=bins, alpha=0.6, color='blue', label='Channel A', edgecolor='white')
        ax.hist(periods_b_us, bins=bins, alpha=0.6, color='red', label='Channel B', edgecolor='white')

        # Add nominal and mean lines
        ax.axvline(nominal_period_us, color='green', linestyle='--', linewidth=2, label=f'Nominal ({nominal_freq} Hz)')
        ax.axvline(np.mean(periods_a_us), color='blue', linestyle=':', linewidth=1.5)
        ax.axvline(np.mean(periods_b_us), color='red', linestyle=':', linewidth=1.5)

        ax.set_xlabel("Period (µs)")
        ax.set_ylabel("Count")
        ax.set_title("Period Distribution")
        ax.legend()
        ax.grid(True, alpha=0.3)

        # Add stats annotation
        stats_text = (
            f"A: {np.mean(periods_a_us):.3f} ± {np.std(periods_a_us):.3f} µs\n"
            f"B: {np.mean(periods_b_us):.3f} ± {np.std(periods_b_us):.3f} µs"
        )
        ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, verticalalignment='top',
                fontfamily='monospace', fontsize=9, bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        plt.close()
    else:
        plt.show()


def plot_periods(
    periods_a: np.ndarray,
    periods_b: np.ndarray,
    sample_rate: float,
    edge_times_a: np.ndarray,
    edge_times_b: np.ndarray,
    output_path: Optional[str | Path] = None,
    nominal_freq: float = 2000.0,
    split: bool = False,
) -> None:
    """
    Plot period measurements for both channels to visualize frequency skew.

    Args:
        periods_a: Array of periods for channel A in seconds
        periods_b: Array of periods for channel B in seconds
        sample_rate: Sample rate in Hz
        edge_times_a: Edge times for channel A in samples
        edge_times_b: Edge times for channel B in samples
        output_path: Optional path to save figure (displays if None)
        nominal_freq: Nominal pulse frequency in Hz
        split: If True, plot channels in separate subplots with independent y-axes
    """
    import matplotlib.pyplot as plt

    periods_a_us = periods_a * 1e6
    periods_b_us = periods_b * 1e6
    nominal_period_us = 1e6 / nominal_freq

    # Time axis (use edge times)
    # Handle case where edge_times has same length as periods (streaming) or +1 (batch)
    if len(edge_times_a) == len(periods_a):
        time_a = edge_times_a / sample_rate
    else:
        time_a = edge_times_a[1:len(periods_a)+1] / sample_rate
    if len(edge_times_b) == len(periods_b):
        time_b = edge_times_b / sample_rate
    else:
        time_b = edge_times_b[1:len(periods_b)+1] / sample_rate

    if split:
        fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

        # Channel A
        ax1 = axes[0]
        ax1.plot(time_a, periods_a_us, 'b.', markersize=1, alpha=0.5)
        ax1.axhline(nominal_period_us, color='green', linestyle='--', alpha=0.7, label=f'Nominal')
        ax1.axhline(np.mean(periods_a_us), color='blue', linestyle=':', alpha=0.7, label='Mean')
        ax1.set_ylabel("Period (µs)")
        ax1.set_title(f"Channel A: {np.mean(periods_a_us):.3f} ± {np.std(periods_a_us):.3f} µs [min={np.min(periods_a_us):.3f}, max={np.max(periods_a_us):.3f}]")
        ax1.legend(loc='upper right')
        ax1.grid(True, alpha=0.3)

        # Channel B
        ax2 = axes[1]
        ax2.plot(time_b, periods_b_us, 'r.', markersize=1, alpha=0.5)
        ax2.axhline(nominal_period_us, color='green', linestyle='--', alpha=0.7, label=f'Nominal')
        ax2.axhline(np.mean(periods_b_us), color='red', linestyle=':', alpha=0.7, label='Mean')
        ax2.set_xlabel("Time (s)")
        ax2.set_ylabel("Period (µs)")
        ax2.set_title(f"Channel B: {np.mean(periods_b_us):.3f} ± {np.std(periods_b_us):.3f} µs [min={np.min(periods_b_us):.3f}, max={np.max(periods_b_us):.3f}]")
        ax2.legend(loc='upper right')
        ax2.grid(True, alpha=0.3)
    else:
        fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)

        # Period vs time for both channels
        ax1 = axes[0]
        ax1.plot(time_a, periods_a_us, 'b.', markersize=1, alpha=0.5, label='Channel A')
        ax1.plot(time_b, periods_b_us, 'r.', markersize=1, alpha=0.5, label='Channel B')
        ax1.axhline(nominal_period_us, color='green', linestyle='--', alpha=0.7, label=f'Nominal ({nominal_freq} Hz)')
        ax1.set_ylabel("Period (µs)")
        ax1.set_title("Pulse Period vs Time")
        ax1.legend(loc='upper right')
        ax1.grid(True, alpha=0.3)

        # Period difference (B - A) to show skew
        n = min(len(periods_a), len(periods_b))
        period_diff_ns = (periods_b[:n] - periods_a[:n]) * 1e9
        time_diff = time_a[:n]

        ax2 = axes[1]
        ax2.plot(time_diff, period_diff_ns, 'purple', linewidth=0.5, alpha=0.7)
        ax2.axhline(0, color='gray', linestyle='--', alpha=0.5)
        ax2.set_xlabel("Time (s)")
        ax2.set_ylabel("Period diff B-A (ns)")
        ax2.set_title(f"Period Difference (mean: {np.mean(period_diff_ns):.2f} ns)")
        ax2.grid(True, alpha=0.3)

    plt.tight_layout()

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        plt.close()
    else:
        plt.show()


def plot_ftm_timeseries(
    ftm_data: list,
    value_type: str,
    capture_start: Optional[datetime] = None,
) -> Optional[str]:
    """
    Plot FTM values over time and return as base64 encoded image.

    Args:
        ftm_data: List of FTM data dicts (each with 'sessions', 'label')
        value_type: 'rtt' for RTT or 'rssi' for RSSI
        capture_start: Optional capture start time for relative timestamps

    Returns:
        Base64 encoded PNG image data, or None if no data
    """
    import matplotlib.pyplot as plt
    import io

    # Collect data points from all slaves
    all_times = []
    all_values = []
    all_labels = []

    for ftm in ftm_data:
        sessions = ftm.get('sessions', [])
        label = ftm.get('label', 'Unknown')
        start = ftm.get('capture_start') or capture_start

        for s in sessions:
            if not s.success or s.timestamp is None:
                continue

            if value_type == 'rtt' and s.rtt_avg_ns is not None:
                value = s.rtt_avg_ns
            elif value_type == 'rssi' and s.rssi_avg is not None:
                value = s.rssi_avg
            else:
                continue

            # Calculate relative time from capture start
            if start:
                rel_time = (s.timestamp - start).total_seconds()
            else:
                rel_time = s.timestamp.timestamp()

            all_times.append(rel_time)
            all_values.append(value)
            all_labels.append(label)

    if not all_values:
        return None

    # Create plot
    fig, ax = plt.subplots(figsize=(10, 4))

    # Group by label for different colors
    unique_labels = sorted(set(all_labels))
    colors = plt.cm.tab10.colors

    for i, label in enumerate(unique_labels):
        mask = [l == label for l in all_labels]
        times = [t for t, m in zip(all_times, mask) if m]
        values = [v for v, m in zip(all_values, mask) if m]
        color = colors[i % len(colors)]
        ax.plot(times, values, '.', markersize=4, alpha=0.7, color=color, label=label)

    # Add 1-sigma shading (global mean/std across all data)
    if len(all_values) > 1:
        mean_val = np.mean(all_values)
        std_val = np.std(all_values)
        time_min, time_max = min(all_times), max(all_times)
        ax.axhline(mean_val, color='gray', linestyle='--', alpha=0.5, linewidth=1)
        ax.fill_between([time_min, time_max],
                        mean_val - std_val, mean_val + std_val,
                        alpha=0.15, color='gray', label=f'±1σ ({std_val:.1f})')

    ax.set_xlabel("Time (s)")
    if value_type == 'rtt':
        ax.set_ylabel("RTT (ns)")
        ax.set_title("FTM Round-Trip Time")
    else:
        ax.set_ylabel("RSSI (dBm)")
        ax.set_title("FTM Signal Strength")

    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    # Convert to base64
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    plt.close()
    buf.seek(0)
    img_data = base64.b64encode(buf.read()).decode('utf-8')
    return f'data:image/png;base64,{img_data}'


def plot_ftm_histogram(
    ftm_data: list,
    value_type: str,
    capture_start: Optional[datetime] = None,
) -> Optional[str]:
    """
    Plot FTM value histogram and return as base64 encoded image.

    Args:
        ftm_data: List of FTM data dicts (each with 'sessions', 'label')
        value_type: 'rtt' for RTT or 'rssi' for RSSI
        capture_start: Optional capture start time (unused, for API consistency)

    Returns:
        Base64 encoded PNG image data, or None if no data
    """
    import matplotlib.pyplot as plt
    import io

    # Collect values from all slaves
    all_values = []
    labels = []

    for ftm in ftm_data:
        sessions = ftm.get('sessions', [])
        label = ftm.get('label', 'Unknown')

        for s in sessions:
            if not s.success:
                continue

            if value_type == 'rtt' and s.rtt_avg_ns is not None:
                all_values.append(s.rtt_avg_ns)
                labels.append(label)
            elif value_type == 'rssi' and s.rssi_avg is not None:
                all_values.append(s.rssi_avg)
                labels.append(label)

    if not all_values:
        return None

    # Create histogram
    fig, ax = plt.subplots(figsize=(8, 4))

    # Group by label for stacked histogram
    unique_labels = sorted(set(labels))
    colors = plt.cm.tab10.colors

    data_by_label = []
    for label in unique_labels:
        data_by_label.append([v for v, l in zip(all_values, labels) if l == label])

    ax.hist(data_by_label, bins=50, stacked=True, alpha=0.8,
            color=[colors[i % len(colors)] for i in range(len(unique_labels))],
            label=unique_labels, edgecolor='white')

    # Add mean/std lines
    mean_val = np.mean(all_values)
    std_val = np.std(all_values)
    ax.axvline(mean_val, color='red', linestyle='--', linewidth=2, label=f'Mean: {mean_val:.1f}')
    ax.axvline(mean_val - std_val, color='orange', linestyle=':', alpha=0.7)
    ax.axvline(mean_val + std_val, color='orange', linestyle=':', alpha=0.7, label=f'±1σ: {std_val:.1f}')

    if value_type == 'rtt':
        ax.set_xlabel("RTT (ns)")
        ax.set_title("RTT Distribution")
    else:
        ax.set_xlabel("RSSI (dBm)")
        ax.set_title("RSSI Distribution")

    ax.set_ylabel("Count")
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    # Convert to base64
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    plt.close()
    buf.seek(0)
    img_data = base64.b64encode(buf.read()).decode('utf-8')
    return f'data:image/png;base64,{img_data}'


def render_ftm_table(ftm_data: list) -> str:
    """
    Generate HTML table for FTM statistics.

    Args:
        ftm_data: List of FTM data dicts (each with 'stats', 'label')

    Returns:
        HTML string for the table
    """
    if not ftm_data:
        return ""

    html = '''<table class="ftm-table">
    <tr>
        <th>Device</th>
        <th>Sessions</th>
        <th>Success</th>
        <th>RTT Mean</th>
        <th>RTT Std</th>
        <th>RTT Range</th>
        <th>RSSI Mean</th>
        <th>RSSI Range</th>
    </tr>'''

    for ftm in ftm_data:
        stats = ftm.get('stats', {})
        label = ftm.get('label', 'Unknown')
        session_count = stats.get('count', 0)
        success_rate = stats.get('success_rate', 0) * 100

        rtt_mean = stats.get('rtt_mean_ns', 0)
        rtt_std = stats.get('rtt_std_ns', 0)
        rtt_min = stats.get('rtt_min_ns', 0)
        rtt_max = stats.get('rtt_max_ns', 0)

        rssi_mean = stats.get('rssi_mean', 0)
        rssi_min = stats.get('rssi_min', 0)
        rssi_max = stats.get('rssi_max', 0)

        html += f'''
    <tr>
        <td>{label}</td>
        <td>{session_count}</td>
        <td>{success_rate:.1f}%</td>
        <td>{rtt_mean:.1f} ns</td>
        <td>{rtt_std:.1f} ns</td>
        <td>[{rtt_min:.0f}, {rtt_max:.0f}]</td>
        <td>{rssi_mean:.1f} dBm</td>
        <td>[{rssi_min}, {rssi_max}]</td>
    </tr>'''

    html += '''
</table>'''
    return html


def generate_report(
    delays_seconds: np.ndarray,
    output_dir: str | Path,
    sample_rate: float,
    times_a: Optional[np.ndarray] = None,
    pulse_freq: Optional[float] = None,
    metadata: Optional[dict] = None,
) -> JitterStats:
    """
    Generate complete analysis report with CSV, plots, and summary.

    Args:
        delays_seconds: Array of time delays in seconds
        output_dir: Directory to save all outputs
        sample_rate: Sample rate in Hz
        times_a: Optional edge times from channel A
        pulse_freq: Optional pulse frequency for phase calculations
        metadata: Optional additional metadata

    Returns:
        JitterStats object with computed statistics
    """
    from .stats import compute_stats

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Compute stats
    stats = compute_stats(delays_seconds, pulse_freq)

    # Save CSV
    save_csv(delays_seconds, output_dir / "delays.csv", times_a, sample_rate)

    # Save summary JSON
    full_metadata = metadata or {}
    full_metadata['sample_rate'] = sample_rate
    if pulse_freq:
        full_metadata['pulse_freq'] = pulse_freq
    save_summary(stats, output_dir / "summary.json", full_metadata)

    # Generate plots
    plot_histogram(delays_seconds, output_dir / "histogram.png")
    plot_timeseries(delays_seconds, sample_rate, times_a, output_dir / "timeseries.png")

    # Print summary to console
    print(stats)

    return stats


def generate_html_report(
    output_dir: str | Path,
    jitter_stats: JitterStats,
    period_stats_a: Optional[dict] = None,
    period_stats_b: Optional[dict] = None,
    frequency_skew_ppm: Optional[float] = None,
    frequency_skew_ns_per_sec: Optional[float] = None,
    metadata: Optional[dict] = None,
    ftm_data: Optional[list] = None,
) -> Path:
    """
    Generate an HTML report with all plots and statistics on one page.

    Args:
        output_dir: Directory containing plots (and where report.html will be saved)
        jitter_stats: JitterStats object
        period_stats_a: Optional period stats for channel A
        period_stats_b: Optional period stats for channel B
        frequency_skew_ppm: Optional frequency skew in PPM
        frequency_skew_ns_per_sec: Optional drift rate in ns/s
        metadata: Optional metadata dict
        ftm_data: Optional list of FTM log data dicts (one per slave)

    Returns:
        Path to generated HTML file
    """
    output_dir = Path(output_dir)

    def embed_image(filename: str) -> str:
        """Embed image as base64 data URI."""
        img_path = output_dir / filename
        if not img_path.exists():
            return ""
        with open(img_path, 'rb') as f:
            data = base64.b64encode(f.read()).decode('utf-8')
        return f'data:image/png;base64,{data}'

    # Build HTML
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    input_file = metadata.get('input_file', 'Unknown') if metadata else 'Unknown'
    detection_method = metadata.get('detection_method', 'Unknown') if metadata else 'Unknown'

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FTS-QA Analysis Report</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; }}
        .header {{ background: #fff; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 20px; }}
        .stats-card {{ background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stats-card h3 {{ margin-top: 0; color: #2980b9; }}
        .stat-row {{ display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #eee; }}
        .stat-label {{ color: #666; }}
        .stat-value {{ font-weight: 600; font-family: 'SF Mono', Monaco, monospace; }}
        .plot-container {{ background: #fff; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .plot-container img {{ width: 100%; height: auto; }}
        .plot-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; }}
        .metadata {{ font-size: 0.9em; color: #666; }}
        .highlight {{ background: #e8f4f8; padding: 2px 6px; border-radius: 3px; }}
        .input-highlight {{
            background: #e3f2fd;
            border: 2px solid #2196f3;
            border-radius: 8px;
            padding: 15px 20px;
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 1.1em;
            margin: 15px 0;
            word-break: break-all;
        }}
        .ftm-table {{
            width: 100%;
            border-collapse: collapse;
            background: #fff;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .ftm-table th, .ftm-table td {{
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        .ftm-table th {{
            background: #2980b9;
            color: white;
            font-weight: 600;
        }}
        .ftm-table tr:hover {{
            background: #f5f5f5;
        }}
        .ftm-table td {{
            font-family: 'SF Mono', Monaco, monospace;
        }}
        .edge-breakdown {{
            background: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .edge-breakdown h3 {{
            color: #2980b9;
            margin-top: 15px;
            margin-bottom: 10px;
        }}
        .edge-breakdown h3:first-child {{
            margin-top: 0;
        }}
        .channel-group {{
            background: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>FTS-QA Analysis Report</h1>
        <div class="input-highlight">{input_file}</div>
        <p class="metadata">
            <strong>Duration:</strong> {metadata.get('duration_s', 0):.1f}s |
            <strong>Detection:</strong> {detection_method} |
            <strong>Generated:</strong> {timestamp}
        </p>
    </div>
'''

    # FTM Statistics section (moved up, includes table + plots)
    if ftm_data:
        html += '''
    <h2>FTM Statistics</h2>
'''
        html += render_ftm_table(ftm_data)

        # FTM plots (timeseries with 1-sigma + histograms)
        rtt_plot = plot_ftm_timeseries(ftm_data, 'rtt')
        rssi_plot = plot_ftm_timeseries(ftm_data, 'rssi')
        rtt_hist = plot_ftm_histogram(ftm_data, 'rtt')
        rssi_hist = plot_ftm_histogram(ftm_data, 'rssi')

        if rtt_plot or rtt_hist:
            html += '''
    <h3>RTT Analysis</h3>
    <div class="plot-row">
'''
            if rtt_plot:
                html += f'''        <div class="plot-container">
            <img src="{rtt_plot}" alt="FTM RTT Timeseries">
        </div>
'''
            if rtt_hist:
                html += f'''        <div class="plot-container">
            <img src="{rtt_hist}" alt="FTM RTT Histogram">
        </div>
'''
            html += '''    </div>
'''

        if rssi_plot or rssi_hist:
            html += '''
    <h3>RSSI Analysis</h3>
    <div class="plot-row">
'''
            if rssi_plot:
                html += f'''        <div class="plot-container">
            <img src="{rssi_plot}" alt="FTM RSSI Timeseries">
        </div>
'''
            if rssi_hist:
                html += f'''        <div class="plot-container">
            <img src="{rssi_hist}" alt="FTM RSSI Histogram">
        </div>
'''
            html += '''    </div>
'''

    # Edge Processing section (moved up)
    edge_stats = metadata.get('edge_stats') if metadata else None
    if edge_stats:
        skip_seconds = edge_stats.get('skip_seconds', 0)
        total_ref = edge_stats.get('total_ref', 0)
        total_target = edge_stats.get('total_target', 0)
        filtered_ref = edge_stats.get('filtered_ref', 0)
        filtered_target = edge_stats.get('filtered_target', 0)
        matched = edge_stats.get('matched', 0)
        rejected = edge_stats.get('rejected', 0)

        # Calculate skipped edges
        ref_skipped = total_ref - filtered_ref if total_ref > filtered_ref else 0
        target_skipped = total_target - filtered_target if total_target > filtered_target else 0

        html += f'''
    <h2>Edge Processing</h2>
    <div class="edge-breakdown">
        <h3>Reference Channel</h3>
        <div class="stat-row"><span class="stat-label">Total edges detected</span><span class="stat-value">{total_ref:,}</span></div>
        <div class="stat-row"><span class="stat-label">After edge-type filter</span><span class="stat-value">{filtered_ref:,}</span></div>
'''
        if skip_seconds > 0:
            html += f'''        <div class="stat-row"><span class="stat-label">Skipped (alignment)</span><span class="stat-value">{skip_seconds:.3f}s</span></div>
'''
        html += f'''
        <h3>Target Channel</h3>
        <div class="stat-row"><span class="stat-label">Total edges detected</span><span class="stat-value">{total_target:,}</span></div>
        <div class="stat-row"><span class="stat-label">After edge-type filter</span><span class="stat-value">{filtered_target:,}</span></div>

        <h3>Matching Results</h3>
        <div class="stat-row"><span class="stat-label">Successfully paired</span><span class="stat-value">{matched:,}</span></div>
        <div class="stat-row"><span class="stat-label">Rejected (out of window)</span><span class="stat-value">{rejected:,}</span></div>
        <p style="font-size: 0.85em; color: #666; margin-top: 10px;">
            Note: Filtered edges are those matching the selected edge type (rising/falling).
            Rejected edges are pairs where the delay exceeded the maximum allowed window.
        </p>
    </div>
'''

    html += f'''
    <div class="stats-grid">
        <div class="stats-card">
            <h3>Jitter Statistics</h3>
            <div class="stat-row"><span class="stat-label">Count</span><span class="stat-value">{jitter_stats.count:,}</span></div>
            <div class="stat-row"><span class="stat-label">Mean</span><span class="stat-value">{jitter_stats.mean_ns:+.3f} ns</span></div>
            <div class="stat-row"><span class="stat-label">Std Dev</span><span class="stat-value">{jitter_stats.std_ns:.3f} ns</span></div>
            <div class="stat-row"><span class="stat-label">Min</span><span class="stat-value">{jitter_stats.min_ns:+.3f} ns</span></div>
            <div class="stat-row"><span class="stat-label">Max</span><span class="stat-value">{jitter_stats.max_ns:+.3f} ns</span></div>
            <div class="stat-row"><span class="stat-label">P50</span><span class="stat-value">±{jitter_stats.p50_ns:.3f} ns</span></div>
            <div class="stat-row"><span class="stat-label">P95</span><span class="stat-value">±{jitter_stats.p95_ns:.3f} ns</span></div>
            <div class="stat-row"><span class="stat-label">P99</span><span class="stat-value">±{jitter_stats.p99_ns:.3f} ns</span></div>
            <div class="stat-row"><span class="stat-label">P99.9</span><span class="stat-value">±{jitter_stats.p999_ns:.3f} ns</span></div>
'''

    if jitter_stats.phase_mean_deg is not None:
        html += f'''            <div class="stat-row"><span class="stat-label">Phase Mean</span><span class="stat-value">{jitter_stats.phase_mean_deg:+.4f}°</span></div>
            <div class="stat-row"><span class="stat-label">Phase Std</span><span class="stat-value">{jitter_stats.phase_std_deg:.4f}°</span></div>
'''

    html += '''        </div>
'''

    # Frequency Skew card (period stats moved to Period Analysis section)
    if frequency_skew_ppm is not None:
        html += f'''        <div class="stats-card">
            <h3>Frequency Skew</h3>
            <div class="stat-row"><span class="stat-label">Skew (B vs A)</span><span class="stat-value highlight">{frequency_skew_ppm:+.4f} ppm</span></div>
            <div class="stat-row"><span class="stat-label">Drift Rate</span><span class="stat-value highlight">{frequency_skew_ns_per_sec:+.1f} ns/s</span></div>
        </div>
'''

    html += '''    </div>

    <h2>Delay Analysis</h2>
    <div class="plot-row">
'''

    # Embed plots
    timeseries_data = embed_image('timeseries.png')
    if timeseries_data:
        html += f'''        <div class="plot-container">
            <h3>Delay vs Time</h3>
            <img src="{timeseries_data}" alt="Delay Timeseries">
        </div>
'''

    histogram_data = embed_image('histogram.png')
    if histogram_data:
        html += f'''        <div class="plot-container">
            <h3>Delay Distribution</h3>
            <img src="{histogram_data}" alt="Delay Histogram">
        </div>
'''

    html += '''    </div>

    <h2>Period Analysis</h2>
'''

    # Combined view
    periods_data = embed_image('periods.png')
    period_hist_data = embed_image('period_histogram.png')
    if periods_data or period_hist_data:
        html += '''    <h3>Combined View</h3>
    <div class="plot-row">
'''
        if periods_data:
            html += f'''        <div class="plot-container">
            <img src="{periods_data}" alt="Period Timeseries">
        </div>
'''
        if period_hist_data:
            html += f'''        <div class="plot-container">
            <img src="{period_hist_data}" alt="Period Histogram">
        </div>
'''
        html += '''    </div>
'''

    # Per-channel analysis with stats
    periods_split_data = embed_image('periods_split.png')
    period_hist_split_data = embed_image('period_histogram_split.png')

    # Channel A subgroup
    if period_stats_a:
        html += f'''
    <h3>Channel A (Reference)</h3>
    <div class="channel-group">
        <div class="stats-grid" style="margin-bottom: 15px;">
            <div class="stats-card">
                <div class="stat-row"><span class="stat-label">Mean Period</span><span class="stat-value">{period_stats_a['mean_us']:.3f} µs</span></div>
                <div class="stat-row"><span class="stat-label">Std Dev</span><span class="stat-value">{period_stats_a['std_us']:.3f} µs</span></div>
                <div class="stat-row"><span class="stat-label">Range</span><span class="stat-value">[{period_stats_a.get('min_us', 0):.3f}, {period_stats_a.get('max_us', 0):.3f}] µs</span></div>
                <div class="stat-row"><span class="stat-label">Frequency</span><span class="stat-value">{period_stats_a['freq_hz']:.6f} Hz</span></div>
                <div class="stat-row"><span class="stat-label">Error from Nominal</span><span class="stat-value">{period_stats_a['freq_ppm_error']:+.1f} ppm</span></div>
            </div>
        </div>
    </div>
'''

    # Channel B subgroup
    if period_stats_b:
        html += f'''
    <h3>Channel B (Target)</h3>
    <div class="channel-group">
        <div class="stats-grid" style="margin-bottom: 15px;">
            <div class="stats-card">
                <div class="stat-row"><span class="stat-label">Mean Period</span><span class="stat-value">{period_stats_b['mean_us']:.3f} µs</span></div>
                <div class="stat-row"><span class="stat-label">Std Dev</span><span class="stat-value">{period_stats_b['std_us']:.3f} µs</span></div>
                <div class="stat-row"><span class="stat-label">Range</span><span class="stat-value">[{period_stats_b.get('min_us', 0):.3f}, {period_stats_b.get('max_us', 0):.3f}] µs</span></div>
                <div class="stat-row"><span class="stat-label">Frequency</span><span class="stat-value">{period_stats_b['freq_hz']:.6f} Hz</span></div>
                <div class="stat-row"><span class="stat-label">Error from Nominal</span><span class="stat-value">{period_stats_b['freq_ppm_error']:+.1f} ppm</span></div>
            </div>
        </div>
    </div>
'''

    # Split view plots
    if periods_split_data or period_hist_split_data:
        html += '''    <h3>Per-Channel Distributions</h3>
    <div class="plot-row">
'''
        if periods_split_data:
            html += f'''        <div class="plot-container">
            <img src="{periods_split_data}" alt="Period Timeseries Split">
        </div>
'''
        if period_hist_split_data:
            html += f'''        <div class="plot-container">
            <img src="{period_hist_split_data}" alt="Period Histogram Split">
        </div>
'''
        html += '''    </div>
'''

    # Add pulse waveforms section if available
    pulses_data = embed_image('pulses.png')
    if pulses_data:
        html += f'''
    <h2>Signal Waveforms</h2>
    <div class="plot-container" style="max-width: 900px;">
        <h3>Sample Pulses</h3>
        <img src="{pulses_data}" alt="Sample Pulses">
    </div>
'''

    html += '''</body>
</html>
'''

    # Write HTML file
    report_path = output_dir / 'report.html'
    with open(report_path, 'w') as f:
        f.write(html)

    return report_path
