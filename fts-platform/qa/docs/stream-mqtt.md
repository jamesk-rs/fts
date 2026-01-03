# stream-mqtt: Edge Detection and MQTT Publishing

This document describes how `stream-mqtt` accumulates edges, matches them between channels, and manages the rolling window for statistics.

## Overview

`stream-mqtt` captures IQ samples from a USRP SDR, detects signal edges on both channels (I and Q), matches corresponding edges between channels to compute delays, and publishes:
1. **Raw edges** to `sdr/edges` (~2000/sec at 2kHz pulse rate)
2. **Rolling window stats** to `sdr/stats` (every 10 seconds)

## Data Flow

```
USRP (10 MSps)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  stream_threaded()                                          │
│  ┌──────────────┐     ┌──────────────┐                      │
│  │ Receiver     │────▶│ Queue        │ (200 chunks = 2s)    │
│  │ Thread       │     │ (decoupled)  │                      │
│  └──────────────┘     └──────┬───────┘                      │
│                              │                              │
│                              ▼                              │
│                       process_chunk()                       │
└─────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
      Edge Detection    Edge Matching    Stats (every 10s)
            │                 │                 │
            ▼                 ▼                 ▼
     all_falling_a/b     all_delays      MQTT: sdr/stats
                              │
                              ▼
                      MQTT: sdr/edges
```

## Processing Stages

### 1. Chunk Reception

Samples arrive in 10ms chunks (100,000 samples at 10 MSps):

```python
chunk_samples = int(args.sample_rate * 0.01)  # 10ms chunks
```

The `stream_threaded()` method decouples reception from processing:
- **Receiver thread**: Pulls samples from UHD into a queue (never blocks)
- **Processing thread**: Pulls from queue, calls `process_chunk()` callback
- **Queue depth**: 200 chunks (~2 seconds buffer)

This prevents UHD overflows when processing occasionally takes longer than 10ms.

### 2. Edge Detection

Each chunk is split into I (channel A) and Q (channel B) components:

```python
chan_a = data.real
chan_b = data.imag
```

`StreamingCrossingDetector` finds threshold crossings in each channel:

```python
edges_a = detector_a.process(chan_a)  # Returns [[time, type], ...]
edges_b = detector_b.process(chan_b)
```

Where:
- `time` = sample index (absolute, cumulative across chunks)
- `type` = 0 (rising edge) or 1 (falling edge)

Only **falling edges** are used (more reliable with AC-coupled signals):

```python
falling_a = edges_a[edges_a[:, 1] == 1, 0]
falling_b = edges_b[edges_b[:, 1] == 1, 0]
```

### 3. Edge Accumulation

Detected edges are appended to per-channel lists:

```python
all_falling_a.append(falling_a)  # List of numpy arrays
all_falling_b.append(falling_b)
```

At 2kHz pulse rate with 10ms chunks, each append adds ~20 edges.

### 4. Edge Matching

Matching uses only the **last 10 chunks** (~200 edges) to keep it fast:

```python
recent_a = np.concatenate(all_falling_a[-10:])
recent_b = np.concatenate(all_falling_b[-10:])

matched_a, matched_b, delays = match_edges(
    recent_a, recent_b, sample_rate, pulse_freq=pulse_freq
)
```

`match_edges()` pairs edges from channel A with corresponding edges from channel B:
- Finds the closest B edge for each A edge within a time tolerance
- Returns matched pairs and their delays (B_time - A_time) in seconds

### 5. Duplicate Prevention

To avoid publishing the same edge multiple times (since we match overlapping windows), we track the last published edge time:

```python
for t_a, t_b, delay_ns in zip(matched_a, matched_b, delays):
    if t_a <= last_published_time:
        continue  # Skip already published
    # ... publish ...
    last_published_time = t_a
```

### 6. Delay Accumulation (for Stats)

Instead of re-matching all edges for statistics (expensive O(n²)), we save delays from per-chunk matching:

```python
if len(delays) > 0:
    all_delays.append(delays)
```

This allows O(n) stats computation later.

## Rolling Window Management

### Window Parameters

```python
WINDOW_SECONDS = 60.0
max_delays = int(WINDOW_SECONDS * args.pulse_freq)  # ~120,000 at 2kHz
```

### Trimming (Every 10 Seconds)

During periodic reporting, delays are trimmed to the window size:

```python
combined_delays = np.concatenate(all_delays)
if len(combined_delays) > max_delays:
    combined_delays = combined_delays[-max_delays:]  # Keep newest
    all_delays = [combined_delays]  # Replace list with single array
```

This keeps memory bounded while maintaining a 60-second sliding window.

### Stats Computation

Stats are computed from accumulated delays (no re-matching required):

```python
stats = compute_stats(combined_delays, args.pulse_freq)
```

Returns:
- `count`: Number of delay measurements
- `mean_ns`, `std_ns`: Mean and standard deviation
- `min_ns`, `max_ns`: Range
- `p50_ns`, `p95_ns`, `p99_ns`, `p999_ns`: Percentiles

## MQTT Output

### Raw Edges: `sdr/edges`

Published per-chunk (~20 edges every 10ms = ~2000/sec):

```json
{
  "ts": 1702834567.123,
  "channel_a_edge_ns": 500000,
  "channel_b_edge_ns": 500100,
  "delay_ns": 100
}
```

### Stats: `sdr/stats`

Published every 10 seconds:

```json
{
  "ts": 1702834570.0,
  "count": 120000,
  "mean_ns": 100.5,
  "std_ns": 23.2,
  "min_ns": 50.1,
  "max_ns": 150.3,
  "p50_ns": 15.6,
  "p95_ns": 45.7,
  "p99_ns": 61.8,
  "p999_ns": 76.0,
  "overflow_count": 0
}
```

## Memory Usage

At steady state (after 60 seconds):
- `all_delays`: ~120,000 float64 values = ~1 MB
- `all_falling_a/b`: Not trimmed, but only last 10 chunks used for matching

The edge lists (`all_falling_a/b`) grow unbounded but are only used for matching recent edges. The delay list is trimmed to maintain the rolling window.

## Performance Considerations

1. **Threaded reception**: Prevents UHD overflows from processing jitter
2. **Limited matching window**: Only last 10 chunks (~200 edges) matched per chunk
3. **Accumulated delays**: Avoids O(n²) re-matching for stats
4. **Infrequent trimming**: Only every 10 seconds, not per-chunk
5. **Infrequent stats**: Computed every 10 seconds, not per-chunk
