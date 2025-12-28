"""
SDR Edge Publisher

Publishes edge timing data from SDR (UHD) to the FTS MQTT broker
for correlation with FTM reports in the RL engine.

Usage:
    # As a module in your UHD script:
    from sdr_publisher import SDRPublisher

    publisher = SDRPublisher("192.168.1.100")  # MQTT broker IP

    for edge_a_ns, edge_b_ns in detect_edges(samples):
        publisher.publish_edge(edge_a_ns, edge_b_ns)

    # Or run standalone for testing:
    python sdr_publisher.py --host 192.168.1.100 --test
"""

import argparse
import json
import time
import logging
from typing import Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class SDRPublisher:
    """
    Publishes SDR edge timing data to MQTT broker.

    The RL engine subscribes to these edges and correlates them
    with FTM reports to compute phase error.
    """

    def __init__(
        self,
        mqtt_host: str = "localhost",
        mqtt_port: int = 1883,
        topic: str = "fts/sdr/edges",
        client_id: Optional[str] = None,
    ):
        """
        Initialize SDR publisher.

        Args:
            mqtt_host: MQTT broker hostname or IP
            mqtt_port: MQTT broker port (default 1883)
            topic: MQTT topic for edge data
            client_id: Optional MQTT client ID
        """
        self.topic = topic
        self.connected = False

        # Create MQTT client
        self.mqtt = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or f"sdr-publisher-{int(time.time())}",
        )
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_disconnect = self._on_disconnect

        # Connect
        logger.info(f"Connecting to MQTT broker at {mqtt_host}:{mqtt_port}")
        self.mqtt.connect(mqtt_host, mqtt_port, keepalive=60)
        self.mqtt.loop_start()

        # Wait for connection
        timeout = 5.0
        start = time.time()
        while not self.connected and (time.time() - start) < timeout:
            time.sleep(0.1)

        if not self.connected:
            raise ConnectionError(f"Failed to connect to MQTT broker at {mqtt_host}:{mqtt_port}")

        logger.info(f"Connected to MQTT broker, publishing to {topic}")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT connection."""
        if reason_code == 0:
            logger.info("MQTT connected")
            self.connected = True
        else:
            logger.error(f"MQTT connection failed: {reason_code}")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT disconnection."""
        logger.warning(f"MQTT disconnected: {reason_code}")
        self.connected = False

    def publish_edge(
        self,
        channel_a_ns: Optional[int] = None,
        channel_b_ns: Optional[int] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        """
        Publish edge timing to MQTT.

        Can publish:
        - Matched edges: both channel_a_ns and channel_b_ns provided
        - Unmatched A edge: only channel_a_ns provided
        - Unmatched B edge: only channel_b_ns provided

        Args:
            channel_a_ns: Channel A edge time in nanoseconds (optional)
            channel_b_ns: Channel B edge time in nanoseconds (optional)
            timestamp: Optional timestamp (default: current time)

        Returns:
            True if published successfully
        """
        if not self.connected:
            logger.warning("Not connected to MQTT broker")
            return False

        if channel_a_ns is None and channel_b_ns is None:
            logger.warning("publish_edge called with no edge data")
            return False

        payload = {"ts": timestamp or time.time()}

        if channel_a_ns is not None:
            payload["channel_a_edge_ns"] = channel_a_ns
        if channel_b_ns is not None:
            payload["channel_b_edge_ns"] = channel_b_ns

        result = self.mqtt.publish(self.topic, json.dumps(payload), qos=0)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def publish_stats(
        self,
        stats: dict,
        overflow_count: int = 0,
        channel_stats: dict = None,
        topic: str = "fts/sdr/stats",
    ) -> bool:
        """
        Publish rolling window statistics to MQTT.

        Args:
            stats: Dictionary with stats (from JitterStats.to_dict())
            overflow_count: UHD buffer overflow count
            channel_stats: Per-channel edge statistics (channel_a_edges, channel_b_edges)
            topic: MQTT topic for stats (default: fts/sdr/stats)

        Returns:
            True if published successfully
        """
        if not self.connected:
            logger.warning("Not connected to MQTT broker")
            return False

        payload = {
            "ts": time.time(),
            "count": stats.get('count', 0),
            "mean_ns": stats.get('mean_ns', 0.0),
            "std_ns": stats.get('std_ns', 0.0),
            "min_ns": stats.get('min_ns', 0.0),
            "max_ns": stats.get('max_ns', 0.0),
            "p50_ns": stats.get('p50_ns', 0.0),
            "p95_ns": stats.get('p95_ns', 0.0),
            "p99_ns": stats.get('p99_ns', 0.0),
            "p999_ns": stats.get('p999_ns', 0.0),
            "overflow_count": overflow_count,
        }

        # Add per-channel stats if provided
        if channel_stats:
            payload["channel_a_edges"] = channel_stats.get('channel_a_edges', 0)
            payload["channel_b_edges"] = channel_stats.get('channel_b_edges', 0)
            payload["matched_count"] = channel_stats.get('matched_count', 0)

        result = self.mqtt.publish(topic, json.dumps(payload), qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def publish_phase_noise(
        self,
        phase_noise: dict,
        timestamp: float,
        topic: str = "fts/sdr/phase_noise",
    ) -> bool:
        """
        Publish phase noise data to MQTT.

        Args:
            phase_noise: Dictionary from PhaseNoiseResult.to_dict()
            timestamp: Timestamp for this measurement (minute boundary)
            topic: MQTT topic for phase noise (default: fts/sdr/phase_noise)

        Returns:
            True if published successfully
        """
        if not self.connected:
            logger.warning("Not connected to MQTT broker")
            return False

        payload = {
            "ts": timestamp,
            **phase_noise,
        }

        result = self.mqtt.publish(topic, json.dumps(payload), qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def publish_edge_batch(
        self,
        edges: list[tuple[int, int]],
        start_timestamp: Optional[float] = None,
        sample_rate: float = 10e6,
    ) -> int:
        """
        Publish a batch of matched edges with computed timestamps.

        Args:
            edges: List of (channel_a_ns, channel_b_ns) tuples
            start_timestamp: Timestamp of first edge
            sample_rate: Sample rate for computing inter-edge timestamps

        Returns:
            Number of edges published successfully
        """
        ts = start_timestamp or time.time()
        count = 0

        for i, (ch_a, ch_b) in enumerate(edges):
            # Compute timestamp based on sample position
            edge_ts = ts + (i / sample_rate)
            if self.publish_edge(channel_a_ns=ch_a, channel_b_ns=ch_b, timestamp=edge_ts):
                count += 1

        return count

    def close(self):
        """Close MQTT connection."""
        self.mqtt.loop_stop()
        self.mqtt.disconnect()
        logger.info("Disconnected from MQTT broker")


def test_publisher(host: str, port: int, count: int, interval: float):
    """Test publisher with synthetic data."""
    publisher = SDRPublisher(host, port)

    print(f"Publishing {count} test edges to {host}:{port}...")

    base_delay_ns = 100  # 100ns base delay
    jitter_ns = 10  # ±10ns jitter

    import random

    for i in range(count):
        # Generate synthetic edge data
        ch_a = i * 500_000  # 500µs period in ns
        delay = base_delay_ns + random.randint(-jitter_ns, jitter_ns)
        ch_b = ch_a + delay

        if publisher.publish_edge(channel_a_ns=ch_a, channel_b_ns=ch_b):
            print(f"  Edge {i+1}: delay={delay}ns")
        else:
            print(f"  Edge {i+1}: FAILED")

        time.sleep(interval)

    publisher.close()
    print("Done")


def main():
    parser = argparse.ArgumentParser(description="SDR Edge Publisher")
    parser.add_argument(
        "--host", default="localhost", help="MQTT broker host"
    )
    parser.add_argument(
        "--port", type=int, default=1883, help="MQTT broker port"
    )
    parser.add_argument(
        "--test", action="store_true", help="Run test mode with synthetic data"
    )
    parser.add_argument(
        "--count", type=int, default=100, help="Number of test edges"
    )
    parser.add_argument(
        "--interval", type=float, default=0.5, help="Interval between test edges (seconds)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose output"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.test:
        test_publisher(args.host, args.port, args.count, args.interval)
    else:
        print("Use --test to run test mode, or import SDRPublisher in your UHD script")


if __name__ == "__main__":
    main()
