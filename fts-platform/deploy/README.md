# FTS Platform - Proxmox LXC Deployment

Deploy the FTS TIG stack (InfluxDB, Grafana, Mosquitto, Telegraf) to a Proxmox LXC container.

## Prerequisites

- Proxmox VE 7.x or 8.x
- SSH access to Proxmox host
- Available storage for LXC container (recommended: 32GB+)

## Quick Deploy

```bash
# 1. Create LXC container (run on Proxmox host)
scp fts-platform/deploy/01-create-lxc.sh root@<proxmox-host>:/root/
ssh root@<proxmox-host>
chmod +x /root/01-create-lxc.sh
/root/01-create-lxc.sh

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

## Optional Profiles

```bash
# Enable RL Engine
docker compose --profile rl up -d

# Enable UHD/SDR streaming
docker compose --profile uhd up -d
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
