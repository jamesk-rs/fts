#!/usr/bin/env python3
"""
FTS-QA Command Line Interface

Commands (all use working directory as positional argument):

    capture <dir>         - Capture raw data from USRP RX
                            Outputs: capture.bin, capture.json
    capture-edges <dir>   - Capture and detect edges in real-time
                            Outputs: edges_ch0.bin, edges_ch1.bin, edges_meta.json
    analyze <dir>         - Analyze captured data (batch mode, loads entire file)
                            Reads: capture.bin; Outputs: reports in same dir or -o <dir>
    analyze-edges <dir>   - Analyze edge files (streaming, memory-efficient)
                            Reads: edges_*.bin; Outputs: reports in same dir or -o <dir>
    stream                - Continuous capture with live jitter feedback (no files)
    stream-mqtt           - Continuous capture with MQTT publishing to RL engine
    generate              - Generate test signals on USRP TX
"""

import warnings
warnings.filterwarnings("ignore", message="A NumPy version")

import argparse
import sys
import numpy as np
from pathlib import Path


def cmd_analyze(args):
    """Analyze a captured .cfile"""
    from detect import detect_crossings, detect_edges_linreg_dual
    from analyze import match_edges, generate_report
    from analyze.stats import compute_periods, compute_period_stats, compute_frequency_skew
    from analyze.report import plot_periods, plot_period_histogram, generate_html_report, plot_pulses
    import json

    input_dir = Path(args.directory)
    if not input_dir.exists():
        print(f"Error: Directory not found: {input_dir}")
        return 1

    # Look for capture.bin in the directory
    input_path = input_dir / "capture.bin"
    if not input_path.exists():
        print(f"Error: capture.bin not found in {input_dir}")
        return 1

    # Load metadata if available
    metadata_file = input_dir / "capture.json"
    if metadata_file.exists():
        with open(metadata_file) as f:
            capture_meta = json.load(f)
        # Use metadata values as defaults
        if 'sample_rate' in capture_meta and args.sample_rate == 10e6:
            args.sample_rate = capture_meta['sample_rate']

    print(f"Loading {input_path}...")
    data = np.fromfile(input_path, dtype=np.complex64)

    # Skip initial samples
    skip_samples = int(args.skip * args.sample_rate)
    if skip_samples > 0:
        if skip_samples >= len(data):
            print(f"Error: Skip ({args.skip}s) exceeds file duration")
            return 1
        data = data[skip_samples:]
        print(f"Skipped first {args.skip}s ({skip_samples:,} samples)")

    # Extract channels from complex I/Q
    chan_a = data.real
    chan_b = data.imag

    print(f"Loaded {len(chan_a):,} samples ({len(chan_a)/args.sample_rate:.2f} seconds)")

    # Print signal stats
    print(f"\nSignal statistics:")
    print(f"  Channel A: min={chan_a.min():.3f}, max={chan_a.max():.3f}, mean={chan_a.mean():.3f}, std={chan_a.std():.3f}")
    print(f"  Channel B: min={chan_b.min():.3f}, max={chan_b.max():.3f}, mean={chan_b.mean():.3f}, std={chan_b.std():.3f}")

    # Compute min_distance from pulse frequency
    # At 2kHz pulses and 10MSps, period = 5000 samples, so min_distance ~2500 would be safe
    # Use half period as minimum distance between same-type edges
    samples_per_period = args.sample_rate / args.pulse_freq
    min_distance = int(samples_per_period * 0.4)  # 40% of period

    algorithm = getattr(args, 'algorithm', 'crossing')

    # Detect edges using selected algorithm
    if algorithm == 'linreg':
        print(f"Detecting edges (linear regression, threshold={args.threshold}, min_distance={min_distance})...")
        # Use float64 for linreg to match streaming mode precision
        result = detect_edges_linreg_dual(
            chan_a.astype(np.float64), chan_b.astype(np.float64),
            trigger_threshold=args.threshold, min_distance=min_distance
        )
        rising_a, falling_a = result['rising_a'], result['falling_a']
        rising_b, falling_b = result['rising_b'], result['falling_b']
    else:
        print(f"Detecting edges (threshold crossing, threshold={args.threshold}, min_distance={min_distance})...")
        rising_a, falling_a = detect_crossings(chan_a, threshold=args.threshold, min_distance=min_distance)
        rising_b, falling_b = detect_crossings(chan_b, threshold=args.threshold, min_distance=min_distance)

    print(f"  Channel A: {len(rising_a)} rising, {len(falling_a)} falling")
    print(f"  Channel B: {len(rising_b)} rising, {len(falling_b)} falling")

    # Select which edge type to analyze
    if args.edge_type == 'rising':
        times_a, times_b = rising_a, rising_b
    elif args.edge_type == 'falling':
        times_a, times_b = falling_a, falling_b
    else:  # both - use falling (positive threshold crossing is cleaner)
        times_a, times_b = falling_a, falling_b

    if len(times_a) == 0 or len(times_b) == 0:
        print("Error: No edges detected. Try lowering --threshold.")
        return 1

    # Match edges and compute delays
    print("Matching edges...")
    matched_a, matched_b, delays = match_edges(times_a, times_b, args.sample_rate, pulse_freq=args.pulse_freq)
    print(f"  Matched {len(delays)} edge pairs")

    # Compute period/frequency analysis
    print("\nPeriod analysis...")
    periods_a = compute_periods(times_a, args.sample_rate)
    periods_b = compute_periods(times_b, args.sample_rate)

    stats_a = compute_period_stats(periods_a, args.pulse_freq)
    stats_b = compute_period_stats(periods_b, args.pulse_freq)
    skew_ppm, skew_ns_per_sec = compute_frequency_skew(periods_a, periods_b)

    print(f"  Channel A: {stats_a}")
    print(f"  Channel B: {stats_b}")
    print(f"  Frequency skew: {skew_ppm:+.3f} ppm ({skew_ns_per_sec:+.1f} ns/s drift)")

    # Generate report
    output_dir = Path(args.output) if args.output else input_dir

    metadata = {
        'input_file': str(input_path),
        'edge_type': args.edge_type,
        'threshold': args.threshold,
        'min_distance': min_distance,
        'detection_method': 'linear-regression' if algorithm == 'linreg' else 'threshold-crossing',
        'period_stats_a': {
            'mean_us': stats_a.mean_us,
            'std_us': stats_a.std_us,
            'freq_hz': stats_a.freq_hz,
            'freq_ppm_error': stats_a.freq_ppm_error,
        },
        'period_stats_b': {
            'mean_us': stats_b.mean_us,
            'std_us': stats_b.std_us,
            'freq_hz': stats_b.freq_hz,
            'freq_ppm_error': stats_b.freq_ppm_error,
        },
        'frequency_skew_ppm': skew_ppm,
        'frequency_skew_ns_per_sec': skew_ns_per_sec,
    }

    print(f"\nGenerating report in {output_dir}...")
    stats = generate_report(
        delays,
        output_dir,
        args.sample_rate,
        matched_a,  # Use matched edge times, not all times_a
        args.pulse_freq,
        metadata,
    )

    # Generate period plots (combined and split views)
    plot_periods(periods_a, periods_b, args.sample_rate, times_a, times_b,
                 output_dir / "periods.png", args.pulse_freq)
    plot_period_histogram(periods_a, periods_b, output_dir / "period_histogram.png",
                          args.pulse_freq)
    plot_periods(periods_a, periods_b, args.sample_rate, times_a, times_b,
                 output_dir / "periods_split.png", args.pulse_freq, split=True)
    plot_period_histogram(periods_a, periods_b, output_dir / "period_histogram_split.png",
                          args.pulse_freq, split=True)

    # Generate pulse waveform plots (sample pulses from start, middle, end)
    # Edge times need to add skip offset to map back to original file positions
    plot_pulses(input_path, args.sample_rate, times_a + skip_samples, output_dir / 'pulses.png',
                threshold=args.threshold)

    # Generate HTML report
    html_path = generate_html_report(
        output_dir,
        stats,
        period_stats_a=metadata['period_stats_a'],
        period_stats_b=metadata['period_stats_b'],
        frequency_skew_ppm=skew_ppm,
        frequency_skew_ns_per_sec=skew_ns_per_sec,
        metadata=metadata,
    )

    print(f"\nDone. Results saved to {output_dir}/")
    print(f"Open {html_path} in browser for full report")
    return 0


def cmd_capture(args):
    """Capture data from USRP RX (192.168.10.2 with GPSDO)"""
    from capture.usrp import USRPCapture
    import json

    output_dir = Path(args.directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "capture.bin"
    metadata_file = output_dir / "capture.json"

    print(f"Capturing {args.samples:,.0f} samples to {output_dir}/")

    capture = USRPCapture(
        sample_rate=args.sample_rate,
        freq=args.freq,
        gain=args.gain,
    )

    capture.capture(int(args.samples), str(output_file))

    # Write metadata
    metadata = {
        'sample_rate': args.sample_rate,
        'samples': int(args.samples),
        'freq': args.freq,
        'gain': args.gain,
    }
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)

    print("Done.")
    return 0


def cmd_stream_mqtt(args):
    """Continuous capture with live MQTT publishing and minute-aligned stats."""
    from capture.usrp import USRPCapture
    from analyze.processor import ChunkProcessor
    from sdr_publisher import SDRPublisher
    import time

    duration_str = f"{args.duration}s" if args.duration else "indefinite (Ctrl+C to stop)"
    print(f"Streaming capture with MQTT publishing (GPSDO timestamps)...")
    print(f"  Duration: {duration_str}")
    print(f"  Sample rate: {args.sample_rate/1e6:.1f} MSps")
    print(f"  Pulse freq: {args.pulse_freq} Hz")
    print(f"  Threshold: {args.threshold}")
    print(f"  MQTT broker: {args.mqtt_host}:{args.mqtt_port}")

    # Connect to MQTT broker
    try:
        publisher = SDRPublisher(args.mqtt_host, args.mqtt_port)
    except ConnectionError as e:
        print(f"Error: {e}")
        return 1

    # Edge publishing callback (called from main thread for real-time publishing)
    edges_published = [0]

    def on_edge(gpsdo_time, delay_ns, ch_a_ns, ch_b_ns):
        if ch_a_ns is not None and ch_b_ns is not None:
            publisher.publish_edge(channel_a_ns=ch_a_ns, channel_b_ns=ch_b_ns, timestamp=gpsdo_time)
        elif ch_a_ns is not None:
            publisher.publish_edge(channel_a_ns=ch_a_ns, timestamp=gpsdo_time)
        elif ch_b_ns is not None:
            publisher.publish_edge(channel_b_ns=ch_b_ns, timestamp=gpsdo_time)
        edges_published[0] += 1

    # Minute stats callback (called from processing thread)
    stats_published = [0]

    def on_minute_stats(bucket, stats):
        channel_stats = {
            'channel_a_edges': len(bucket.edges_a),
            'channel_b_edges': len(bucket.edges_b),
            'matched_count': stats.count,
        }
        publisher.publish_stats(stats.to_dict(), processor._overflow_count, channel_stats)
        stats_published[0] += 1

        print(f"[MINUTE {bucket.minute_str}] {stats.count} matched | "
              f"mean={stats.mean_ns:+.1f}ns std={stats.std_ns:.1f}ns "
              f"p50={stats.p50_ns:.1f}ns p99={stats.p99_ns:.1f}ns")

    processor = ChunkProcessor(
        sample_rate=args.sample_rate,
        pulse_freq=args.pulse_freq,
        threshold=args.threshold,
        on_minute_stats=on_minute_stats,
        on_edge=on_edge,
    )

    capture = USRPCapture(
        sample_rate=args.sample_rate,
        freq=args.freq,
        gain=args.gain,
        addr=args.usrp_addr,
    )

    # Timing for 10s status reports
    start_time = time.time()
    next_report_time = start_time + 10.0
    last_status = processor.get_status()

    def process_chunk(data, chunk_time):
        nonlocal next_report_time, last_status

        processor.process(data, chunk_time)
        processor.set_overflow_count(capture._overflow_count)

        # Report every 10 seconds
        now = time.time()
        if now > next_report_time:
            last_status = processor.print_status(now - start_time, last_status)
            next_report_time += 10.0

        return True  # Continue streaming

    try:
        capture.stream_threaded(
            callback=process_chunk,
            chunk_samples=int(args.sample_rate * 0.01),  # 10ms chunks
            duration=args.duration,
            queue_depth=200,  # ~2 seconds buffer for processing jitter
        )
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        processor.flush()
        processor.stop()
        publisher.close()

    # Final stats
    processor.print_final_stats()
    print(f"Edges published: {edges_published[0]}")
    print(f"Stats published: {stats_published[0]}")

    return 0


def cmd_stream(args):
    """Continuous capture with live jitter analysis (per-minute stats)."""
    from capture.usrp import USRPCapture
    from analyze.processor import ChunkProcessor
    import time

    duration_str = f"{args.duration}s" if args.duration else "indefinite (Ctrl+C to stop)"
    print(f"Streaming capture with live jitter analysis...")
    print(f"  Duration: {duration_str}")
    print(f"  Sample rate: {args.sample_rate/1e6:.1f} MSps")
    print(f"  Pulse freq: {args.pulse_freq} Hz")
    print(f"  Threshold: {args.threshold}")

    # Minute stats callback (called from processing thread)
    def on_minute_stats(bucket, stats):
        print(f"[MINUTE {bucket.minute_str}] {len(bucket.edges_a)} matched | "
              f"mean={stats.mean_ns:+.1f}ns std={stats.std_ns:.1f}ns "
              f"p50={stats.p50_ns:.1f}ns p99={stats.p99_ns:.1f}ns")

    processor = ChunkProcessor(
        sample_rate=args.sample_rate,
        pulse_freq=args.pulse_freq,
        threshold=args.threshold,
        on_minute_stats=on_minute_stats,
    )

    capture = USRPCapture(
        sample_rate=args.sample_rate,
        freq=args.freq,
        gain=args.gain,
    )

    # Timing for 10s status reports
    start_time = time.time()
    next_report_time = start_time + 10.0
    last_status = processor.get_status()

    def process_chunk(data, chunk_time):
        nonlocal next_report_time, last_status

        processor.process(data, chunk_time)
        processor.set_overflow_count(capture._overflow_count)

        # Report every 10 seconds
        now = time.time()
        if now > next_report_time:
            last_status = processor.print_status(now - start_time, last_status)
            next_report_time += 10.0

        return True  # Continue streaming

    try:
        capture.stream(
            callback=process_chunk,
            chunk_samples=int(args.sample_rate * 0.01),  # 10ms chunks
            duration=args.duration,
        )
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        processor.flush()
        processor.stop()

    processor.print_final_stats()
    return 0


def cmd_generate(args):
    """Generate test signals on USRP TX (192.168.10.3)"""
    from capture.usrp import USRPTransmit
    from generate.waveforms import generate_square_pulses

    duration_str = f"{args.duration} s" if args.duration else "indefinite (Ctrl+C to stop)"
    print(f"Generating 50% duty cycle square wave:")
    print(f"  Pulse freq:  {args.pulse_freq/1e3:.1f} kHz")
    print(f"  Phase shift: {args.phase} ns")
    print(f"  Jitter std:  {args.jitter} ns")
    print(f"  Amplitude:   {args.amplitude}")
    print(f"  Duration:    {duration_str}")
    print(f"  MIMO clock:  {not args.no_mimo}")

    # Generate waveform (1 second buffer, will repeat)
    waveform_duration = min(args.duration, 1.0) if args.duration else 1.0
    waveform = generate_square_pulses(
        freq=args.pulse_freq,
        sample_rate=args.sample_rate,
        phase_shift_ns=args.phase,
        jitter_std_ns=args.jitter,
        duration=waveform_duration,
        amplitude=args.amplitude,
    )

    # Transmit
    tx = USRPTransmit(
        sample_rate=args.sample_rate,
        use_mimo_clock=not args.no_mimo,
    )
    tx.transmit_waveform(waveform, duration=args.duration, repeat=True)

    print("Done.")
    return 0


def cmd_capture_edges(args):
    """Capture from USRP and detect edges, writing to per-channel binary files.

    Supports two algorithms:
    - crossing: Simple threshold crossing with linear interpolation (default, fast)
    - linreg: Linear regression on edge slope (more robust, may be slower)
    """
    from capture.usrp import USRPCapture
    from detect import StreamingCrossingDetector, StreamingLinregDetector
    from edgeio import EdgeFileWriter, write_metadata, EDGE_RISING, EDGE_FALLING
    import time

    output_dir = Path(args.directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing edge files (refuse to overwrite to prevent data corruption)
    existing_files = list(output_dir.glob('edges_ch*.bin'))
    if existing_files:
        print(f"Error: Edge files already exist in {output_dir}/")
        print(f"  Found: {', '.join(f.name for f in existing_files)}")
        print(f"  Remove existing files or use a different directory to avoid data corruption.")
        return 1

    # Compute min_distance from pulse frequency
    samples_per_period = args.sample_rate / args.pulse_freq
    min_distance = int(samples_per_period * 0.4)
    skip_samples = int(args.skip * args.sample_rate)

    algorithm = getattr(args, 'algorithm', 'crossing')

    print(f"Capturing edges to {output_dir}/")
    print(f"  Duration: {args.duration}s")
    print(f"  Sample rate: {args.sample_rate/1e6:.1f} MSps")
    print(f"  Pulse freq: {args.pulse_freq} Hz")
    print(f"  Threshold: {args.threshold}")
    print(f"  Skip: {args.skip}s ({skip_samples} samples)")
    print(f"  Min distance: {min_distance} samples")
    print(f"  Algorithm: {algorithm}")

    # Write metadata
    write_metadata(
        output_dir,
        sample_rate=args.sample_rate,
        threshold=args.threshold,
        channel_count=2,
        pulse_freq=args.pulse_freq,
        skip_samples=skip_samples,
        min_distance=min_distance,
        algorithm=algorithm,
    )

    # Initialize detectors (one per channel)
    if algorithm == 'linreg':
        detector_a = StreamingLinregDetector(
            trigger_threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
        )
        detector_b = StreamingLinregDetector(
            trigger_threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
        )
    else:  # crossing (default)
        detector_a = StreamingCrossingDetector(
            threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
        )
        detector_b = StreamingCrossingDetector(
            threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
        )

    # Open edge files (V1 format)
    writer_a = EdgeFileWriter(output_dir, channel=0)
    writer_b = EdgeFileWriter(output_dir, channel=1)

    REPORT_PERIOD = 1.0  # seconds
    start_time = time.time()
    next_report_time = start_time + REPORT_PERIOD

    last_report_edge_count_a = 0
    last_report_edge_count_b = 0

    def process_chunk(data, chunk_time):
        nonlocal start_time, next_report_time, REPORT_PERIOD
        nonlocal last_report_edge_count_a, last_report_edge_count_b

        # chunk_time is GPSDO time (not used for edge file capture)
        _ = chunk_time

        # Extract channels from complex data (float64 for edge detector precision)
        chan_a = data.real.astype(np.float64)
        chan_b = data.imag.astype(np.float64)

        # Detect edges (returns Nx2 array: [[time, type], ...])
        edges_a = detector_a.process(chan_a)
        edges_b = detector_b.process(chan_b)

        # Write edges directly (already in detection order)
        for t, edge_type in edges_a:
            writer_a.write_edge(float(t), int(edge_type))
        for t, edge_type in edges_b:
            writer_b.write_edge(float(t), int(edge_type))

        # Periodic report
        now = time.time()
        if now > next_report_time:
            elapsed = now - start_time
            samples = detector_a.samples_processed
            print(f"[{elapsed:5.1f}s] {samples/1e6:.1f}M samples, "
                  f"edges: A={writer_a.edge_count} (+{last_report_edge_count_a - writer_a.edge_count}), B={writer_b.edge_count} (+{last_report_edge_count_b - writer_b.edge_count})")
            next_report_time += REPORT_PERIOD
            last_report_edge_count_a = writer_a.edge_count
            last_report_edge_count_b = writer_b.edge_count

        return True  # Continue streaming

    # Start capture
    capture = USRPCapture(
        sample_rate=args.sample_rate,
        freq=args.freq,
        gain=args.gain,
    )

    try:
        capture.stream(
            callback=process_chunk,
            chunk_samples=int(args.sample_rate * 0.01),  # 10ms chunks
            duration=args.duration,
        )
    except KeyboardInterrupt:
        print("\nStopped by user.")

    writer_a.close()
    writer_b.close()

    print(f"\nCapture complete:")
    print(f"  Total samples: {detector_a.samples_processed:,}")
    print(f"  Channel A edges: {writer_a.edge_count}")
    print(f"  Channel B edges: {writer_b.edge_count}")
    print(f"  Output: {output_dir}/")

    return 0


def analyze_cfile_streaming(args):
    """Memory-bounded streaming analysis of raw .cfile"""
    from detect import StreamingLinregDetector, StreamingCrossingDetector
    from analyze.streaming import StreamingMatcher, StreamingStats, DelayFileWriter
    from analyze.report import (
        plot_histogram, plot_timeseries, generate_html_report,
        plot_periods, plot_period_histogram, plot_pulses,
    )
    from analyze.stats import JitterStats, compute_periods, compute_period_stats, compute_frequency_skew

    input_path = Path(args.directory)
    sample_rate = args.sample_rate
    pulse_freq = args.pulse_freq

    # Compute parameters
    samples_per_period = sample_rate / pulse_freq
    min_distance = int(samples_per_period * 0.4)
    skip_samples = int(args.skip * sample_rate)
    period_ns = 1e9 / pulse_freq
    max_delay_ns = period_ns * 0.1

    # Get file size to show progress
    file_size = input_path.stat().st_size
    total_samples = file_size // 8  # complex64 = 8 bytes
    duration_s = total_samples / sample_rate

    algorithm = getattr(args, 'algorithm', 'linreg')

    print(f"Streaming analysis of {input_path}")
    print(f"  File size: {file_size / 1e9:.2f} GB ({total_samples:,} samples, {duration_s:.1f}s)")
    print(f"  Sample rate: {sample_rate/1e6:.1f} MSps")
    print(f"  Pulse freq: {pulse_freq} Hz")
    print(f"  Threshold: {args.threshold}")
    print(f"  Skip: {args.skip}s ({skip_samples:,} samples)")
    print(f"  Max delay: {max_delay_ns:.0f}ns")
    print(f"  Algorithm: {algorithm}")

    # Initialize detectors (one per channel)
    # Disable settle phase to match batch mode behavior
    if algorithm == 'linreg':
        detector_a = StreamingLinregDetector(
            trigger_threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
            settle=False,
        )
        detector_b = StreamingLinregDetector(
            trigger_threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
            settle=False,
        )
    else:  # crossing
        detector_a = StreamingCrossingDetector(
            threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
            settle=False,
        )
        detector_b = StreamingCrossingDetector(
            threshold=args.threshold,
            min_distance=min_distance,
            skip_samples=skip_samples,
            settle=False,
        )

    # Initialize matcher and stats
    matcher = StreamingMatcher(sample_rate=sample_rate, max_delay_ns=max_delay_ns)
    stats = StreamingStats()

    # Accumulators for delays and edge times (for plotting)
    all_times = []
    all_delays = []
    all_edge_times_a = []
    all_edge_times_b = []

    CHUNK_SIZE = 1_000_000  # 1M samples per chunk (8MB)
    samples_processed = 0
    last_report_samples = 0

    print(f"\nProcessing...")

    with open(input_path, 'rb') as f:
        while True:
            # Read chunk of complex64 data
            chunk = np.fromfile(f, dtype=np.complex64, count=CHUNK_SIZE)
            if len(chunk) == 0:
                break

            # Detect edges in each channel (returns Nx2 array: [[time, type], ...])
            edges_a = detector_a.process(chunk.real.astype(np.float64))
            edges_b = detector_b.process(chunk.imag.astype(np.float64))

            # Use falling edges (type == 1, more reliable for AC-coupled signals)
            ref_times = edges_a[edges_a[:, 1] == 1, 0] if len(edges_a) > 0 else np.array([])
            target_times = edges_b[edges_b[:, 1] == 1, 0] if len(edges_b) > 0 else np.array([])

            # Accumulate edge times for period analysis
            if len(ref_times) > 0:
                all_edge_times_a.extend(ref_times)
            if len(target_times) > 0:
                all_edge_times_b.extend(target_times)

            # Match edges and update stats
            for result in matcher.match(ref_times, target_times):
                if not result.rejected:
                    all_times.append(result.time_samples)
                    all_delays.append(result.delay_ns)
                    stats.update(result.delay_ns)

            samples_processed += len(chunk)

            # Progress report every 10M samples
            if samples_processed - last_report_samples >= 10_000_000:
                pct = 100 * samples_processed / total_samples
                print(f"  [{pct:5.1f}%] {samples_processed/1e6:.0f}M samples, "
                      f"{stats.count} matches, {matcher.reject_count} rejected")
                last_report_samples = samples_processed

    all_times = np.array(all_times)
    all_delays = np.array(all_delays)
    all_edge_times_a = np.array(all_edge_times_a)
    all_edge_times_b = np.array(all_edge_times_b)

    print(f"\nProcessed {samples_processed:,} samples")
    print(f"  Matched: {matcher.match_count}, Rejected: {matcher.reject_count}")

    if len(all_delays) == 0:
        print("Error: No valid matches found")
        return 1

    # Print jitter statistics
    print(f"\nJitter Statistics (n={stats.count})")
    print(f"  Mean:   {stats.mean:+.3f} ns")
    print(f"  Std:    {stats.std:.3f} ns")
    print(f"  Min:    {stats.min:.3f} ns")
    print(f"  Max:    {stats.max:.3f} ns")
    print(f"  P50:    ±{stats.percentile(50):.3f} ns")
    print(f"  P95:    ±{stats.percentile(95):.3f} ns")
    print(f"  P99:    ±{stats.percentile(99):.3f} ns")
    print(f"  P99.9:  ±{stats.percentile(99.9):.3f} ns")

    # Period analysis
    print("\nPeriod analysis...")
    periods_a = compute_periods(all_edge_times_a, sample_rate)
    periods_b = compute_periods(all_edge_times_b, sample_rate)

    period_stats_a = compute_period_stats(periods_a, pulse_freq)
    period_stats_b = compute_period_stats(periods_b, pulse_freq)
    skew_ppm, skew_ns_per_sec = compute_frequency_skew(periods_a, periods_b)

    print(f"  Channel A: {period_stats_a}")
    print(f"  Channel B: {period_stats_b}")
    print(f"  Frequency skew: {skew_ppm:+.3f} ppm ({skew_ns_per_sec:+.1f} ns/s drift)")

    # Output directory
    output_dir = Path(args.output) if args.output else input_path
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save delays
    delay_file = output_dir / 'delays.bin'
    with DelayFileWriter(delay_file) as writer:
        writer.write_batch(all_times, all_delays)
    print(f"\nSaved {writer.count} delays to {delay_file}")

    # Generate delay plots
    plot_histogram(all_delays / 1e9, output_dir / 'histogram.png')
    plot_timeseries(all_delays / 1e9, sample_rate, all_times, output_dir / 'timeseries.png')

    # Generate period plots (combined and split views)
    plot_periods(periods_a, periods_b, sample_rate, all_edge_times_a, all_edge_times_b,
                 output_dir / 'periods.png', pulse_freq)
    plot_period_histogram(periods_a, periods_b, output_dir / 'period_histogram.png', pulse_freq)
    plot_periods(periods_a, periods_b, sample_rate, all_edge_times_a, all_edge_times_b,
                 output_dir / 'periods_split.png', pulse_freq, split=True)
    plot_period_histogram(periods_a, periods_b, output_dir / 'period_histogram_split.png', pulse_freq, split=True)

    # Generate pulse waveform plots (sample pulses from start, middle, end)
    # StreamingLinregDetector returns global sample indices (already accounts for skip)
    plot_pulses(input_path, sample_rate, all_edge_times_a, output_dir / 'pulses.png',
                threshold=args.threshold)

    # Create JitterStats for HTML report
    jitter_stats = JitterStats(
        count=stats.count,
        mean_ns=stats.mean,
        std_ns=stats.std,
        min_ns=stats.min,
        max_ns=stats.max,
        p50_ns=stats.percentile(50),
        p95_ns=stats.percentile(95),
        p99_ns=stats.percentile(99),
        p999_ns=stats.percentile(99.9),
    )

    # Period stats for HTML report
    period_stats_a_dict = {
        'mean_us': period_stats_a.mean_us,
        'std_us': period_stats_a.std_us,
        'min_us': period_stats_a.min_us,
        'max_us': period_stats_a.max_us,
        'freq_hz': period_stats_a.freq_hz,
        'freq_ppm_error': period_stats_a.freq_ppm_error,
    }
    period_stats_b_dict = {
        'mean_us': period_stats_b.mean_us,
        'std_us': period_stats_b.std_us,
        'min_us': period_stats_b.min_us,
        'max_us': period_stats_b.max_us,
        'freq_hz': period_stats_b.freq_hz,
        'freq_ppm_error': period_stats_b.freq_ppm_error,
    }

    # Build metadata (same format as batch analysis)
    metadata = {
        'input_file': str(input_path),
        'edge_type': args.edge_type,
        'threshold': args.threshold,
        'min_distance': min_distance,
        'detection_method': 'linear-regression' if algorithm == 'linreg' else 'threshold-crossing',
        'sample_rate': sample_rate,
        'pulse_freq': pulse_freq,
        'duration_s': duration_s,
        'period_stats_a': period_stats_a_dict,
        'period_stats_b': period_stats_b_dict,
    }

    # Generate HTML report
    html_path = generate_html_report(
        output_dir,
        jitter_stats,
        period_stats_a=period_stats_a_dict,
        period_stats_b=period_stats_b_dict,
        frequency_skew_ppm=skew_ppm,
        frequency_skew_ns_per_sec=skew_ns_per_sec,
        metadata=metadata,
    )

    print(f"Plots saved to {output_dir}/")
    print(f"Open {html_path} in browser for full report")

    return 0


def analyze_edge_files(args):
    """Analyze pre-extracted edge files."""
    from edgeio import EdgeFileReader, read_metadata, EDGE_RISING, EDGE_FALLING
    from analyze.streaming import StreamingMatcher, StreamingStats, DelayFileWriter
    from analyze.report import (
        plot_histogram, plot_timeseries, generate_html_report,
        plot_periods, plot_period_histogram,
    )
    from analyze.stats import JitterStats, compute_periods, compute_period_stats, compute_frequency_skew

    input_dir = Path(args.directory)

    # Read metadata
    try:
        meta = read_metadata(input_dir)
    except FileNotFoundError:
        print(f"Error: No edges_meta.json found in {input_dir}")
        return 1

    sample_rate = meta['sample_rate']
    pulse_freq = meta.get('pulse_freq', 2000)

    print(f"Analyzing edges from {input_dir}/")
    print(f"  Sample rate: {sample_rate/1e6:.1f} MSps")
    print(f"  Pulse freq: {pulse_freq} Hz")

    # Parse FTM logs - explicit or auto-detected
    ftm_data = []
    ftm_logs = getattr(args, 'ftm_logs', None) or []

    # Auto-detect FTM logs in input directory if none specified
    if not ftm_logs:
        for name in ['slave1.log', 'slave2.log', 'master.log']:
            auto_log = input_dir / name
            if auto_log.exists():
                ftm_logs.append(str(auto_log))

    if ftm_logs:
        from ftmio import parse_ftm_log, compute_ftm_stats
        from datetime import datetime

        # Get capture start time for alignment
        capture_start = None
        if 'start_time' in meta:
            try:
                capture_start = datetime.fromisoformat(meta['start_time'])
            except ValueError:
                pass

        for log_path in ftm_logs:
            print(f"  Loading FTM log: {log_path}")
            parsed = parse_ftm_log(log_path)
            parsed['stats'] = compute_ftm_stats(parsed['sessions'])
            parsed['capture_start'] = capture_start
            ftm_data.append(parsed)
            print(f"    {parsed['label']}: {parsed['success_count']} ok, {parsed['failure_count']} failed")

    # Initialize readers
    reader_ref = EdgeFileReader(input_dir, channel=args.ref_channel)
    reader_target = EdgeFileReader(input_dir, channel=args.target_channel)

    n_ref = reader_ref.edge_count()
    n_target = reader_target.edge_count()
    print(f"  Reference (ch{args.ref_channel}): {n_ref} edges")
    print(f"  Target (ch{args.target_channel}): {n_target} edges")

    if n_ref == 0 or n_target == 0:
        print("Error: No edges to analyze")
        return 1

    def find_edge_index_at_time(reader: EdgeFileReader, target_time: float) -> int:
        """Binary search for first edge >= target_time."""
        chunk_size = 100000
        offset = 0
        while True:
            batch = reader.read_range(offset, offset + chunk_size)
            if len(batch) == 0:
                return offset
            if batch['time'][-1] >= target_time:
                idx = np.searchsorted(batch['time'], target_time)
                return offset + idx
            offset += len(batch)

    # Auto-detect common start time (handle late-starting channels)
    edges_ref_first = reader_ref.read_range(0, 1)
    edges_target_first = reader_target.read_range(0, 1)
    start_ref = edges_ref_first[0]['time'] if len(edges_ref_first) > 0 else 0
    start_target = edges_target_first[0]['time'] if len(edges_target_first) > 0 else 0
    common_start = max(start_ref, start_target)

    skip_seconds = 0.0
    ref_start_idx = 0
    target_start_idx = 0
    if common_start > min(start_ref, start_target):
        skip_seconds = (common_start - min(start_ref, start_target)) / sample_rate
        # Find starting edge indices to ensure temporal alignment
        ref_start_idx = find_edge_index_at_time(reader_ref, common_start)
        target_start_idx = find_edge_index_at_time(reader_target, common_start)
        print(f"  Auto-skipping {skip_seconds:.3f}s to align channels")
        print(f"  Starting at: ref idx {ref_start_idx}, target idx {target_start_idx}")

    # Compute max delay threshold (10% of period)
    period_ns = 1e9 / pulse_freq
    max_delay_ns = period_ns * 0.1

    # Initialize matcher and stats
    matcher = StreamingMatcher(sample_rate=sample_rate, max_delay_ns=max_delay_ns)
    stats = StreamingStats()

    # Output directory
    output_dir = Path(args.output) if args.output else input_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Process edges
    print(f"\nMatching edges (max delay: {max_delay_ns:.0f}ns)...")

    # Select edge type
    edge_type = EDGE_FALLING if args.edge_type == 'falling' else EDGE_RISING

    # Memory-efficient streaming: read edges in batches
    # We need to process both channels together for matching
    BATCH_SIZE = 100000  # edges per batch

    # For plots: subsample to max 50k points
    MAX_PLOT_POINTS = 50000
    plot_times = []
    plot_delays = []
    plot_subsample_rate = max(1, n_ref // (2 * MAX_PLOT_POINTS))

    # For period analysis: use streaming stats (compute periods per batch)
    period_stats_a = StreamingStats()
    period_stats_b = StreamingStats()
    # Sample periods for plot (subsample to limit memory)
    periods_a_sampled = []
    periods_b_sampled = []
    edge_times_a_sampled = []  # Track edge times for period plot x-axis
    edge_times_b_sampled = []
    period_sample_rate = max(1, n_ref // (2 * MAX_PLOT_POINTS))
    # Track last edge time for continuity across batches
    last_ref_time = None
    last_target_time = None

    # Stream through edges and write delays directly to file
    delay_file = output_dir / 'delays.bin'
    match_idx = 0
    total_ref_filtered = 0
    total_target_filtered = 0

    with DelayFileWriter(delay_file) as delay_writer:
        # Read edges in batches - both channels starting from common_start time
        ref_iter = reader_ref.iter_batches(batch_size=BATCH_SIZE, start_edge=ref_start_idx)
        target_iter = reader_target.iter_batches(batch_size=BATCH_SIZE, start_edge=target_start_idx)

        # Buffers for unmatched edges across batches
        ref_buffer = np.array([], dtype=np.float64)
        target_buffer = np.array([], dtype=np.float64)

        # Track matched ref edges to avoid duplicates from buffer overlap
        matched_ref_times = set()

        for ref_batch in ref_iter:
            # Get corresponding target batch
            try:
                target_batch = next(target_iter)
            except StopIteration:
                target_batch = np.array([], dtype=reader_target.DTYPE)

            # Filter by edge type (no time filtering needed - already started at common_start)
            ref_mask = ref_batch['type'] == edge_type
            target_mask = target_batch['type'] == edge_type
            ref_times_batch = ref_batch['time'][ref_mask]
            target_times_batch = target_batch['time'][target_mask]

            total_ref_filtered += len(ref_times_batch)
            total_target_filtered += len(target_times_batch)

            # Combine with buffers
            ref_times = np.concatenate([ref_buffer, ref_times_batch]) if len(ref_buffer) > 0 else ref_times_batch
            target_times = np.concatenate([target_buffer, target_times_batch]) if len(target_buffer) > 0 else target_times_batch

            # Compute periods within this batch (and across batch boundary)
            if len(ref_times_batch) > 0:
                # Include last edge from previous batch for continuity
                if last_ref_time is not None:
                    ref_with_prev = np.concatenate([[last_ref_time], ref_times_batch])
                else:
                    ref_with_prev = ref_times_batch
                # Compute periods (in seconds)
                ref_periods = np.diff(ref_with_prev) / sample_rate
                for p in ref_periods:
                    period_stats_a.update(p * 1e6)  # Store in microseconds
                # Sample periods for plot (subsample to limit memory)
                for i, p in enumerate(ref_periods):
                    # When last_ref_time exists, ref_periods[0] is cross-boundary period
                    # with global index (batch_start - 1), and ref_periods[i] for i>=1
                    # has global index (batch_start + i - 1)
                    if last_ref_time is not None:
                        global_idx = total_ref_filtered - len(ref_times_batch) - 1 + i
                    else:
                        global_idx = total_ref_filtered - len(ref_times_batch) + i
                    if global_idx % period_sample_rate == 0:
                        periods_a_sampled.append(p)
                        # Track edge time (period goes from edge i to i+1, use edge i+1 time)
                        if i + 1 < len(ref_with_prev):
                            edge_times_a_sampled.append(ref_with_prev[i + 1])
                        else:
                            edge_times_a_sampled.append(ref_times_batch[-1])
                last_ref_time = ref_times_batch[-1]

            if len(target_times_batch) > 0:
                if last_target_time is not None:
                    target_with_prev = np.concatenate([[last_target_time], target_times_batch])
                else:
                    target_with_prev = target_times_batch
                target_periods = np.diff(target_with_prev) / sample_rate
                for p in target_periods:
                    period_stats_b.update(p * 1e6)  # Store in microseconds
                for i, p in enumerate(target_periods):
                    # When last_target_time exists, target_periods[0] is cross-boundary period
                    if last_target_time is not None:
                        global_idx = total_target_filtered - len(target_times_batch) - 1 + i
                    else:
                        global_idx = total_target_filtered - len(target_times_batch) + i
                    if global_idx % period_sample_rate == 0:
                        periods_b_sampled.append(p)
                        if i + 1 < len(target_with_prev):
                            edge_times_b_sampled.append(target_with_prev[i + 1])
                        else:
                            edge_times_b_sampled.append(target_times_batch[-1])
                last_target_time = target_times_batch[-1]

            # Match edges
            for result in matcher.match(ref_times, target_times):
                if not result.rejected:
                    # Skip if already matched (from buffer overlap)
                    if result.time_samples in matched_ref_times:
                        continue
                    matched_ref_times.add(result.time_samples)

                    delay_writer.write(result.time_samples, result.delay_ns)
                    stats.update(result.delay_ns)

                    # Subsample for plotting
                    if match_idx % plot_subsample_rate == 0:
                        plot_times.append(result.time_samples)
                        plot_delays.append(result.delay_ns)
                    match_idx += 1

            # Keep unmatched tail for next iteration (last few edges might match next batch)
            # Keep edges from the last ~1 second worth of samples
            keep_samples = sample_rate * 1.0
            if len(ref_times) > 0:
                cutoff = ref_times[-1] - keep_samples
                ref_buffer = ref_times[ref_times > cutoff]
            if len(target_times) > 0:
                cutoff = target_times[-1] - keep_samples
                target_buffer = target_times[target_times > cutoff]

        # Process any remaining target batches (for period analysis)
        for target_batch in target_iter:
            target_mask = target_batch['type'] == edge_type
            target_times_batch = target_batch['time'][target_mask]
            total_target_filtered += len(target_times_batch)

            # Compute periods for remaining target edges
            if len(target_times_batch) > 0:
                if last_target_time is not None:
                    target_with_prev = np.concatenate([[last_target_time], target_times_batch])
                else:
                    target_with_prev = target_times_batch
                target_periods = np.diff(target_with_prev) / sample_rate
                for p in target_periods:
                    period_stats_b.update(p * 1e6)
                for i, p in enumerate(target_periods):
                    # When last_target_time exists, target_periods[0] is cross-boundary period
                    if last_target_time is not None:
                        global_idx = total_target_filtered - len(target_times_batch) - 1 + i
                    else:
                        global_idx = total_target_filtered - len(target_times_batch) + i
                    if global_idx % period_sample_rate == 0:
                        periods_b_sampled.append(p)
                        if i + 1 < len(target_with_prev):
                            edge_times_b_sampled.append(target_with_prev[i + 1])
                        else:
                            edge_times_b_sampled.append(target_times_batch[-1])
                last_target_time = target_times_batch[-1]

    print(f"  Filtered edges: ref={total_ref_filtered}, target={total_target_filtered}")
    print(f"  Matched: {matcher.match_count}, Rejected: {matcher.reject_count}")

    # Convert to arrays for plotting
    all_times = np.array(plot_times)
    all_delays = np.array(plot_delays)
    periods_a = np.array(periods_a_sampled)
    periods_b = np.array(periods_b_sampled)

    if len(all_delays) == 0:
        print("Error: No valid matches found")
        return 1

    # Print statistics
    print(f"\nJitter Statistics (n={stats.count})")
    print(f"  Mean:   {stats.mean:+.3f} ns")
    print(f"  Std:    {stats.std:.3f} ns")
    print(f"  Min:    {stats.min:.3f} ns")
    print(f"  Max:    {stats.max:.3f} ns")
    print(f"  P50:    ±{stats.percentile(50):.3f} ns")
    print(f"  P95:    ±{stats.percentile(95):.3f} ns")
    print(f"  P99:    ±{stats.percentile(99):.3f} ns")
    print(f"  P99.9:  ±{stats.percentile(99.9):.3f} ns")

    print(f"\nSaved {stats.count} delays to {delay_file}")

    # Generate delay plots (using subsampled data)
    plot_histogram(all_delays / 1e9, output_dir / 'histogram.png')
    plot_timeseries(all_delays / 1e9, sample_rate, all_times, output_dir / 'timeseries.png')

    # Period analysis - compute stats from streaming accumulators
    # period_stats_a and period_stats_b are StreamingStats with values in microseconds
    nominal_period_us = 1e6 / pulse_freq

    mean_period_a_us = period_stats_a.mean
    mean_period_b_us = period_stats_b.mean
    freq_a = 1e6 / mean_period_a_us if mean_period_a_us > 0 else 0
    freq_b = 1e6 / mean_period_b_us if mean_period_b_us > 0 else 0
    ppm_error_a = (mean_period_a_us - nominal_period_us) / nominal_period_us * 1e6 if nominal_period_us > 0 else 0
    ppm_error_b = (mean_period_b_us - nominal_period_us) / nominal_period_us * 1e6 if nominal_period_us > 0 else 0

    # Frequency skew between channels
    skew_ppm = (mean_period_a_us - mean_period_b_us) / mean_period_a_us * 1e6 if mean_period_a_us > 0 else 0
    skew_ns_per_sec = skew_ppm * 1000  # 1 ppm = 1000 ns/s

    print(f"\nPeriod Analysis:")
    print(f"  Channel A: Period: {mean_period_a_us:.3f} ± {period_stats_a.std:.3f} µs ({freq_a:.6f} Hz, {-ppm_error_a:+.1f} ppm)")
    print(f"  Channel B: Period: {mean_period_b_us:.3f} ± {period_stats_b.std:.3f} µs ({freq_b:.6f} Hz, {-ppm_error_b:+.1f} ppm)")
    print(f"  Frequency skew: {skew_ppm:+.3f} ppm ({skew_ns_per_sec:+.1f} ns/s)")

    # Generate period plots (using sampled periods with actual edge times)
    edge_times_a = np.array(edge_times_a_sampled)
    edge_times_b = np.array(edge_times_b_sampled)
    # Combined (overlay) plots
    plot_periods(periods_a, periods_b, sample_rate, edge_times_a, edge_times_b,
                 output_dir / 'periods.png', pulse_freq)
    plot_period_histogram(periods_a, periods_b, output_dir / 'period_histogram.png', pulse_freq)
    # Split (separate channel) plots
    plot_periods(periods_a, periods_b, sample_rate, edge_times_a, edge_times_b,
                 output_dir / 'periods_split.png', pulse_freq, split=True)
    plot_period_histogram(periods_a, periods_b, output_dir / 'period_histogram_split.png', pulse_freq, split=True)

    # Create JitterStats for HTML report
    jitter_stats = JitterStats(
        count=stats.count,
        mean_ns=stats.mean,
        std_ns=stats.std,
        min_ns=stats.min,
        max_ns=stats.max,
        p50_ns=stats.percentile(50),
        p95_ns=stats.percentile(95),
        p99_ns=stats.percentile(99),
        p999_ns=stats.percentile(99.9),
    )

    # Period stats dicts for HTML report
    period_stats_a_dict = {
        'mean_us': mean_period_a_us,
        'std_us': period_stats_a.std,
        'min_us': period_stats_a.min,
        'max_us': period_stats_a.max,
        'freq_hz': freq_a,
        'freq_ppm_error': -ppm_error_a,  # Negative because longer period = lower freq
    }
    period_stats_b_dict = {
        'mean_us': mean_period_b_us,
        'std_us': period_stats_b.std,
        'min_us': period_stats_b.min,
        'max_us': period_stats_b.max,
        'freq_hz': freq_b,
        'freq_ppm_error': -ppm_error_b,
    }

    # Build metadata
    duration_s = last_ref_time / sample_rate if last_ref_time is not None else 0
    metadata = {
        'input_file': str(input_dir),
        'edge_type': args.edge_type,
        'threshold': meta.get('threshold', 0.4),
        'detection_method': 'threshold-crossing',
        'sample_rate': sample_rate,
        'pulse_freq': pulse_freq,
        'duration_s': duration_s,
        'period_stats_a': period_stats_a_dict,
        'period_stats_b': period_stats_b_dict,
        'edge_stats': {
            'total_ref': n_ref,
            'total_target': n_target,
            'skip_seconds': skip_seconds,
            'filtered_ref': total_ref_filtered,
            'filtered_target': total_target_filtered,
            'matched': matcher.match_count,
            'rejected': matcher.reject_count,
        },
    }

    # Generate HTML report
    html_path = generate_html_report(
        output_dir,
        jitter_stats,
        period_stats_a=period_stats_a_dict,
        period_stats_b=period_stats_b_dict,
        frequency_skew_ppm=skew_ppm,
        frequency_skew_ns_per_sec=skew_ns_per_sec,
        metadata=metadata,
        ftm_data=ftm_data if ftm_data else None,
    )

    print(f"Plots saved to {output_dir}/")
    print(f"Open {html_path} in browser for full report")

    return 0


def linreg_crossing(points_x, points_y, peak_val):
    """
    Apply linear regression to edge points and find 50% crossing.

    Args:
        points_x: Array of sample indices
        points_y: Array of amplitude values
        peak_val: Peak value for computing 50% level

    Returns:
        Edge time in samples (sub-sample precision)
    """
    import numpy as np

    n = len(points_x)
    if n < 2:
        return float(points_x[0]) if n == 1 else 0.0

    # Target is 50% of peak
    target_y = peak_val * 0.5

    # Linear regression: y = mx + b
    sum_x = np.sum(points_x.astype(np.float64))
    sum_y = np.sum(points_y.astype(np.float64))
    sum_xy = np.sum(points_x.astype(np.float64) * points_y.astype(np.float64))
    sum_xx = np.sum(points_x.astype(np.float64) ** 2)

    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-10:
        return float(points_x[n // 2])

    m = (n * sum_xy - sum_x * sum_y) / denom
    b = (sum_y - m * sum_x) / n

    if abs(m) < 1e-10:
        return float(points_x[n // 2])

    # Solve for x where y = target_y
    return (target_y - b) / m


def analyze_edge_files_v2(args):
    """Analyze V2 edge files with deferred linear regression."""
    import numpy as np
    from edgeio import EdgeFileReaderV2, read_metadata, EDGE_RISING, EDGE_FALLING
    from analyze.streaming import StreamingMatcher, StreamingStats, DelayFileWriter
    from analyze.report import (
        plot_histogram, plot_timeseries, generate_html_report,
    )
    from analyze.stats import JitterStats

    input_dir = Path(args.directory)

    # Read metadata
    meta = read_metadata(input_dir)
    sample_rate = meta['sample_rate']
    pulse_freq = meta.get('pulse_freq', 2000)

    print(f"Analyzing V2 edges from {input_dir}/")
    print(f"  Sample rate: {sample_rate/1e6:.1f} MSps")
    print(f"  Pulse freq: {pulse_freq} Hz")
    print(f"  Applying deferred linear regression...")

    # Initialize V2 readers
    reader_ref = EdgeFileReaderV2(input_dir, channel=args.ref_channel)
    reader_target = EdgeFileReaderV2(input_dir, channel=args.target_channel)

    # Compute max delay threshold (10% of period)
    period_ns = 1e9 / pulse_freq
    max_delay_ns = period_ns * 0.1

    # Initialize matcher and stats
    matcher = StreamingMatcher(sample_rate=sample_rate, max_delay_ns=max_delay_ns)
    stats = StreamingStats()

    # Output directory
    output_dir = Path(args.output) if args.output else input_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Select edge type
    edge_type = EDGE_FALLING if args.edge_type == 'falling' else EDGE_RISING

    # Process edges: apply regression and collect times
    print(f"\nApplying linear regression to edges...")

    # Collect all edge times (with regression applied)
    ref_times = []
    target_times = []
    n_ref_processed = 0
    n_target_processed = 0

    for edge in reader_ref.iter_edges():
        if edge['edge_type'] == edge_type:
            time = linreg_crossing(edge['points_x'], edge['points_y'], edge['peak_val'])
            ref_times.append(time)
        n_ref_processed += 1
        if n_ref_processed % 10000 == 0:
            print(f"  Processed {n_ref_processed} ref edges...")

    for edge in reader_target.iter_edges():
        if edge['edge_type'] == edge_type:
            time = linreg_crossing(edge['points_x'], edge['points_y'], edge['peak_val'])
            target_times.append(time)
        n_target_processed += 1

    ref_times = np.array(ref_times, dtype=np.float64)
    target_times = np.array(target_times, dtype=np.float64)

    print(f"  Reference (ch{args.ref_channel}): {len(ref_times)} edges")
    print(f"  Target (ch{args.target_channel}): {len(target_times)} edges")

    if len(ref_times) == 0 or len(target_times) == 0:
        print("Error: No edges to analyze")
        return 1

    # For plots: subsample to max 50k points
    MAX_PLOT_POINTS = 50000
    plot_times = []
    plot_delays = []
    plot_subsample_rate = max(1, len(ref_times) // MAX_PLOT_POINTS)

    # Match edges and compute delays
    print(f"\nMatching edges (max delay: {max_delay_ns:.0f}ns)...")

    delay_file = output_dir / 'delays.bin'
    match_idx = 0

    with DelayFileWriter(delay_file) as delay_writer:
        for result in matcher.match(ref_times, target_times):
            if not result.rejected:
                delay_writer.write(result.time_samples, result.delay_ns)
                stats.update(result.delay_ns)

                # Subsample for plotting
                if match_idx % plot_subsample_rate == 0:
                    plot_times.append(result.time_samples)
                    plot_delays.append(result.delay_ns)
                match_idx += 1

    print(f"  Matched: {matcher.match_count}, Rejected: {matcher.reject_count}")

    # Convert to arrays for plotting
    all_times = np.array(plot_times)
    all_delays = np.array(plot_delays)

    if len(all_delays) == 0:
        print("Error: No valid matches found")
        return 1

    # Print statistics
    print(f"\nJitter Statistics (n={stats.count})")
    print(f"  Mean:   {stats.mean:+.3f} ns")
    print(f"  Std:    {stats.std:.3f} ns")
    print(f"  Min:    {stats.min:.3f} ns")
    print(f"  Max:    {stats.max:.3f} ns")
    print(f"  P50:    ±{stats.percentile(50):.3f} ns")
    print(f"  P95:    ±{stats.percentile(95):.3f} ns")
    print(f"  P99:    ±{stats.percentile(99):.3f} ns")
    print(f"  P99.9:  ±{stats.percentile(99.9):.3f} ns")

    print(f"\nSaved {stats.count} delays to {delay_file}")

    # Create JitterStats for report
    jitter_stats = JitterStats(
        count=stats.count,
        mean_ns=stats.mean,
        std_ns=stats.std,
        min_ns=stats.min,
        max_ns=stats.max,
        p50_ns=stats.percentile(50),
        p95_ns=stats.percentile(95),
        p99_ns=stats.percentile(99),
        p999_ns=stats.percentile(99.9),
    )

    # Metadata for report
    metadata = {
        'input': str(input_dir),
        'sample_rate': sample_rate,
        'pulse_freq': pulse_freq,
        'edge_type': args.edge_type,
        'format': 'V2 (deferred regression)',
    }

    # Generate plots
    print("\nGenerating plots...")
    plot_histogram(all_delays, output_dir / 'histogram.png', stats=jitter_stats)
    plot_timeseries(all_times, all_delays, output_dir / 'timeseries.png',
                   sample_rate=sample_rate, stats=jitter_stats)

    # Generate HTML report
    html_path = generate_html_report(
        output_dir,
        jitter_stats,
        metadata=metadata,
    )

    print(f"Plots saved to {output_dir}/")
    print(f"Open {html_path} in browser for full report")

    return 0


def cmd_analyze_edges(args):
    """Analyze edge files or raw .cfile and compute jitter statistics."""
    input_path = Path(args.directory)

    if not input_path.exists():
        print(f"Error: Not found: {input_path}")
        return 1

    if input_path.is_file() and input_path.suffix == '.cfile':
        # Streaming analysis of raw .cfile
        return analyze_cfile_streaming(args)
    elif input_path.is_dir():
        # Check for V2 format
        from edgeio import read_metadata
        try:
            meta = read_metadata(input_path)
            if meta.get('version', 1) >= 2:
                return analyze_edge_files_v2(args)
        except FileNotFoundError:
            pass
        # V1 format or no metadata
        return analyze_edge_files(args)
    else:
        print(f"Error: {input_path} is not a .cfile or edge directory")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="FTS-QA: Fine Time Sync Quality Assurance Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # analyze command
    p_analyze = subparsers.add_parser('analyze', help='Analyze captured data file')
    p_analyze.add_argument('directory', help='Working directory (containing capture.bin)')
    p_analyze.add_argument('-o', '--output', help='Output directory (default: same as input)')
    p_analyze.add_argument('-r', '--sample-rate', type=float, default=10e6,
                          help='Sample rate in Hz (default: 10e6)')
    p_analyze.add_argument('-f', '--pulse-freq', type=float, default=2000,
                          help='Pulse frequency in Hz (default: 2000)')
    p_analyze.add_argument('--edge-type', choices=['rising', 'falling', 'both'],
                          default='falling', help='Edge type to analyze (default: falling)')
    p_analyze.add_argument('--threshold', type=float, default=0.4,
                          help='Threshold for edge detection (default: 0.4)')
    p_analyze.add_argument('--skip', type=float, default=0.1,
                          help='Skip first N seconds (default: 0.1)')
    p_analyze.add_argument('--algorithm', choices=['crossing', 'linreg'],
                          default='crossing',
                          help='Edge detection algorithm (default: crossing). '
                               'linreg uses linear regression on 20-80%% edge slope.')
    p_analyze.set_defaults(func=cmd_analyze)

    # capture command
    p_capture = subparsers.add_parser('capture',
        help='Capture from RX (192.168.10.2 with GPSDO)')
    p_capture.add_argument('directory', help='Working directory (outputs capture.bin, capture.json)')
    p_capture.add_argument('-n', '--samples', type=float, default=250e6,
                          help='Number of samples (default: 250e6)')
    p_capture.add_argument('-r', '--sample-rate', type=float, default=10e6,
                          help='Sample rate in Hz (default: 10e6)')
    p_capture.add_argument('-f', '--freq', type=float, default=0,
                          help='Center frequency (default: 0)')
    p_capture.add_argument('-g', '--gain', type=float, default=0,
                          help='RX gain (default: 0)')
    p_capture.set_defaults(func=cmd_capture)

    # stream command
    p_stream = subparsers.add_parser('stream',
        help='Continuous capture with live jitter analysis (RX)')
    p_stream.add_argument('-d', '--duration', type=float, default=60,
                         help='Duration in seconds (default: 60)')
    p_stream.add_argument('-r', '--sample-rate', type=float, default=10e6,
                         help='Sample rate in Hz (default: 10e6)')
    p_stream.add_argument('-f', '--freq', type=float, default=0,
                         help='Center frequency (default: 0)')
    p_stream.add_argument('-g', '--gain', type=float, default=0,
                         help='RX gain (default: 0)')
    p_stream.add_argument('--pulse-freq', type=float, default=2000,
                         help='Pulse frequency in Hz (default: 2000)')
    p_stream.add_argument('--threshold', type=float, default=0.4,
                         help='Threshold for edge detection (default: 0.4)')
    p_stream.set_defaults(func=cmd_stream)

    # stream-mqtt command
    p_stream_mqtt = subparsers.add_parser('stream-mqtt',
        help='Continuous capture with MQTT publishing to RL engine')
    p_stream_mqtt.add_argument('-d', '--duration', type=float, default=None,
                               help='Duration in seconds (default: indefinite)')
    p_stream_mqtt.add_argument('-r', '--sample-rate', type=float, default=10e6,
                               help='Sample rate in Hz (default: 10e6)')
    p_stream_mqtt.add_argument('-f', '--freq', type=float, default=0,
                               help='Center frequency (default: 0)')
    p_stream_mqtt.add_argument('-g', '--gain', type=float, default=0,
                               help='RX gain (default: 0)')
    p_stream_mqtt.add_argument('--pulse-freq', type=float, default=2000,
                               help='Pulse frequency in Hz (default: 2000)')
    p_stream_mqtt.add_argument('--threshold', type=float, default=0.4,
                               help='Threshold for edge detection (default: 0.4)')
    p_stream_mqtt.add_argument('--mqtt-host', default='localhost',
                               help='MQTT broker host (default: localhost)')
    p_stream_mqtt.add_argument('--mqtt-port', type=int, default=1883,
                               help='MQTT broker port (default: 1883)')
    p_stream_mqtt.add_argument('--usrp-addr', default=None,
                               help='USRP RX address (default: USRP_RX_ADDR env or 192.168.10.2)')
    p_stream_mqtt.set_defaults(func=cmd_stream_mqtt)

    # generate command
    p_generate = subparsers.add_parser('generate',
        help='Generate test signals on TX (192.168.10.3)')
    p_generate.add_argument('-d', '--duration', type=float, default=None,
                           help='Duration in seconds (default: indefinite)')
    p_generate.add_argument('-f', '--pulse-freq', type=float, default=2000,
                           help='Pulse frequency in Hz (default: 2000)')
    p_generate.add_argument('-p', '--phase', type=float, default=0,
                           help='Phase shift in nanoseconds (default: 0)')
    p_generate.add_argument('-j', '--jitter', type=float, default=0,
                           help='Jitter std dev in nanoseconds (default: 0)')
    p_generate.add_argument('-a', '--amplitude', type=float, default=1.0,
                           help='Waveform amplitude 0.0-1.0 (default: 1.0)')
    p_generate.add_argument('-r', '--sample-rate', type=float, default=10e6,
                           help='Sample rate in Hz (default: 10e6)')
    p_generate.add_argument('--no-mimo', action='store_true',
                           help='Use internal clock instead of MIMO from RX')
    p_generate.set_defaults(func=cmd_generate)

    # capture-edges command
    p_capture_edges = subparsers.add_parser('capture-edges',
        help='Capture and detect edges, save to binary files')
    p_capture_edges.add_argument('directory',
                                 help='Working directory for edge files')
    p_capture_edges.add_argument('-d', '--duration', type=float, default=60,
                                 help='Duration in seconds (default: 60)')
    p_capture_edges.add_argument('-r', '--sample-rate', type=float, default=10e6,
                                 help='Sample rate in Hz (default: 10e6)')
    p_capture_edges.add_argument('-f', '--freq', type=float, default=0,
                                 help='Center frequency (default: 0)')
    p_capture_edges.add_argument('-g', '--gain', type=float, default=0,
                                 help='RX gain (default: 0)')
    p_capture_edges.add_argument('--pulse-freq', type=float, default=2000,
                                 help='Pulse frequency in Hz (default: 2000)')
    p_capture_edges.add_argument('--threshold', type=float, default=0.4,
                                 help='Threshold for edge detection (default: 0.4)')
    p_capture_edges.add_argument('--skip', type=float, default=0.1,
                                 help='Skip first N seconds (default: 0.1)')
    p_capture_edges.add_argument('--algorithm', choices=['crossing', 'linreg'],
                                 default='crossing',
                                 help='Edge detection algorithm (default: crossing). '
                                      'linreg is more robust but may be slower.')
    p_capture_edges.set_defaults(func=cmd_capture_edges)

    # analyze-edges command
    p_analyze_edges = subparsers.add_parser('analyze-edges',
        help='Analyze edge files or raw .cfile (streaming, memory-bounded)')
    p_analyze_edges.add_argument('directory', help='Working directory with edge files, or .cfile')
    p_analyze_edges.add_argument('-o', '--output', help='Output directory (default: same as input)')
    p_analyze_edges.add_argument('--ref-channel', type=int, default=0,
                                 help='Reference channel (default: 0)')
    p_analyze_edges.add_argument('--target-channel', type=int, default=1,
                                 help='Target channel (default: 1)')
    p_analyze_edges.add_argument('--edge-type', choices=['rising', 'falling'],
                                 default='falling', help='Edge type to analyze (default: falling)')
    # Options for .cfile streaming analysis
    p_analyze_edges.add_argument('-r', '--sample-rate', type=float, default=10e6,
                                 help='Sample rate in Hz (default: 10e6, for .cfile input)')
    p_analyze_edges.add_argument('-f', '--pulse-freq', type=float, default=2000,
                                 help='Pulse frequency in Hz (default: 2000, for .cfile input)')
    p_analyze_edges.add_argument('--threshold', type=float, default=0.4,
                                 help='Threshold for edge detection (default: 0.4, for .cfile input)')
    p_analyze_edges.add_argument('--skip', type=float, default=0.1,
                                 help='Skip first N seconds (default: 0.1, for .cfile input)')
    p_analyze_edges.add_argument('--algorithm', choices=['crossing', 'linreg'],
                                 default='linreg',
                                 help='Edge detection algorithm (default: linreg for .cfile streaming). '
                                      'crossing uses simple threshold, linreg uses linear regression.')
    p_analyze_edges.add_argument('--ftm-log', action='append', dest='ftm_logs',
                                 metavar='FILE',
                                 help='FTM log file from idf.py monitor (can specify multiple times)')
    p_analyze_edges.set_defaults(func=cmd_analyze_edges)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
