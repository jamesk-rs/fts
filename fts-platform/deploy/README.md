# FTS Platform Deployment

Deploy the FTS TIG stack (Telegraf, InfluxDB, Grafana) with MQTT broker.

## Scripts

| Script | Where to run | Purpose |
|--------|--------------|---------|
| `01-create-lxc.sh` | Proxmox host | Creates LXC container (Proxmox-specific) |
| `02-install-docker.sh` | Inside LXC | Installs Docker (Proxmox-specific) |
| `03-deploy-stack.sh` | Any Docker host | Deploys FTS stack (universal) |

The `03-deploy-stack.sh` script works on any Debian-based system with Docker Compose installed - Proxmox LXC, regular VM, cloud instance, or bare metal.

## Deployment Profiles

### Local Setup

Everything runs on a single machine. Use this for development or when all hardware (ESP32, SDR) is connected to the same host.

```
┌─────────────────────────────────────────┐
│              Local Host                 │
│                                         │
│  ESP32 ──┐                              │
│          ├─► Mosquitto ─► Telegraf      │
│  SDR ────┤                    │         │
│          ▼                    ▼         │
│    stream-mqtt           InfluxDB       │
│                              │          │
│                              ▼          │
│                           Grafana       │
└─────────────────────────────────────────┘
```

### Split Setup

Lab equipment stays local, TIG stack runs in the cloud. MQTT messages are bridged over the internet with authentication. Handles unreliable connections - messages queue locally during outages.

```
LAB (Shuttle PC)                         CLOUD (LXC/VM)
┌─────────────────────┐                  ┌─────────────────────┐
│  ESP32 ──┐          │                  │                     │
│          ├─► Mosquitto ═══════════════►│ Mosquitto (auth)    │
│  SDR ────┤     │    │   MQTT bridge    │      │              │
│          ▼     │    │   (encrypted)    │      ▼              │
│   stream-mqtt  │    │                  │   Telegraf          │
│                │    │                  │      │              │
│           queue on  │                  │      ▼              │
│           disconnect│                  │   InfluxDB          │
│                     │                  │      │              │
│                     │                  │      ▼              │
│                     │                  │   Grafana           │
└─────────────────────┘                  └─────────────────────┘
```

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
./deploy/03-deploy-stack.sh local
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
./deploy/03-deploy-stack.sh split-cloud
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
./deploy/03-deploy-stack.sh split-local
```

The script will:
1. Configure Mosquitto with bridge to cloud
2. Start Mosquitto + stream-mqtt
3. Begin forwarding `fts/#` messages to cloud

### Proxmox LXC Installation

If deploying to Proxmox, use the helper scripts:

```bash
# On Proxmox host: create LXC container
scp deploy/01-create-lxc.sh root@proxmox:/root/
ssh root@proxmox
chmod +x /root/01-create-lxc.sh
./01-create-lxc.sh

# On Proxmox host: install Docker in LXC
scp deploy/02-install-docker.sh root@proxmox:/root/
chmod +x /root/02-install-docker.sh
./02-install-docker.sh

# Enter LXC and deploy
pct enter 200
git clone <repo-url> /opt/fts
cd /opt/fts/fts-platform
./deploy/03-deploy-stack.sh split-cloud  # or 'local'
```

## Configuration

### Environment Variables

All configuration is in `.env` (auto-generated from `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `INFLUX_ADMIN_TOKEN` | Yes | InfluxDB API token |
| `INFLUX_ADMIN_PASSWORD` | Yes | InfluxDB admin password |
| `GRAFANA_ADMIN_PASSWORD` | Yes | Grafana admin password |
| `MQTT_USERNAME` | split-* | MQTT authentication username |
| `MQTT_PASSWORD` | split-* | MQTT authentication password |
| `MQTT_BRIDGE_HOST` | split-local | Cloud Mosquitto address |
| `MQTT_BRIDGE_PORT` | split-local | Cloud Mosquitto port (default: 1883) |

### Adding RL Engine

The RL engine can be added to `local` or `split-cloud` profiles:

```bash
docker compose --profile local --profile rl up -d
# or
docker compose --profile split-cloud --profile rl up -d
```

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
