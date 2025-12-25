# FTS Platform - Proxmox LXC Deployment

Deploy the full FTS stack (InfluxDB, Grafana, Mosquitto, Telegraf, RL Engine) to a Proxmox LXC container.

## Prerequisites

- Proxmox VE 7.x or 8.x
- SSH access to Proxmox host
- Available storage for LXC container (recommended: 32GB+)

## Quick Deploy

```bash
# 1. Copy files to Proxmox host
scp -r deploy/ root@<proxmox-host>:/root/fts-deploy/

# 2. SSH to Proxmox and run setup
ssh root@<proxmox-host>
cd /root/fts-deploy
chmod +x *.sh

# 3. Create LXC container
./01-create-lxc.sh

# 4. Install Docker inside LXC (run from Proxmox host)
./02-install-docker.sh

# 5. Deploy FTS stack (run inside LXC)
pct enter 200
cd /opt/fts-platform
./03-deploy-stack.sh
```

## Configuration

Edit `.env` before deployment to set:
- `INFLUX_ADMIN_TOKEN` - Secure token for InfluxDB API
- `INFLUX_ADMIN_PASSWORD` - Admin password for InfluxDB
- `GRAFANA_ADMIN_PASSWORD` - Admin password for Grafana

## Ports

After deployment, these ports will be exposed:

| Service | Port | URL |
|---------|------|-----|
| InfluxDB | 8086 | http://<lxc-ip>:8086 |
| Grafana | 3000 | http://<lxc-ip>:3000 |
| MQTT | 1883 | mqtt://<lxc-ip>:1883 |
| MQTT WS | 9001 | ws://<lxc-ip>:9001 |

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
# On Proxmox host
pct set 200 -firewall 0  # Or configure rules:
# pvesh create /nodes/<node>/lxc/200/firewall/rules -type in -action ACCEPT -dport 1883 -proto tcp
# pvesh create /nodes/<node>/lxc/200/firewall/rules -type in -action ACCEPT -dport 8086 -proto tcp
# pvesh create /nodes/<node>/lxc/200/firewall/rules -type in -action ACCEPT -dport 3000 -proto tcp
```
