#!/bin/bash
# Copy FTS Platform files to LXC container via Proxmox host
# Run this from your local machine in the fts-platform directory

set -e

PROXMOX_HOST="${1:-}"
CTID="${2:-200}"

if [ -z "$PROXMOX_HOST" ]; then
    echo "Usage: $0 <proxmox-host> [container-id]"
    echo ""
    echo "Example: $0 root@192.168.1.10 200"
    exit 1
fi

echo "=== Copying FTS Platform to LXC $CTID on $PROXMOX_HOST ==="

# Create archive of required files
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLATFORM_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PLATFORM_DIR"

echo "Creating archive..."
tar czf /tmp/fts-platform.tar.gz \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='qa/data' \
    --exclude='*.ipynb_checkpoints' \
    deploy/docker-compose.cloud.yml \
    deploy/telegraf.cloud.conf \
    deploy/.env.example \
    deploy/03-deploy-stack.sh \
    grafana/provisioning/dashboards/*.json \
    grafana/provisioning/dashboards/dashboards.yml \
    rl_engine/

echo "Copying to Proxmox host..."
scp /tmp/fts-platform.tar.gz "$PROXMOX_HOST":/tmp/

echo "Extracting to LXC container..."
ssh "$PROXMOX_HOST" "pct exec $CTID -- mkdir -p /opt/fts-platform && \
    cat /tmp/fts-platform.tar.gz | pct exec $CTID -- tar xzf - -C /opt/fts-platform --strip-components=0 && \
    pct exec $CTID -- chmod +x /opt/fts-platform/deploy/03-deploy-stack.sh && \
    rm /tmp/fts-platform.tar.gz"

rm /tmp/fts-platform.tar.gz

echo ""
echo "=== Files Copied Successfully ==="
echo ""
echo "Next steps:"
echo "  1. Enter the LXC container:"
echo "     ssh $PROXMOX_HOST"
echo "     pct enter $CTID"
echo ""
echo "  2. Deploy the stack:"
echo "     cd /opt/fts-platform/deploy"
echo "     ./03-deploy-stack.sh"
