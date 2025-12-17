#!/usr/bin/env python3
"""InfluxDB data viewer for FTS."""

import argparse
import sys
from datetime import datetime

try:
    from influxdb_client import InfluxDBClient
except ImportError:
    print("Install influxdb-client: pip install influxdb-client")
    sys.exit(1)

DEFAULT_URL = "http://localhost:8086"
DEFAULT_TOKEN = "fts-token-change-me"
DEFAULT_ORG = "fts"
DEFAULT_BUCKET = "fts"


def query_measurement(client, bucket, measurement, range_str, limit):
    """Query a specific measurement."""
    query = f'''
from(bucket: "{bucket}")
  |> range(start: {range_str})
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {limit})
'''
    query_api = client.query_api()
    tables = query_api.query(query)

    results = []
    for table in tables:
        for record in table.records:
            results.append(record.values)
    return results


def format_record(record, measurement):
    """Format a record for display."""
    ts = record.get("_time")
    if ts:
        ts_str = ts.strftime("%H:%M:%S.%f")[:-3]
    else:
        ts_str = "??:??:??"

    device = record.get("device", "?")

    if measurement == "ftm":
        rtt = record.get("rtt_ps", "?")
        rssi = record.get("rssi", "?")
        t1 = record.get("t1", "?")
        return f"[{ts_str}] {device}: RTT={rtt}ps RSSI={rssi}dBm t1={t1}"
    elif measurement == "ftm_stats":
        rtt_avg = record.get("rtt_avg_ps", "?")
        rtt_min = record.get("rtt_min_ps", "?")
        rtt_max = record.get("rtt_max_ps", "?")
        rssi_avg = record.get("rssi_avg", "?")
        count = record.get("count", "?")
        return f"[{ts_str}] {device}: RTT={rtt_avg}ps [{rtt_min},{rtt_max}] RSSI={rssi_avg}dBm cnt={count}"
    elif measurement == "metrics":
        cycle = record.get("cycle_counter", "?")
        period = record.get("period_ticks", "?")
        delta = record.get("period_delta", "?")
        return f"[{ts_str}] {device}: cycle={cycle} period={period} delta={delta}"
    elif measurement == "rl_action":
        corr = record.get("correction_fp16", "?")
        phase = record.get("phase_error_ns", "?")
        gain = record.get("gain_K", "?")
        return f"[{ts_str}] {device}: correction={corr} phase_error={phase}ns K={gain}"
    elif measurement == "edges":
        delay = record.get("delay_ns", "?")
        ch_a = record.get("channel_a_ns", "?")
        ch_b = record.get("channel_b_ns", "?")
        return f"[{ts_str}] delay={delay}ns ch_a={ch_a} ch_b={ch_b}"
    else:
        return f"[{ts_str}] {record}"


def main():
    parser = argparse.ArgumentParser(description="InfluxDB data viewer for FTS")
    parser.add_argument("-u", "--url", default=DEFAULT_URL, help=f"InfluxDB URL (default: {DEFAULT_URL})")
    parser.add_argument("-t", "--token", default=DEFAULT_TOKEN, help="InfluxDB token")
    parser.add_argument("-o", "--org", default=DEFAULT_ORG, help=f"Organization (default: {DEFAULT_ORG})")
    parser.add_argument("-b", "--bucket", default=DEFAULT_BUCKET, help=f"Bucket (default: {DEFAULT_BUCKET})")
    parser.add_argument("-m", "--measurement", default="ftm",
                        choices=["ftm", "ftm_stats", "metrics", "rl_action", "edges", "all"],
                        help="Measurement to query (default: ftm)")
    parser.add_argument("-r", "--range", default="-5m", help="Time range (default: -5m)")
    parser.add_argument("-n", "--limit", type=int, default=20, help="Max records (default: 20)")
    parser.add_argument("-w", "--watch", action="store_true", help="Watch mode (poll every 2s)")
    args = parser.parse_args()

    try:
        client = InfluxDBClient(url=args.url, token=args.token, org=args.org)
        # Test connection
        client.ping()
        print(f"Connected to {args.url}")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    measurements = ["ftm", "ftm_stats", "metrics", "rl_action", "edges"] if args.measurement == "all" else [args.measurement]

    import time
    try:
        while True:
            if args.watch:
                print("\033[2J\033[H", end="")  # Clear screen
                print(f"InfluxDB: {args.url} | Range: {args.range} | {datetime.now().strftime('%H:%M:%S')}")
                print("-" * 60)

            for measurement in measurements:
                results = query_measurement(client, args.bucket, measurement, args.range, args.limit)

                if args.measurement == "all":
                    print(f"\n=== {measurement.upper()} ({len(results)} records) ===")
                else:
                    print(f"Measurement: {measurement} ({len(results)} records)")
                    print("-" * 60)

                if not results:
                    print("  (no data)")
                else:
                    for record in results:
                        print(format_record(record, measurement))

            if not args.watch:
                break
            time.sleep(2)

    except KeyboardInterrupt:
        print("\nDone")
    finally:
        client.close()


if __name__ == "__main__":
    main()
