#!/bin/bash
# Deploy FTS Platform stack
# Run this script from the fts-platform directory (parent of deploy/)

set -e

# Determine script location and fts-platform root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FTS_PLATFORM_DIR="$(dirname "$SCRIPT_DIR")"

cd "$FTS_PLATFORM_DIR"
echo "=== FTS Platform Deployment ==="
echo "Working directory: $FTS_PLATFORM_DIR"

# Check if .env exists, create from example if not
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env

        # Generate secure token and password
        GENERATED_TOKEN=$(openssl rand -hex 32)
        GENERATED_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)

        sed -i "s/CHANGE_ME_generate_with_openssl_rand_hex_32/$GENERATED_TOKEN/g" .env
        sed -i "s/CHANGE_ME_secure_password/$GENERATED_PASS/g" .env

        echo ""
        echo "Generated credentials (save these!):"
        echo "  InfluxDB Token: $GENERATED_TOKEN"
        echo "  Admin Password: $GENERATED_PASS"
        echo ""
    elif [ -f deploy/.env.example ]; then
        echo "Creating .env from deploy/.env.example..."
        cp deploy/.env.example .env

        GENERATED_TOKEN=$(openssl rand -hex 32)
        GENERATED_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)

        sed -i "s/CHANGE_ME_generate_with_openssl_rand_hex_32/$GENERATED_TOKEN/g" .env
        sed -i "s/CHANGE_ME_secure_password/$GENERATED_PASS/g" .env

        echo ""
        echo "Generated credentials (save these!):"
        echo "  InfluxDB Token: $GENERATED_TOKEN"
        echo "  Admin Password: $GENERATED_PASS"
        echo ""
    else
        echo "Error: No .env.example found. Please create .env manually."
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

if [ -z "$INFLUX_ADMIN_PASSWORD" ] || [ "$INFLUX_ADMIN_PASSWORD" = "CHANGE_ME_secure_password" ]; then
    echo "Error: Please set INFLUX_ADMIN_PASSWORD in .env"
    exit 1
fi

# Get Docker GID
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
export DOCKER_GID

# Create required directories
echo "Setting up directories..."
mkdir -p grafana/provisioning/dashboards
mkdir -p grafana/provisioning/datasources
mkdir -p mosquitto
mkdir -p influxdb

# Configure Grafana datasource with correct token
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

# Create mosquitto config if not exists
if [ ! -f mosquitto/mosquitto.conf ]; then
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
fi

# Create init-buckets script if not exists
if [ ! -f influxdb/init-buckets.sh ]; then
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
fi

# Pull and start services
echo ""
echo "Pulling Docker images..."
docker compose pull

echo ""
echo "Starting services..."
docker compose up -d

# Wait for services
echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check status
echo ""
echo "=== Service Status ==="
docker compose ps

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
echo ""
echo "Optional profiles:"
echo "  RL Engine:   docker compose --profile rl up -d"
echo "  UHD/SDR:     docker compose --profile uhd up -d"
