#!/bin/bash
# Deploy FTS Platform stack
# Usage: ./03-deploy-stack.sh [profile]
#   Profiles: local, split-local, split-cloud (default)

set -e

# Parse profile argument
PROFILE="${1:-split-cloud}"

case "$PROFILE" in
    local|split-local|split-cloud)
        ;;
    *)
        echo "Error: Invalid profile '$PROFILE'"
        echo "Valid profiles: local, split-local, split-cloud"
        exit 1
        ;;
esac

# Determine script location and fts-platform root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FTS_PLATFORM_DIR="$(dirname "$SCRIPT_DIR")"

cd "$FTS_PLATFORM_DIR"
echo "=== FTS Platform Deployment ==="
echo "Profile: $PROFILE"
echo "Working directory: $FTS_PLATFORM_DIR"

# Check if .env exists, create from example if not
if [ ! -f .env ]; then
    if [ ! -f .env.example ]; then
        echo "Error: No .env.example found. Please create .env manually."
        exit 1
    fi

    echo "Creating .env from .env.example..."
    cp .env.example .env

    # Generate secure credentials
    GENERATED_TOKEN=$(openssl rand -hex 32)
    GENERATED_INFLUX_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)
    GENERATED_GRAFANA_PASS=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)

    sed -i "s/CHANGE_ME_generate_with_openssl_rand_hex_32/$GENERATED_TOKEN/g" .env
    sed -i "s/CHANGE_ME_secure_password/$GENERATED_INFLUX_PASS/g" .env
    sed -i "s/CHANGE_ME_grafana_password/$GENERATED_GRAFANA_PASS/g" .env

    echo ""
    echo "Generated credentials (save these!):"
    echo "  InfluxDB Token: $GENERATED_TOKEN"
    echo "  InfluxDB Password: $GENERATED_INFLUX_PASS"
    echo "  Grafana Password: $GENERATED_GRAFANA_PASS"
    echo ""
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

if [ -z "$GRAFANA_ADMIN_PASSWORD" ] || [ "$GRAFANA_ADMIN_PASSWORD" = "CHANGE_ME_grafana_password" ]; then
    echo "Error: Please set GRAFANA_ADMIN_PASSWORD in .env"
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
    uid: influxdb
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
    uid: influxdb-health
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

# Configure Mosquitto based on profile
echo "Configuring Mosquitto for profile: $PROFILE..."
mkdir -p mosquitto/config

case "$PROFILE" in
    local)
        # Unauthenticated local setup
        cp mosquitto/mosquitto-local.conf mosquitto/config/mosquitto.conf
        ;;
    split-cloud)
        # Authenticated cloud setup - generate password file
        if [ -z "$MQTT_USERNAME" ] || [ -z "$MQTT_PASSWORD" ] || [ "$MQTT_PASSWORD" = "CHANGE_ME_mqtt_password" ]; then
            # Generate MQTT password if not set
            MQTT_USERNAME="${MQTT_USERNAME:-fts}"
            MQTT_PASSWORD=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)
            echo ""
            echo "Generated MQTT credentials (save these!):"
            echo "  MQTT Username: $MQTT_USERNAME"
            echo "  MQTT Password: $MQTT_PASSWORD"
            echo ""
            # Update .env with generated password
            if grep -q "^MQTT_PASSWORD=" .env; then
                sed -i "s/^MQTT_PASSWORD=.*/MQTT_PASSWORD=$MQTT_PASSWORD/" .env
            else
                echo "MQTT_PASSWORD=$MQTT_PASSWORD" >> .env
            fi
            if grep -q "^MQTT_USERNAME=" .env; then
                sed -i "s/^MQTT_USERNAME=.*/MQTT_USERNAME=$MQTT_USERNAME/" .env
            else
                echo "MQTT_USERNAME=$MQTT_USERNAME" >> .env
            fi
        fi
        cp mosquitto/mosquitto-split-cloud.conf mosquitto/config/mosquitto.conf
        # Generate password file
        echo "Generating MQTT password file..."
        docker run --rm -v "$(pwd)/mosquitto/config:/mosquitto/config" eclipse-mosquitto:2 \
            mosquitto_passwd -c -b /mosquitto/config/passwd "$MQTT_USERNAME" "$MQTT_PASSWORD"
        ;;
    split-local)
        # Local with bridge to cloud - needs bridge settings
        if [ -z "$MQTT_BRIDGE_HOST" ]; then
            echo "Error: MQTT_BRIDGE_HOST must be set in .env for split-local profile"
            echo "This should be the IP or hostname of your cloud Mosquitto instance"
            exit 1
        fi
        if [ -z "$MQTT_USERNAME" ] || [ -z "$MQTT_PASSWORD" ] || [ "$MQTT_PASSWORD" = "CHANGE_ME_mqtt_password" ]; then
            echo "Error: MQTT_USERNAME and MQTT_PASSWORD must be set in .env for split-local profile"
            echo "Use the same credentials configured on the cloud instance"
            exit 1
        fi
        MQTT_BRIDGE_PORT="${MQTT_BRIDGE_PORT:-1883}"
        # Substitute variables in template
        sed -e "s/\${MQTT_BRIDGE_HOST}/$MQTT_BRIDGE_HOST/g" \
            -e "s/\${MQTT_BRIDGE_PORT}/$MQTT_BRIDGE_PORT/g" \
            -e "s/\${MQTT_BRIDGE_USERNAME}/$MQTT_USERNAME/g" \
            -e "s/\${MQTT_BRIDGE_PASSWORD}/$MQTT_PASSWORD/g" \
            mosquitto/mosquitto-split-local.conf.template > mosquitto/config/mosquitto.conf
        ;;
esac

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
docker compose --profile "$PROFILE" pull

echo ""
echo "Starting services..."
docker compose --profile "$PROFILE" up -d

# Wait for services
echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check status
echo ""
echo "=== Service Status ==="
docker compose --profile "$PROFILE" ps

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
echo "Current profile: $PROFILE"
echo ""
echo "To add RL engine (local or split-cloud only):"
echo "  docker compose --profile $PROFILE --profile rl up -d"
