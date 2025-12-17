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
- Devices wait for IP before continuing initialization

## MQTT

Static broker URL configured at build time (`CONFIG_FTS_MQTT_BROKER_URI`).
Works in both modes - broker can be on master's network or external.

## Master Discovery (ESP-NOW)

Independent of WiFi mode, but operates on the same channel as WiFi.
So we wait for Wifi to connect and get IP before starting.

1. Master broadcasts sync packets every 500ms via ESP-NOW
2. Packet contains: magic, run_id, MAC clock timestamp
3. Slaves receive broadcast, extract master's MAC from packet source
4. `FTM_MASTER_MAC_BIT` set in EventGroup when MAC acquired

## FTM (Fine Timing Measurement)

Slaves initiate FTM sessions to master's MAC address.

1. FTM poll task starts on WiFi connect
2. Task waits for `FTM_MASTER_MAC_BIT` (blocks until master discovered)
3. Once MAC known, runs FTM sessions every 1 second
4. Results sent to CRM
