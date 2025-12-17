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

# Topic patterns for each message type
TOPIC_PATTERNS = {
    "ftm": "fts/+/ftm",
    "ftm_stats": "fts/+/ftm_stats",
    "metrics": "fts/+/metrics",
    "edges": "fts/sdr/edges",
    "all": "fts/#",
}


def format_ftm(payload, device):
    """Format FTM message for display."""
    rtt = payload.get("rtt_ps", "?")
    rssi = payload.get("rssi", "?")
    t1 = payload.get("t1", "?")
    return f"{device}: RTT={rtt}ps RSSI={rssi}dBm t1={t1}"


def format_ftm_stats(payload, device):
    """Format FTM stats message for display."""
    rtt_avg = payload.get("rtt_avg_ps", "?")
    rtt_min = payload.get("rtt_min_ps", "?")
    rtt_max = payload.get("rtt_max_ps", "?")
    rssi_avg = payload.get("rssi_avg", "?")
    count = payload.get("count", "?")
    status = payload.get("status", "?")
    return f"{device}: RTT={rtt_avg}ps [{rtt_min},{rtt_max}] RSSI={rssi_avg}dBm cnt={count} st={status}"


def format_metrics(payload, device):
    """Format metrics message for display."""
    cycle = payload.get("cycle_counter", "?")
    period = payload.get("period_ticks", "?")
    delta = payload.get("period_delta", "?")
    return f"{device}: cycle={cycle} period={period} delta={delta}"


def format_edges(payload):
    """Format edges message for display."""
    delay = payload.get("delay_ns", "?")
    ch_a = payload.get("channel_a_edge_ns", "?")
    ch_b = payload.get("channel_b_edge_ns", "?")
    return f"delay={delay}ns ch_a={ch_a} ch_b={ch_b}"


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

    # Extract device from topic (fts/{device}/...)
    parts = topic.split("/")
    device = parts[1] if len(parts) > 1 else "?"
    msg_type = parts[2] if len(parts) > 2 else parts[-1]

    # Try to parse as JSON
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = None

    # Format output based on mode
    if userdata.get('formatted') and payload:
        if msg_type == "ftm":
            formatted = format_ftm(payload, device)
        elif msg_type == "ftm_stats":
            formatted = format_ftm_stats(payload, device)
        elif msg_type == "metrics":
            formatted = format_metrics(payload, device)
        elif msg_type == "edges":
            formatted = format_edges(payload)
        else:
            formatted = json.dumps(payload, separators=(',', ':'))
        print(f"[{ts}] {formatted}")
    else:
        # Raw JSON output
        if payload:
            if userdata.get('compact'):
                payload_str = json.dumps(payload, separators=(',', ':'))
            else:
                payload_str = json.dumps(payload, indent=2)
        else:
            payload_str = msg.payload.decode('utf-8', errors='replace')
        print(f"[{ts}] {topic}")
        print(payload_str)
        print()


def main():
    parser = argparse.ArgumentParser(description="MQTT message viewer for FTS")
    parser.add_argument("-b", "--broker", default=DEFAULT_BROKER, help=f"Broker address (default: {DEFAULT_BROKER})")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help=f"Broker port (default: {DEFAULT_PORT})")
    parser.add_argument("-m", "--message-type", default="all",
                        choices=list(TOPIC_PATTERNS.keys()),
                        help="Message type to subscribe (default: all)")
    parser.add_argument("-t", "--topic", default=None, help="Custom topic (overrides -m)")
    parser.add_argument("-c", "--compact", action="store_true", help="Compact JSON output")
    parser.add_argument("-f", "--formatted", action="store_true", help="Formatted single-line output")
    args = parser.parse_args()

    # Determine topic from message type or custom topic
    topic = args.topic if args.topic else TOPIC_PATTERNS.get(args.message_type, DEFAULT_TOPIC)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.user_data_set({
        'broker': args.broker,
        'port': args.port,
        'topic': topic,
        'compact': args.compact,
        'formatted': args.formatted,
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
