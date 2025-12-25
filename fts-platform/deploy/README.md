# FTS Platform - Proxmox LXC Deployment

Deploy the FTS TIG stack (InfluxDB, Grafana, Mosquitto, Telegraf).
* 01-create-lxc.sh - Run on ProxMox host (if you want to deploy in LXC)
* 02-install-docker.sh - Run inside the container (LXS or whatever other container or VM you choose)
* 03-deploy-stack.sh - Run inside the container, it will create .env unless it exists already

## Prerequisites

- Proxmox VE 7.x or 8.x
- SSH access to Proxmox host
- Available storage for LXC container (recommended: 32GB+)

## Quick Deploy

```bash
# 1. Create LXC container (run on Proxmox host)
scp fts-platform/deploy/01-create-lxc.sh root@<proxmox-host>:/root/
ssh root@<proxmox-host>
chmod +x /root/01-crea01-create-lxc.shte-lxc.sh
/root/

# 2. Install Docker inside LXC (run on Proxmox host)
scp fts-platform/deploy/02-install-docker.sh root@<proxmox-host>:/root/
chmod +x /root/02-install-docker.sh
/root/02-install-docker.sh

# 3. Clone repo inside LXC
pct enter 200
git clone <your-repo-url> /opt/fts
cd /opt/fts/fts-platform

# 4. Deploy
./deploy/03-deploy-stack.sh
```

## Configuration

The deploy script auto-generates credentials on first run. To customize, create `.env` before running:

```bash
cp .env.example .env
# Edit .env with your values
./deploy/03-deploy-stack.sh
```

Environment variables:
- `INFLUX_ADMIN_TOKEN` - API token for InfluxDB
- `INFLUX_ADMIN_PASSWORD` - Admin password for InfluxDB
- `GRAFANA_ADMIN_PASSWORD` - Admin password for Grafana

## Ports

| Service | Port | URL |
|---------|------|-----|
| InfluxDB | 8086 | http://<lxc-ip>:8086 |
| Grafana | 3000 | http://<lxc-ip>:3000 |
| MQTT | 1883 | mqtt://<lxc-ip>:1883 |
| MQTT WS | 9001 | ws://<lxc-ip>:9001 |

## Deployment Profiles

Three deployment modes are available:

| Profile | Services | Use Case |
|---------|----------|----------|
| `local` | Mosquitto + TIG + stream-mqtt | Full local stack (Shuttle PC) |
| `split-local` | Mosquitto + stream-mqtt | Lab side of split setup |
| `split-cloud` | Mosquitto + TIG | Cloud side of split setup |

```bash
# Full local deployment (everything on one machine)
docker compose --profile local up -d

# Split setup - on Lab/Shuttle (with MQTT bridge to cloud)
docker compose --profile split-local up -d

# Split setup - on Cloud LXC
docker compose --profile split-cloud up -d

# Add RL engine (works with local or split-cloud)
docker compose --profile local --profile rl up -d
```

### Split Setup Architecture

```
LAB (Shuttle)                          CLOUD (LXC)
┌──────────────────┐                   ┌──────────────────┐
│ ESP32 → Mosquitto ════════════════►  │ Mosquitto        │
│ SDR → stream-mqtt│   MQTT bridge     │   ↓              │
│                  │                   │ Telegraf         │
│                  │                   │   ↓              │
│                  │                   │ InfluxDB→Grafana │
└──────────────────┘                   └──────────────────┘
```

For split setup, add bridge config to Lab's `mosquitto.conf`:
```conf
connection fts-cloud
address <cloud-ip>:1883
topic fts/# out 1
cleansession false
```

## ESP32 Configuration

Update your ESP32 firmware to connect to the cloud:

```c
fts_mqtt_config_t mqtt_cfg = {
    .broker_uri = "mqtt://<lxc-ip>:1883",
    .device_id = "slave1",
    .ctrl_cb = control_callback,
};
```

## Firewall

If your Proxmox host has a firewall, open these ports on the LXC:
```bash
pct set 200 -firewall 0  # Disable firewall
# Or configure rules for ports 1883, 3000, 8086, 9001
```
