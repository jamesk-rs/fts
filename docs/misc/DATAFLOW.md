# FTS Data Flow Architecture

This document describes the data flow between lab and cloud components in the split deployment.

## Overview

The split deployment separates data collection (lab) from storage and visualization (cloud):
- **Lab**: ESP32 devices, SDR capture, local MQTT with timestamping
- **Cloud**: InfluxDB storage, Grafana visualization

## Architecture Diagram

```
LAB                                                      CLOUD
┌───────────────────────────────────────────┐          ┌────────────────────────┐
│                                           │          │                        │
│  ESP32 devices          ┌──────────────┐  │          │  ┌─────────┐           │
│    │                    │ Telegraf-Lab │  │          │  │ Grafana │           │
│    │ fts/{device}/*     │ +timestamps  │  │          │  └────┬────┘           │
│    ▼                    └──────┬───────┘  │          │       ▲                │
│  ┌──────────┐   fts/#     ▲    │          │          │       │                │
│  │          │─────────────┘    │          │          │  ┌────┴─────┐          │
│  │Mosquitto │◄─────────────────┘          │          │  │ InfluxDB │          │
│  │          │  fts_ts/#, health/lab/*     │          │  └────┬─────┘          │
│  └────┬─────┘                             │          │       ▲                │
│       │  ▲                                │          │       │                │
│       │  │ sdr/#                          │          │  ┌────┴────┐           │
│       │  └──────────┐                     │          │  │Telegraf │           │
│       │        ┌────┴─────┐               │          │  └────┬────┘           │
│       │        │stream-   │               │          │       ▲                │
│       │        │mqtt      │               │          │       │                │
│       │        └──────────┘               │          │  ┌────┴─────┐          │
│       ▼                                   │          │  │Mosquitto │          │
│  ┌────────┐  fts_ts/#, sdr/#, health/#    │          │  └────┬─────┘          │
│  │ Bridge ├───────────────────────────────┼──────────┼───────┘                │
│  └────────┘                               │          │                        │
│                                           │          │                        │
└───────────────────────────────────────────┘          └────────────────────────┘
```

ESP32 devices publish to `fts/{device}/*` topics (e.g., `fts/master/ftm`). Lab Telegraf subscribes to these, adds Unix timestamps, and republishes to `fts_ts/{device}/*`. The MQTT bridge forwards timestamped data to the cloud.

## Topic Namespaces

| Namespace | Description | Timestamp Source | Bridged? |
|-----------|-------------|------------------|----------|
| `fts/+/*` | Raw ESP32 data (no Unix timestamp) | - | No (local only) |
| `fts_ts/+/*` | Timestamped ESP32 data | Lab Telegraf arrival time | Yes |
| `sdr/*` | SDR data from stream-mqtt | Embedded in JSON (`ts` field) | Yes |
| `health/lab/*` | Lab system health metrics | Lab Telegraf collection time | Yes |

## Health Metrics

Both lab and cloud collect system health metrics, tagged by site:

| Metric | Lab (site=lab) | Cloud (site=cloud) |
|--------|----------------|-------------------|
| CPU usage | Yes | Yes |
| Memory | Yes | Yes |
| Disk usage | Yes | Yes |
| Disk I/O | Yes | Yes |
| Network | Yes | Yes |
| Docker containers | Yes | Yes |

Grafana dashboards can filter by `site` tag to view lab-only, cloud-only, or combined metrics.

## Buffering & Reliability

When cloud is offline:
1. Messages queue on lab Mosquitto
2. Data are not persisted on disk - it was causing too much delays
3. When connection restores, messages are delivered in order
4. Timestamps remain accurate (set at lab collection time)

## Configuration Files

| File | Purpose |
|------|---------|
| `telegraf/telegraf-lab.conf` | Lab Telegraf: timestamp injection + health collection |
| `telegraf/telegraf.conf` | Cloud Telegraf: consume timestamped data + cloud health |
| `mosquitto/mosquitto-split-local.conf.template` | Lab Mosquitto with bridge config |
| `mosquitto/mosquitto-split-cloud.conf` | Cloud Mosquitto with authentication |
| `docker-compose.yml` | Service definitions for all profiles |
