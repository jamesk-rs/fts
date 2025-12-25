#!/bin/bash
# Deploy FTS Platform stack inside LXC container
# Run this script inside the LXC container at /opt/fts-platform

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== FTS Platform Deployment ==="

# Check if .env exists
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env

        # Generate secure token and password
        GENERATED_TOKEN=$(openssl rand -hex 32)
        GENERATED_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)

        sed -i "s/CHANGE_ME_generate_with_openssl_rand_hex_32/$GENERATED_TOKEN/g" .env
        sed -i "s/CHANGE_ME_secure_password_here/$GENERATED_PASS/g" .env

        echo ""
        echo "Generated credentials (save these!):"
        echo "  InfluxDB Token: $GENERATED_TOKEN"
        echo "  Admin Password: $GENERATED_PASS"
        echo ""
    else
        echo "Error: .env.example not found. Please create .env manually."
        exit 1
    fi
fi

# Source environment
set -a
source .env
set +a

# Validate required variables
if [ -z "$INFLUX_ADMIN_TOKEN" ] || [ "$INFLUX_ADMIN_TOKEN" = "CHANGE_ME_generate_with_openssl_rand_hex_32" ]; then
    echo "Error: Please set INFLUX_ADMIN_TOKEN in .env"
    exit 1
fi

if [ -z "$INFLUX_ADMIN_PASSWORD" ] || [ "$INFLUX_ADMIN_PASSWORD" = "CHANGE_ME_secure_password_here" ]; then
    echo "Error: Please set INFLUX_ADMIN_PASSWORD in .env"
    exit 1
fi

# Get Docker GID
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
export DOCKER_GID

# Create directory structure
echo "Setting up directories..."
mkdir -p grafana/provisioning/dashboards
mkdir -p grafana/provisioning/datasources
mkdir -p mosquitto
mkdir -p influxdb
mkdir -p telegraf
mkdir -p rl_engine

# Copy telegraf config
if [ -f telegraf.cloud.conf ]; then
    cp telegraf.cloud.conf telegraf/telegraf.conf
fi

# Update Grafana datasource with correct token
echo "Configuring Grafana datasource..."
cat > grafana/provisioning/datasources/influxdb.yml << EOF
apiVersion: 1
datasources:
  - name: InfluxDB
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    jsonData:
      version: Flux
      organization: ${INFLUX_ORG:-fts}
      defaultBucket: ${INFLUX_BUCKET:-fts}
    secureJsonData:
      token: ${INFLUX_ADMIN_TOKEN}
    isDefault: true

  - name: InfluxDB-Health
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    jsonData:
      version: Flux
      organization: ${INFLUX_ORG:-fts}
      defaultBucket: health
    secureJsonData:
      token: ${INFLUX_ADMIN_TOKEN}
EOF

# Create mosquitto config
echo "Configuring Mosquitto..."
cat > mosquitto/mosquitto.conf << 'EOF'
listener 1883
protocol mqtt

listener 9001
protocol websockets

allow_anonymous true

persistence true
persistence_location /mosquitto/data/

log_dest file /mosquitto/log/mosquitto.log
log_dest stdout
log_type error
log_type warning
log_type notice
log_type information

max_keepalive 120
EOF

# Create init-buckets script
echo "Creating InfluxDB init script..."
cat > influxdb/init-buckets.sh << 'EOF'
#!/bin/bash
set -e

until influx ping &>/dev/null; do
    echo "Waiting for InfluxDB to be ready..."
    sleep 1
done

if ! influx bucket list --org "$DOCKER_INFLUXDB_INIT_ORG" --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" | grep -q "^health"; then
    echo "Creating 'health' bucket..."
    influx bucket create \
        --name health \
        --org "$DOCKER_INFLUXDB_INIT_ORG" \
        --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"
    echo "Bucket 'health' created successfully"
else
    echo "Bucket 'health' already exists"
fi
EOF
chmod +x influxdb/init-buckets.sh

# Check if we need RL engine
if [ -d rl_engine ] && [ -f rl_engine/Dockerfile ]; then
    echo "RL engine found, will be included in deployment"
    COMPOSE_PROFILES=""
else
    echo "RL engine not found, deploying without it"
    # Remove rl_engine from compose if not present
    COMPOSE_PROFILES=""
fi

# Pull and start services
echo ""
echo "Pulling Docker images..."
docker compose -f docker-compose.cloud.yml pull

echo ""
echo "Starting services..."
docker compose -f docker-compose.cloud.yml up -d

# Wait for services
echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check status
echo ""
echo "=== Service Status ==="
docker compose -f docker-compose.cloud.yml ps

# Get container IP
CONTAINER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Services available at:"
echo "  InfluxDB:  http://${CONTAINER_IP}:8086"
echo "  Grafana:   http://${CONTAINER_IP}:3000"
echo "  MQTT:      mqtt://${CONTAINER_IP}:1883"
echo "  MQTT WS:   ws://${CONTAINER_IP}:9001"
echo ""
echo "Credentials:"
echo "  InfluxDB Username: ${INFLUX_ADMIN_USERNAME:-admin}"
echo "  InfluxDB Password: (see .env)"
echo "  Grafana Username:  admin"
echo "  Grafana Password:  (see .env)"
echo ""
echo "ESP32 Configuration:"
echo "  Update broker_uri to: mqtt://${CONTAINER_IP}:1883"
