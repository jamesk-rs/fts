#!/bin/bash
# Create additional buckets after InfluxDB init
# This script runs once when the container starts with a fresh volume

set -e

# Wait for InfluxDB to be ready
until influx ping &>/dev/null; do
    echo "Waiting for InfluxDB to be ready..."
    sleep 1
done

# Create health bucket if it doesn't exist
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
