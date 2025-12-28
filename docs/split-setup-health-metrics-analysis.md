# Health Metrics in Split Setup - Analysis and Solutions

**Issue:** System/container health metrics are collected only in the cloud, but Lab should also be covered

**Related:** Issue #5 (MQTT bridge for split setup)

## Problem Statement

In the split setup, the TIG stack (Telegraf, InfluxDB, Grafana) runs in the cloud, while SDR handling code (stream-mqtt) runs in the lab. Currently:

- ✅ **What's working:** SDR data is collected in the lab and pushed to MQTT, which is bridged to the cloud
- ❌ **What's missing:** Lab PC system health metrics (CPU, memory, disk, Docker container stats) are not collected

This creates a blind spot - we can't monitor the health of the lab PC where stream-mqtt is doing CPU-intensive signal processing.

## Current Architecture

### Local Setup (Working)
```
Lab PC
├── Mosquitto
├── InfluxDB
├── Grafana  
├── Telegraf ──┐ (collects system metrics)
│              ├─→ CPU, RAM, Disk
│              ├─→ Docker stats (stream-mqtt)
│              └─→ Writes to InfluxDB
└── stream-mqtt
```

### Split Setup (Current - Missing Lab Metrics)
```
Lab PC                          Cloud
├── Mosquitto ════════════════► Mosquitto
└── stream-mqtt                 ├── InfluxDB
                                ├── Grafana
                                └── Telegraf ──┐ (collects CLOUD metrics only!)
                                               ├─→ Cloud CPU, RAM
                                               └─→ Cloud Docker stats
```

**Problem:** Telegraf in the cloud can't see Lab PC system resources.

## Proposed Solutions

### Option 1: Add Telegraf to split-local Profile ⭐ **RECOMMENDED**

**Approach:** Run Telegraf on the lab PC, configured to send health metrics via MQTT to the cloud.

**Architecture:**
```
Lab PC                          Cloud
├── Mosquitto ════════════════► Mosquitto (bridged)
├── stream-mqtt                 │
└── Telegraf ──┐                ├── Telegraf (reads MQTT + local metrics)
               ├─→ Collects:    │   └─→ Writes to InfluxDB
               │   - CPU        │
               │   - RAM        ├── InfluxDB (health bucket)
               │   - Disk       └── Grafana (shows both lab & cloud)
               │   - Docker
               └─→ Publishes to MQTT:
                   health/lab/* (separate from fts/ namespace)
```

**Pros:**
- ✅ Clean separation: Lab Telegraf collects, Cloud Telegraf writes to InfluxDB
- ✅ Leverages existing MQTT bridge (queuing, reconnection, authentication)
- ✅ Minimal changes to existing code
- ✅ Works with existing authentication
- ✅ Lab metrics queued locally during cloud outages
- ✅ Aligns with current architecture (all data flows through MQTT)
- ✅ Easy to extend for multiple lab locations

**Cons:**
- ⚠️ Adds another container to lab PC (~50MB RAM)
- ⚠️ MQTT overhead for metrics (minimal compared to SDR data ~2000 msgs/sec)

**Implementation Effort:** ~4-6 hours
- New Telegraf config for lab
- Update docker-compose.split-local.yml
- Update cloud Telegraf to consume lab metrics
- Update documentation
- Testing

### Option 2: Telegraf Direct Push to Cloud InfluxDB

**Approach:** Run Telegraf on lab PC, writing directly to cloud InfluxDB over HTTP.

**Architecture:**
```
Lab PC                          Cloud
├── Mosquitto ════════════════► Mosquitto
├── stream-mqtt                 │
└── Telegraf ══════════════════►├── InfluxDB
   (HTTPS writes)               └── Grafana
```

**Pros:**
- ✅ Direct writes to InfluxDB (no MQTT overhead)
- ✅ Simpler: one less MQTT topic set

**Cons:**
- ❌ Requires InfluxDB endpoint exposed to internet (security risk)
- ❌ Limited queuing during outages (Telegraf: memory buffer only, ~10k metrics; then drops old data)
- ❌ Requires managing InfluxDB token on lab PC
- ❌ Bypasses the MQTT bridging infrastructure
- ❌ Doesn't fit the "data flows through MQTT" architecture

**Queuing Comparison:**
- **Telegraf buffer:** Memory-only, limited to `metric_buffer_limit` (default 10,000), drops oldest when full
- **Mosquitto bridge:** Disk-backed persistent queue, unlimited (`max_queued_messages 0`), no data loss until disk full

**NOT RECOMMENDED** due to security and inferior queuing during outages.

### Option 3: Remote Telegraf Agent

**Approach:** Use Telegraf's remote collection capabilities (SSH, exec, http, etc.)

**Pros:**
- ✅ No additional containers on lab PC

**Cons:**
- ❌ Complex setup (SSH keys, network access)
- ❌ Lab PC needs to expose APIs/SSH (security concern)
- ❌ Doesn't fit the MQTT-based architecture
- ❌ More complex authentication and firewall rules

**NOT RECOMMENDED** - doesn't align with current architecture.

## Recommendation: Implement Option 1

**Rationale:**
1. **Architectural fit:** Aligns with existing MQTT-based data flow
2. **Reliability:** Leverages existing bridge reliability (queuing, reconnection)
3. **Security:** All data flows through authenticated MQTT bridge
4. **Separation of concerns:** Lab collects, cloud stores
5. **Scalability:** Easy to extend to multiple lab locations
6. **Minimal overhead:** Telegraf container is lightweight

## Implementation Plan for Option 1

### 1. Create Lab Telegraf Configuration

**New file:** `fts-platform/telegraf/telegraf-split-local.conf`

```toml
[agent]
  hostname = "fts-lab"
  interval = "10s"  # Less frequent than FTS data
  round_interval = true
  metric_batch_size = 1000
  metric_buffer_limit = 10000

# Output to local MQTT (will be bridged to cloud)
[[outputs.mqtt]]
  servers = ["tcp://mosquitto:1883"]
  topic_prefix = "health/lab"
  data_format = "json"
  json_timestamp_units = "1ns"
  layout = "non-batch"  # One message per metric
  # Note: Uses health/ namespace (separate from fts/)

# System CPU metrics
[[inputs.cpu]]
  percpu = true
  totalcpu = true
  collect_cpu_time = false

# Memory metrics
[[inputs.mem]]

# Swap metrics
[[inputs.swap]]

# Disk usage
[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs", "devfs", "iso9660", "overlay", "aufs", "squashfs"]

# Disk I/O
[[inputs.diskio]]

# System load averages
[[inputs.system]]

# Network statistics
[[inputs.net]]

# Docker container metrics
[[inputs.docker]]
  endpoint = "unix:///var/run/docker.sock"
  gather_services = false
  perdevice = true
  total = true
```

### 2. Update Lab Docker Compose

**File:** `fts-platform/docker-compose.split-local.yml`

Add Telegraf service:

```yaml
  telegraf-lab:
    image: telegraf:1.29
    profiles: ["split-local"]
    volumes:
      - ./telegraf/telegraf-split-local.conf:/etc/telegraf/telegraf.conf:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
    environment:
      - HOST_PROC=/host/proc
      - HOST_SYS=/host/sys
    group_add:
      - "${DOCKER_GID}"
    depends_on:
      mosquitto:
        condition: service_healthy
    restart: unless-stopped
```

### 2a. Update Lab Mosquitto Bridge Configuration

**File:** `fts-platform/mosquitto/mosquitto-split-local.conf.template`

Add health topic to bridge forwarding (line 35):

```
topic fts/# out 1
topic health/# out 1
```

### 3. Update Cloud Telegraf Configuration

**File:** `fts-platform/telegraf/telegraf.conf`

Add MQTT consumer for lab health metrics:

```toml
# MQTT Consumer - Lab Health Metrics
[[inputs.mqtt_consumer]]
  servers = ["tcp://mosquitto:1883"]
  username = "${MQTT_USERNAME}"
  password = "${MQTT_PASSWORD}"
  client_id = "telegraf-lab-health"
  persistent_session = true
  topics = ["health/lab/#"]
  qos = 1
  data_format = "json"
  json_time_key = "timestamp"
  json_time_format = "unix_ns"
  tag_keys = ["host", "site"]
  
  # Route to health bucket (add to namepass)
  # This will be handled by the existing health bucket output
```

**Add site tag to cloud metrics:**
Cloud Telegraf should also tag its metrics with `site=cloud` for consistency. Add processor:

```toml
# Tag cloud metrics with site
[[processors.enum]]
  namepass = ["cpu", "mem", "swap", "disk", "diskio", "system", "net", "docker*", "internal*"]
  [[processors.enum.mapping]]
    tag = "site"
    value = "cloud"
```

And update the health bucket output namepass to NOT exclude these:

```toml
# InfluxDB v2 Output - System health metrics
[[outputs.influxdb_v2]]
  urls = ["http://influxdb:8086"]
  token = "${INFLUX_TOKEN}"
  organization = "${INFLUX_ORG}"
  bucket = "${INFLUX_BUCKET_HEALTH}"
  namepass = ["cpu", "mem", "swap", "disk", "diskio", "system", "net", "docker*", "internal*"]
```

### 4. Update Deployment Script

**File:** `fts-platform/bin/03-deploy-stack.sh`

No changes needed - the script already handles split-local profile correctly.

### 5. Update Documentation

**File:** `fts-platform/README.md`

Update the split setup architecture diagram to show lab Telegraf.

### 6. Testing Procedure

1. **Deploy split-local:**
   ```bash
   cd fts-platform
   ./bin/03-deploy-stack.sh split-local
   ```

2. **Verify lab Telegraf is publishing:**
   ```bash
   ./bin/mqtt_peek.py --topic "health/lab/#"
   ```

3. **Verify cloud Telegraf is consuming:**
   ```bash
   docker compose logs -f telegraf
   ```

4. **Check InfluxDB health bucket:**
   ```bash
   ./bin/influx_peek.py --bucket health --measurement cpu
   ```

5. **Verify Grafana dashboards:**
   - Open System Health dashboard
   - Should see metrics tagged with `site=lab` and `site=cloud`
   - Filter by site to view lab or cloud separately

6. **Test disconnection scenario:**
   - Stop cloud Mosquitto
   - Wait 60 seconds (metrics should queue locally)
   - Start cloud Mosquitto
   - Verify metrics catch up

## MQTT Topic Structure

Lab health metrics use the `health/` namespace (separate from `fts/` data):

```
health/lab/cpu         - CPU usage metrics
health/lab/mem         - Memory metrics
health/lab/swap        - Swap usage
health/lab/disk        - Disk usage
health/lab/diskio      - Disk I/O
health/lab/net         - Network stats
health/lab/docker      - Docker container stats
health/lab/system      - System load averages
```

**Note:** The `health/` prefix keeps health metrics separate from FTS measurement data (`fts/` prefix). Both are bridged to cloud via Mosquitto.

Each message will be JSON format with timestamp and metric fields.

## Data Volume Estimate

With 10-second collection interval:
- ~8 metrics × 6 msgs/min = ~48 messages/minute
- ~3,000 messages/hour
- ~70,000 messages/day

Compared to SDR edges (~2,000 msgs/second), this is negligible.

## Grafana Dashboard Changes

The existing health dashboards should automatically pick up lab metrics because:
1. They query the `health` bucket
2. Metrics have the same measurement names
3. The `host` tag will differentiate lab vs cloud

To show both:
- Use `host` variable selector
- Or filter queries by host tag
- Consider adding a "Lab vs Cloud" comparison panel

## Future Enhancements

1. **Multi-lab support:** Each lab can have unique hostname/tag
2. **Lab-specific dashboard:** Create dedicated dashboard for lab PC health
3. **Alerts:** Add Grafana alerts for lab PC high CPU/memory
4. **Custom metrics:** Add stream-mqtt specific metrics (buffer depth, overflow count)

## Telegraf Architecture: Single vs Split Instance

The proposed solution runs two Telegraf instances in cloud:
1. **Lab metrics collector** (new) - Publishes to MQTT (`health/lab/*`)
2. **Cloud MQTT consumer + local collector** (existing) - Reads MQTT, collects cloud metrics, writes to InfluxDB

### Option A: Keep Split (Recommended for Lab, Question for Cloud)

**Lab PC:**
- Separate Telegraf instance makes sense (no InfluxDB available locally)

**Cloud:**
- **Current:** One Telegraf does MQTT consumption + local collection
- **Alternative:** Split into two instances:
  - Instance 1: MQTT consumer only (FTS data + lab health)
  - Instance 2: Local metrics collector only (cloud health)

**Pros of splitting cloud Telegraf:**
- Separation of concerns (data ingestion vs collection)
- Different collection intervals (1s for FTS MQTT, 10s for health)
- Easier to debug and restart independently
- Can scale differently (e.g., more resources to MQTT consumer)

**Cons of splitting cloud Telegraf:**
- One more container to manage
- Slightly more complex configuration

**Recommendation:** Start with single cloud Telegraf (simpler), can split later if needed for performance/debugging.

### Option B: Unified Cloud Telegraf (Current Approach)

Keep one cloud Telegraf that:
- Consumes MQTT (FTS data + lab health metrics)
- Collects local cloud metrics
- Writes everything to InfluxDB

This is simpler and sufficient unless there are performance issues.

## Alternative Considered: InfluxDB Line Protocol

Instead of JSON, we could use InfluxDB line protocol over MQTT:

**Lab Telegraf:**
```toml
[[outputs.mqtt]]
  data_format = "influx"
```

**Cloud Telegraf:**
```toml
[[inputs.mqtt_consumer]]
  data_format = "influx"
```

**Pros:**
- More efficient (smaller messages)
- Native format for InfluxDB

**Cons:**
- Less human-readable for debugging
- Harder to inspect with mqtt_peek.py

**Decision:** Start with JSON for debugging, can optimize later if needed.

## Migration Path

- **New deployments:** Work out of the box with `split-local` profile
- **Existing deployments:** Run `bin/03-deploy-stack.sh split-local` to add Telegraf
- **No breaking changes:** Cloud side is backward compatible
- **Rollback:** Simply stop lab Telegraf container

## Security Considerations

- ✅ Uses existing MQTT authentication
- ✅ No new ports exposed
- ✅ No secrets stored on lab PC (uses local mosquitto)
- ✅ Data encrypted if MQTT bridge uses TLS (future enhancement)

## Performance Impact

- **Lab PC:** +50MB RAM, negligible CPU
- **Network:** ~1 KB/sec additional traffic
- **Cloud:** Negligible impact (existing Telegraf handles it)

## Conclusion

**Recommendation:** Implement Option 1 - Add Telegraf to split-local profile

This solution:
- Provides visibility into lab PC health metrics
- Aligns with existing MQTT-based architecture
- Maintains reliability and security
- Requires minimal changes
- Is easy to test and deploy

**Next Steps:**
1. Review and approve this analysis
2. Implement changes in order listed above
3. Test on development environment
4. Deploy to production split-local setup
5. Update Grafana dashboards to differentiate lab/cloud
