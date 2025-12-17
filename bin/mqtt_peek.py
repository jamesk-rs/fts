#!/usr/bin/env python3
"""MQTT message viewer for FTS devices."""

import argparse
import json
import sys
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Install paho-mqtt: pip install paho-mqtt")
    sys.exit(1)

DEFAULT_BROKER = "192.168.129.206"
DEFAULT_PORT = 1883
DEFAULT_TOPIC = "fts/#"


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"Connected to {userdata['broker']}:{userdata['port']}")
        client.subscribe(userdata['topic'])
        print(f"Subscribed to: {userdata['topic']}")
        print("-" * 60)
    else:
        print(f"Connection failed: {rc}")


def on_message(client, userdata, msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    topic = msg.topic

    # Try to parse as JSON for pretty printing
    try:
        payload = json.loads(msg.payload.decode())
        if userdata.get('compact'):
            payload_str = json.dumps(payload, separators=(',', ':'))
        else:
            payload_str = json.dumps(payload, indent=2)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload_str = msg.payload.decode('utf-8', errors='replace')

    print(f"[{ts}] {topic}")
    print(payload_str)
    print()


def main():
    parser = argparse.ArgumentParser(description="MQTT message viewer for FTS")
    parser.add_argument("-b", "--broker", default=DEFAULT_BROKER, help=f"Broker address (default: {DEFAULT_BROKER})")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help=f"Broker port (default: {DEFAULT_PORT})")
    parser.add_argument("-t", "--topic", default=DEFAULT_TOPIC, help=f"Topic to subscribe (default: {DEFAULT_TOPIC})")
    parser.add_argument("-c", "--compact", action="store_true", help="Compact JSON output")
    args = parser.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.user_data_set({
        'broker': args.broker,
        'port': args.port,
        'topic': args.topic,
        'compact': args.compact,
    })
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(args.broker, args.port, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nDisconnected")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
