#!/bin/bash
# Install Docker inside LXC container
# Run this script on the Proxmox host

set -e

CTID="${CTID:-200}"

echo "=== Installing Docker in LXC $CTID ==="

# Update system and install dependencies
echo "Updating system..."
pct exec $CTID -- bash -c 'apt-get update && apt-get upgrade -y'

echo "Installing dependencies..."
pct exec $CTID -- bash -c 'apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    htop \
    vim'

# Add Docker's official GPG key
echo "Adding Docker repository..."
pct exec $CTID -- bash -c 'install -m 0755 -d /etc/apt/keyrings'
pct exec $CTID -- bash -c 'curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc'
pct exec $CTID -- bash -c 'chmod a+r /etc/apt/keyrings/docker.asc'

# Add the repository to apt sources
pct exec $CTID -- bash -c 'echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null'

# Install Docker
echo "Installing Docker..."
pct exec $CTID -- bash -c 'apt-get update'
pct exec $CTID -- bash -c 'apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin'

# Start and enable Docker
echo "Starting Docker..."
pct exec $CTID -- systemctl start docker
pct exec $CTID -- systemctl enable docker

# Verify installation
echo "Verifying Docker installation..."
pct exec $CTID -- docker --version
pct exec $CTID -- docker compose version

# Create FTS platform directory
echo "Creating FTS platform directory..."
pct exec $CTID -- mkdir -p /opt/fts-platform

echo ""
echo "=== Docker Installed Successfully ==="
echo ""
echo "Next step: Copy FTS platform files to the container:"
echo ""
echo "  # From your local machine:"
echo "  cd fts-platform"
echo "  tar czf - docker-compose.yml telegraf grafana mosquitto influxdb deploy/.env deploy/03-deploy-stack.sh | \\"
echo "    ssh root@<proxmox-host> 'pct exec $CTID -- tar xzf - -C /opt/fts-platform'"
echo ""
echo "  # Or enter the container and clone the repo:"
echo "  pct enter $CTID"
echo "  cd /opt/fts-platform"
