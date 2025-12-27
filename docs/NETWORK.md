# Network Architecture

## Operation Modes

FTS supports three network modes for timing synchronization and telemetry uplink.

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

#### Linux Host Setup

```bash
# Set up USB interface (appears as usb0 or enx*)
sudo ip link set usb0 up
sudo ip addr add 192.168.7.1/24 dev usb0

# Run DHCP server
sudo dnsmasq -d -i usb0 --dhcp-range=192.168.7.2,192.168.7.10,12h

# Enable IP forwarding for MQTT access
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
```

## MQTT

Static broker URL configured at build time (`CONFIG_FTS_MQTT_BROKER_URI`).
Works in all modes - broker can be on master's network, external AP, or via USB uplink.

## Master Discovery

Works over IP or ESP-NOW. For IP we wait for WiFi to connect and get IP before starting.

1. Master broadcasts sync packets every 500ms (over UDP or ESP-NOW)
2. Packet contains: magic, master's MAC address, FTM channel, run_id, MAC clock timestamp
3. Slaves receive broadcast, extract master's MAC and channel from packet source
4. If slave sees its channel does not match master's, it triggers WiFi reconnect
