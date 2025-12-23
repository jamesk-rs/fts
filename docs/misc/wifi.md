---
nav_exclude: true
---

# WiFi Architecture

## Operation Modes

### Internal AP Mode

Master runs as WiFi Access Point. Slaves connect to master's AP.
- Master: `ftm_master_init()` - creates AP with FTM responder enabled
- Slaves: connect to master's SSID
- All traffic (MQTT, FTM) flows through master's network

### External AP Mode

All devices connect to an external WiFi AP (e.g., home router).
- Master: `ftm_master_init_sta()` - connects as STA
- Slaves: connect to same AP
- All devices wait for IP before continuing initialization

Note that slave might end up on different channel from the master.
This is bad, FTM will not work. This error is detected at master discovery phase (see below) and wifi reconnects hoping to get more lucky next time.

## MQTT

Static broker URL configured at build time (`CONFIG_FTS_MQTT_BROKER_URI`).
Works in both modes - broker can be on master's network or external.

## Master Discovery

Works over IP, so we wait for Wifi to connect and get IP before starting.

1. Master broadcasts sync packets every 500ms, over UDP
2. Packet contains: magic, master's MAC address, FTM channel, run_id, MAC clock timestamp
3. Slaves receive broadcast, extract master's MAC and channel from packet source
4. If slave sees it is on differnt channel, it triggers wifi reconnect
5. `FTM_MASTER_SYNC_BIT` set in EventGroup when MAC acquired

## FTM (Fine Timing Measurement)

Slaves initiate FTM sessions to master's MAC address.

1. FTM poll task starts on WiFi connect
2. Task waits for `FTM_MASTER_SYNC_BIT` (blocks until master discovered)
3. Once master's MAC and channel are known, runs FTM sessions every 1 second
4. Results sent to CRM
