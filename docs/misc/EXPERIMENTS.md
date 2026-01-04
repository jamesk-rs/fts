# FTS Experiment Tracking

The `bin/experiment` script provides a simple way to annotate time-series data with experiment markers. These annotations appear in Grafana dashboards and help correlate measurements with physical setup changes.

## Quick Start

```bash
# Start an experiment
bin/experiment start my-test-001 "Testing with devices at 5m distance"

# Log events during the experiment
bin/experiment e "moved master to corner"
bin/experiment e "enabled high-power mode"

# Stop the experiment
bin/experiment stop "Test complete"

# List past experiments
bin/experiment list

# Show details of a specific experiment
bin/experiment show my-test-001
```

## Data Storage

### InfluxDB Measurement

Experiments are stored in the `experiments` measurement in the `fts` bucket:

```
experiments,experiment=<name>,type=<type> description="<text>" <timestamp_ns>
```

**Tags:**
- `experiment` - Experiment name (e.g., `distance-test-001`)
- `type` - Event type: `start`, `event`, or `stop`

**Fields:**
- `description` - Human-readable description text

**Timestamp:** Nanosecond precision Unix timestamp

### Example Data

```
experiments,experiment=range-test,type=start description="Testing\ range\ limits" 1703865600000000000
experiments,experiment=range-test,type=event description="devices\ at\ 10m" 1703865660000000000
experiments,experiment=range-test,type=stop description="Test\ complete" 1703866200000000000
```

## Local State

The script tracks the currently active experiment in `/tmp/fts-experiment-current`. This allows the `event` command to work without specifying the experiment name.

If no experiment is active, events are logged under the `unnamed` experiment.

## Configuration

The script reads configuration from `.env` in the project root:

| Variable | Description | Default |
|----------|-------------|---------|
| `INFLUX_URL` | InfluxDB API URL | Auto-detected |
| `INFLUX_TOKEN` | InfluxDB API token | Falls back to `INFLUX_ADMIN_TOKEN`, then `changeme` |
| `INFLUX_ORG` | InfluxDB organization | `fts` |
| `INFLUX_BUCKET` | InfluxDB bucket | `fts` |

### URL Auto-Detection

For split setups, `INFLUX_URL` is automatically derived from `MQTT_BRIDGE_HOST`:
- If `INFLUX_URL` is set, use it directly
- If `MQTT_BRIDGE_HOST` is set (split-local), use `http://${MQTT_BRIDGE_HOST}:8086`
- Otherwise, use `http://localhost:8086` (local setup)

## Grafana Integration

Experiments appear as annotations in Grafana dashboards. To add experiment annotations to a panel:

1. Go to Dashboard Settings > Annotations
2. Add a new annotation query:
   ```flux
   from(bucket: "fts")
     |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
     |> filter(fn: (r) => r._measurement == "experiments")
   ```
3. Configure display:
   - Title: `${experiment}: ${type}`
   - Text: `${_value}` (the description field)
   - Tags: `${type}`

## Commands Reference

| Command | Shortcut | Description |
|---------|----------|-------------|
| `start <name> [desc]` | - | Start a new experiment |
| `event <desc>` | `e` | Log an event in current experiment |
| `stop [desc]` | - | Stop current experiment |
| `status` | `s` | Show current experiment |
| `list` | `ls` | List all experiments (last 20) |
| `show <name>` | - | Show all events for an experiment |

Use `-v` or `--verbose` before any command to show configuration.

## Querying Experiments

### List all experiments in last 30 days

```flux
from(bucket: "fts")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "experiments")
  |> filter(fn: (r) => r.type == "start")
  |> keep(columns: ["_time", "experiment", "_value"])
```

### Get all events for a specific experiment

```flux
from(bucket: "fts")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "experiments")
  |> filter(fn: (r) => r.experiment == "my-test-001")
  |> sort(columns: ["_time"])
```

### Get experiment duration

```flux
from(bucket: "fts")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "experiments")
  |> filter(fn: (r) => r.experiment == "my-test-001")
  |> filter(fn: (r) => r.type == "start" or r.type == "stop")
  |> pivot(rowKey: ["experiment"], columnKey: ["type"], valueColumn: "_time")
```
