#!/bin/bash
# Create LXC container for FTS Platform on Proxmox
# Run this script on the Proxmox host

set -e

# Configuration - adjust these as needed
CTID="${CTID:-200}"
HOSTNAME="${HOSTNAME:-fts-platform}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE="${TEMPLATE:-local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst}"
MEMORY="${MEMORY:-4096}"
CORES="${CORES:-4}"
DISK="${DISK:-32}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"  # Use "dhcp" or static IP like "192.168.1.100/24"
GATEWAY="${GATEWAY:-}"  # Required if using static IP

echo "=== FTS Platform LXC Creation ==="
echo "Container ID: $CTID"
echo "Hostname: $HOSTNAME"
echo "Memory: ${MEMORY}MB"
echo "Cores: $CORES"
echo "Disk: ${DISK}GB"
echo ""

# Check if template exists, if not download it
if ! pveam list local | grep -q "debian-12-standard"; then
    echo "Downloading Debian 12 template..."
    pveam update
    pveam download local debian-12-standard_12.7-1_amd64.tar.zst
fi

# Check if container already exists
if pct status $CTID &>/dev/null; then
    echo "Error: Container $CTID already exists!"
    echo "To recreate, first run: pct destroy $CTID"
    exit 1
fi

# Build network config
if [ "$IP" = "dhcp" ]; then
    NET_CONFIG="name=eth0,bridge=$BRIDGE,ip=dhcp"
else
    NET_CONFIG="name=eth0,bridge=$BRIDGE,ip=$IP,gw=$GATEWAY"
fi

echo "Creating LXC container..."
pct create $CTID $TEMPLATE \
    --hostname $HOSTNAME \
    --storage $STORAGE \
    --rootfs ${STORAGE}:${DISK} \
    --memory $MEMORY \
    --cores $CORES \
    --net0 $NET_CONFIG \
    --features nesting=1,keyctl=1 \
    --unprivileged 1 \
    --onboot 1 \
    --start 0

# Enable features required for Docker
echo "Configuring LXC for Docker..."
cat >> /etc/pve/lxc/${CTID}.conf << 'EOF'

# Docker requirements
lxc.apparmor.profile: unconfined
lxc.cap.drop:
lxc.cgroup2.devices.allow: a
lxc.mount.auto: proc:rw sys:rw
EOF

echo "Starting container..."
pct start $CTID

# Wait for container to be ready
echo "Waiting for container to start..."
sleep 5

# Get container IP
if [ "$IP" = "dhcp" ]; then
    for i in {1..30}; do
        CONTAINER_IP=$(pct exec $CTID -- ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || true)
        if [ -n "$CONTAINER_IP" ]; then
            break
        fi
        echo "Waiting for DHCP..."
        sleep 2
    done
else
    CONTAINER_IP="${IP%/*}"
fi

echo ""
echo "=== LXC Container Created Successfully ==="
echo "Container ID: $CTID"
echo "IP Address: ${CONTAINER_IP:-unknown}"
echo ""
echo "Next step: Run ./02-install-docker.sh"
