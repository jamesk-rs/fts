# FTS RL Platform

Real-time RL-based clock synchronization platform for FTS.

## Quick Start

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f rl_engine

# Stop all services
docker compose down
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Mosquitto | 1883 | MQTT broker |
| InfluxDB | 8086 | Time series database |
| Redis | 6379 | State cache |
| Grafana | 3000 | Dashboards (admin/admin) |
| RL Engine | - | RL processing service |

## MQTT Topics

### Device → Platform
- `fts/{device_id}/ftm` - FTM reports
- `fts/{device_id}/metrics` - Timing metrics

### SDR → Platform
- `fts/sdr/edges` - Edge timing data

### Platform → Device
- `fts/{device_id}/control` - Period corrections

## ESP32 Integration

Add to your FTS firmware:

```c
#include "fts_mqtt.h"

void control_callback(int32_t correction, float phase_error, float K) {
    // Apply period correction to DTR
    dtr_apply_correction(correction);
}

void app_main(void) {
    fts_mqtt_config_t mqtt_cfg = {
        .broker_uri = "mqtt://192.168.1.100:1883",
        .device_id = "slave1",
        .ctrl_cb = control_callback,
    };
    fts_mqtt_init(&mqtt_cfg);
    fts_mqtt_start();

    // In your FTM callback:
    fts_mqtt_publish_ftm(esp_timer_get_time(), session_id,
                         rtt_ps, rssi, t1, t2, t3, t4);
}
```

## SDR Integration

```python
from sdr_publisher import SDRPublisher

publisher = SDRPublisher("192.168.1.100")

for edge_a_ns, edge_b_ns in detect_edges(samples):
    publisher.publish_edge(edge_a_ns, edge_b_ns)
```

## RL Parameters

Environment variables for `rl_engine`:

| Variable | Default | Description |
|----------|---------|-------------|
| `RL_LEARNING_RATE` | 0.01 | Learning rate for gain updates |
| `RL_INITIAL_GAIN` | 1.0 | Initial proportional gain |
| `RL_MIN_GAIN` | 0.1 | Minimum gain bound |
| `RL_MAX_GAIN` | 10.0 | Maximum gain bound |
| `CORRELATION_WINDOW_MS` | 100 | FTM-SDR correlation window |

## Development

```bash
# Rebuild rl_engine after code changes
docker compose build rl_engine
docker compose up -d rl_engine

# View rl_engine logs
docker compose logs -f rl_engine

# Access InfluxDB UI
open http://localhost:8086

# Access Grafana
open http://localhost:3000
```
