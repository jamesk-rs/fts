---
title: Network Operation Modes
date: 2025-12-20
nav_order: 3
has_toc: true
---
# Network Operation Modes

FTS supports three network modes for timing synchronization and telemetry uplink.

## Table of Contents
- TOC
{:toc}

## Network Operation Modes
### Internal AP Mode (`CONFIG_FTS_MODE_INTERNAL_AP`)

Master runs as WiFi Access Point. Slaves connect to master's AP.

- **Master**: creates AP with FTM responder enabled
- **Slaves**: connect to master's SSID
- **FTM**: shares channel with master's AP
- **Telemetry**: via master's network (master routes to broker)

### External AP Mode (`CONFIG_FTS_MODE_EXTERNAL_AP`)

All devices connect to an external WiFi AP.

- **Master**: connects as STA
- **Slaves**: connects as STA
- **FTM**: shares channel with external AP
- **Telemetry**: via external AP to broker

Note: Slave might end up on different channel from the master.
This is detected at master discovery phase and WiFi reconnects hoping to get more lucky next time.

### USB-NCM Mode (`CONFIG_FTS_MODE_USB_NCM`)

ESP-NOW for timing sync, USB-NCM for telemetry uplink. Requires ESP32-S3.

- **FTM**: master discovery via ESP-NOW broadcast (preconfigured channel)
- **Telemetry**: via USB-NCM to Linux host
- ESP32 acts as USB network device (DHCP client)
- Linux host provides DHCP server and routes to MQTT broker

## Master Discovery

Works over IP or ESP-NOW. For IP we wait for WiFi to connect and get IP before starting.

1. Master broadcasts sync packets every 500ms (over UDP or ESP-NOW)
2. Packet contains: magic, master's MAC address, FTM channel, run_id, MAC clock timestamp
3. Slaves receive broadcast, extract master's MAC and channel from packet source
4. If slave sees its channel does not match master's, it triggers WiFi reconnect
