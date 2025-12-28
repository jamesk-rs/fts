#!/bin/sh
# Enable public dashboards for specified dashboard UIDs via Grafana API

GRAFANA_URL="${GRAFANA_URL:-http://grafana:3000}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-admin}"

# Dashboards to make public (space-separated)
DASHBOARD_UIDS="sdr-stats-dashboard ftm-stats-dashboard fts-timing"

echo "Waiting for Grafana to be ready..."
until curl -sf "${GRAFANA_URL}/api/health" > /dev/null 2>&1; do
    sleep 2
done
echo "Grafana is ready"

for uid in $DASHBOARD_UIDS; do
    echo "Enabling public dashboard for: $uid"

    # Check if public dashboard already exists and is enabled
    existing=$(curl -sf -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
        "${GRAFANA_URL}/api/dashboards/uid/${uid}/public-dashboards/" 2>/dev/null)

    if echo "$existing" | grep -q '"isEnabled":true'; then
        access_token=$(echo "$existing" | sed -n 's/.*"accessToken":"\([^"]*\)".*/\1/p')
        echo "  Already public: ${GRAFANA_URL}/public-dashboards/${access_token}"
        continue
    fi

    # Create public dashboard
    response=$(curl -sf -X POST \
        -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
        -H "Content-Type: application/json" \
        -d '{"isEnabled": true, "timeSelectionEnabled": true, "annotationsEnabled": false}' \
        "${GRAFANA_URL}/api/dashboards/uid/${uid}/public-dashboards/")

    if [ $? -eq 0 ]; then
        access_token=$(echo "$response" | sed -n 's/.*"accessToken":"\([^"]*\)".*/\1/p')
        echo "  Public URL: ${GRAFANA_URL}/public-dashboards/${access_token}"
    else
        echo "  Failed to enable public dashboard"
    fi
done

echo "Done configuring public dashboards"
