# Issue Analysis: Health Metrics in Split Setup

**Issue:** System/container health metrics are collected only in the cloud, but Lab should also be covered

**Status:** ✅ Analysis complete, awaiting approval to implement

---

## Problem Summary

In the split setup, Telegraf runs only in the cloud and collects metrics from the cloud host. The lab PC running stream-mqtt (doing CPU-intensive SDR signal processing) has no health monitoring, creating a blind spot.

## Root Cause

The split-local profile only runs Mosquitto and stream-mqtt. Telegraf was intentionally excluded because it needs InfluxDB, which is in the cloud.

## Recommended Solution ⭐

**Add Telegraf to split-local profile** to collect lab PC health metrics and publish them via MQTT.

### Architecture

```
Lab PC                          Cloud
├── Mosquitto ════════════════► Mosquitto (bridged)
├── stream-mqtt                 │
└── Telegraf ──┐                ├── Telegraf (reads MQTT + collects cloud metrics)
               ├─→ Collects:    │   └─→ Writes to InfluxDB
               │   - CPU        │
               │   - RAM        ├── InfluxDB (health bucket)
               │   - Disk       └── Grafana (shows both lab & cloud)
               │   - Docker
               └─→ Publishes to: fts/lab/health/*
```

### Why This Solution?

✅ **Aligns with architecture:** All data flows through MQTT bridge  
✅ **Reliable:** Leverages existing bridge queuing/reconnection  
✅ **Secure:** Uses existing MQTT authentication  
✅ **Scalable:** Easy to add multiple labs  
✅ **Minimal overhead:** ~50MB RAM, ~1 KB/sec network  

### Alternatives Considered

❌ **Direct InfluxDB writes:** Security risk, no queuing, bypasses MQTT  
❌ **Remote collection (SSH):** Complex, doesn't fit architecture  

## Implementation Plan

**Estimated effort:** 4-6 hours

1. **Create** `telegraf/telegraf-split-local.conf` - Lab Telegraf config with MQTT output
2. **Update** `docker-compose.split-local.yml` - Add telegraf-lab service
3. **Update** `telegraf/telegraf.conf` - Add MQTT consumer for `fts/lab/health/#`
4. **Update** `README.md` - Document new architecture
5. **Test** - Verify metrics flow: lab → MQTT → cloud → InfluxDB → Grafana

## Detailed Analysis

See **[split-setup-health-metrics-analysis.md](split-setup-health-metrics-analysis.md)** for:
- Complete architecture diagrams for all 3 options
- Detailed pros/cons analysis
- Complete implementation steps with code examples
- Testing procedures
- MQTT topic structure
- Data volume estimates
- Security considerations
- Future enhancements

## Key Design Decisions

| Decision | Value | Rationale |
|----------|-------|-----------|
| **Collection interval** | 10 seconds | Less frequent than FTS data (1s), sufficient for system metrics |
| **Data format** | JSON | Human-readable for debugging, can optimize to line protocol later |
| **Topic prefix** | `fts/lab/health/` | Consistent with existing topic structure |
| **Output method** | MQTT publish | Leverages existing bridge infrastructure |

## Data Flow

```
Lab Telegraf → Local MQTT → Bridge → Cloud MQTT → Cloud Telegraf → InfluxDB → Grafana
  (collect)     (publish)   (queue)   (forward)    (consume)        (store)    (display)
```

**Reliability:** If cloud is down, metrics queue locally on lab Mosquitto and are delivered when connection resumes.

## Impact Analysis

| Component | Impact | Details |
|-----------|--------|---------|
| **Lab PC** | +50MB RAM | One additional Telegraf container |
| **Network** | +1 KB/sec | Negligible vs 2000 SDR msgs/sec |
| **Cloud** | Negligible | Existing Telegraf handles additional topics |
| **Security** | None | Uses existing MQTT auth |

## Testing Checklist

- [ ] Deploy split-local with new Telegraf
- [ ] Verify MQTT messages on `fts/lab/health/#` using mqtt_peek.py
- [ ] Verify cloud Telegraf logs show consumption
- [ ] Check data in InfluxDB health bucket
- [ ] Verify Grafana dashboards show lab metrics (tagged `host=fts-lab`)
- [ ] Test disconnection scenario (bridge down/up)
- [ ] Verify metrics queue and catch up after reconnection

## Open Questions

1. **Collection interval:** Is 10 seconds acceptable? (vs 1s for FTS data)
2. **Multi-lab support:** Should metrics include lab location tag?
3. **Dashboard:** Create separate dashboard for lab metrics or integrate into existing?
4. **Approval:** Proceed with implementation?

## Next Steps

- [x] Complete analysis
- [x] Document solutions
- [ ] Get approval from maintainer
- [ ] Implement changes
- [ ] Test on development environment
- [ ] Deploy to production
- [ ] Update Grafana dashboards

---

**For questions or to approve implementation, please comment on the issue.**
