# FTS Platform

This folder contains implementation of TIG stack (Telegraf, InfluxDB, Grafana) with MQTT broker.
It includes stream-mqtt container pulling data from SDR and logging edge timing.
ESP32 devices are expected to submit MQTT messages directly.
It supports two deployment modes: a local installation (all components on one host) and split setup (data collectors on one host and the rest of the containers on another host, in the cloud).

## Quick Start

```bash
# Deploy with local profile
./bin/03-deploy-stack.sh local

# Start all services
bin/docker start

# View logs
bin/docker logs -f

# Stop all services
bin/docker stop
```

## Deployment Profiles

Set `COMPOSE_PROFILES` in `.env` to select:

| Profile | Use case |
|---------|----------|
| `local` | Everything on single machine (development) |
| `split-local` | Lab side of split setup (Mosquitto + stream-mqtt) |
| `split-cloud` | Cloud side of split setup (full TIG stack) |

See DATAFLOW.md for more details.

## Installation

### Prerequisites

- Docker and Docker Compose installed
- Git to clone the repository

### Local Setup Installation

```bash
# Clone repository
git clone <repo-url> /opt/fts
cd /opt/fts/fts-platform

# Deploy with local profile
./bin/03-deploy-stack.sh local
```

The script will:
1. Generate `.env` with secure credentials
2. Configure unauthenticated Mosquitto (local network only)
3. Start all services

Access points after deployment:
- Grafana: http://localhost:3000
- InfluxDB: http://localhost:8086
- MQTT: mqtt://localhost:1883

### Split Setup Installation

**Step 1: Deploy Cloud Instance**

On your cloud server (LXC, VM, or any Docker host):

```bash
# Clone repository
git clone <repo-url> /opt/fts
cd /opt/fts/fts-platform

# Deploy with split-cloud profile
./bin/03-deploy-stack.sh split-cloud
```

The script will:
1. Generate `.env` with secure credentials including MQTT username/password
2. Configure authenticated Mosquitto
3. Start Mosquitto + TIG stack

**Save the generated credentials** - you'll need `MQTT_USERNAME` and `MQTT_PASSWORD` for the lab setup.

**Step 2: Deploy Lab Instance**

On your lab machine (Shuttle PC):

```bash
# Clone repository
git clone <repo-url> /opt/fts
cd /opt/fts/fts-platform

# Create .env with cloud credentials
cp .env.example .env
```

Edit `.env` and set:
```bash
# Use same credentials as cloud instance
MQTT_USERNAME=fts
MQTT_PASSWORD=<password from cloud .env>

# Cloud instance address
MQTT_BRIDGE_HOST=<cloud-ip-or-hostname>
MQTT_BRIDGE_PORT=1883
```

Then deploy:
```bash
./bin/03-deploy-stack.sh split-local
```

The script will:
1. Configure Mosquitto with bridge to cloud
2. Start Mosquitto + stream-mqtt
3. Begin forwarding `fts/#` messages to cloud

### Proxmox LXC Installation

If deploying to Proxmox, use the helper scripts:

```bash
# On Proxmox host: create LXC container
scp bin/01-create-lxc.sh root@proxmox:/root/
ssh root@proxmox
chmod +x /root/01-create-lxc.sh
./01-create-lxc.sh

# On Proxmox host: install Docker in LXC
scp bin/02-install-docker.sh root@proxmox:/root/
chmod +x /root/02-install-docker.sh
./02-install-docker.sh

# Enter LXC and deploy
pct enter 200
git clone <repo-url> /opt/fts
cd /opt/fts/fts-platform
./bin/03-deploy-stack.sh split-cloud  # or 'local'
```

## Configuration

### Environment Variables

All configuration is in `.env` (auto-generated from `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `COMPOSE_PROFILES` | Yes | Deployment profile (local, split-local, split-cloud) |
| `INFLUX_ADMIN_TOKEN` | Yes | InfluxDB API token |
| `INFLUX_ADMIN_PASSWORD` | Yes | InfluxDB admin password |
| `GRAFANA_ADMIN_PASSWORD` | Yes | Grafana admin password |
| `MQTT_USERNAME` | split-* | MQTT authentication username |
| `MQTT_PASSWORD` | split-* | MQTT authentication password |
| `MQTT_BRIDGE_HOST` | split-local | Cloud Mosquitto address |
| `MQTT_BRIDGE_PORT` | split-local | Cloud Mosquitto port (default: 1883) |

## Ports

| Service | Port | Protocol |
|---------|------|----------|
| Grafana | 3000 | HTTP |
| InfluxDB | 8086 | HTTP |
| MQTT | 1883 | TCP |
| MQTT WebSocket | 9001 | WS |

## Firewall

For split setup, ensure these ports are open on the cloud instance:
- 1883/tcp - MQTT (required for bridge)
- 3000/tcp - Grafana (for dashboard access)
- 8086/tcp - InfluxDB (optional, for direct API access)

## Development

```bash
# Rebuild after code changes
bin/docker restart

# Access InfluxDB UI
open http://localhost:8086

# Access Grafana
open http://localhost:3000
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Mosquitto | 1883 | MQTT broker |
| InfluxDB | 8086 | Time series database |
| Grafana | 3000 | Dashboards |
| Telegraf | - | Metrics collection |

## MQTT Topics

### Device → Platform
- `fts/{device_id}/ftm` - FTM reports
- `fts/{device_id}/metrics` - Timing metrics

### SDR → Platform
- `sdr/edges` - Edge timing data
- `sdr/stats` - Rolling window statistics
- `sdr/phase_noise` - Phase noise measurements

### Platform → Device
- `fts/{device_id}/control` - Remote control (draft implementation)

```
(.venv) abb@fts:~/fts/fts-platform$ bin/control
usage: control [-h] [--broker BROKER] [--port PORT] {trigger-wifi-disconnect} ...
control: error: the following arguments are required: command
```