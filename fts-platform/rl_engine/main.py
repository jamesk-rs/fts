"""
FTS Real-Time RL Engine

Correlates FTM reports from ESP32 devices with SDR edge timing data,
computes squared phase error as reward, and sends DTC period corrections
back to devices via MQTT.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import paho.mqtt.client as mqtt
import redis
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from config import Config

# Configure logging
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rl_engine")


@dataclass
class DeviceState:
    """Current state of a device."""

    device_id: str
    last_ftm_ts: float = 0.0
    last_edge_ts: float = 0.0
    last_metrics_ts: float = 0.0
    phase_error_ns: float = 0.0
    period_ticks: int = 20000  # Default 500us at 40MHz
    cumulative_reward: float = 0.0
    last_reward: Optional[float] = None
    target_delay_ns: float = 0.0  # Target phase alignment
    ftm_buffer: list = field(default_factory=list)  # Buffer recent FTM reports


@dataclass
class EdgeEvent:
    """SDR edge timing event."""

    ts: float
    channel_a_ns: int
    channel_b_ns: int
    delay_ns: int


class RLEngine:
    """
    Real-time RL engine for clock synchronization.

    Receives:
    - FTM reports from ESP32 devices (via MQTT)
    - Timing metrics from ESP32 devices (via MQTT)
    - Edge timing from SDR (via MQTT)

    Outputs:
    - Period corrections to ESP32 devices (via MQTT)
    """

    def __init__(self):
        logger.info("Initializing RL Engine...")

        # Device states
        self.states: dict[str, DeviceState] = {}

        # RL parameters - per-device gain (K)
        self.K: dict[str, float] = defaultdict(lambda: Config.INITIAL_GAIN)

        # Recent edge events for correlation
        self.edge_buffer: list[EdgeEvent] = []
        self.edge_buffer_max_age_s = 1.0  # Keep 1 second of edges

        # Initialize clients
        self._init_mqtt()
        self._init_influxdb()
        self._init_redis()

        logger.info("RL Engine initialized")

    def _init_mqtt(self):
        """Initialize MQTT client."""
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt.on_connect = self._on_mqtt_connect
        self.mqtt.on_message = self._on_mqtt_message

        logger.info(f"Connecting to MQTT broker at {Config.MQTT_HOST}:{Config.MQTT_PORT}")
        self.mqtt.connect(Config.MQTT_HOST, Config.MQTT_PORT, keepalive=60)

    def _init_influxdb(self):
        """Initialize InfluxDB client."""
        logger.info(f"Connecting to InfluxDB at {Config.INFLUX_URL}")
        self.influx = InfluxDBClient(
            url=Config.INFLUX_URL,
            token=Config.INFLUX_TOKEN,
            org=Config.INFLUX_ORG,
        )
        self.influx_write = self.influx.write_api(write_options=SYNCHRONOUS)

    def _init_redis(self):
        """Initialize Redis client."""
        logger.info(f"Connecting to Redis at {Config.REDIS_HOST}:{Config.REDIS_PORT}")
        self.redis = redis.Redis(
            host=Config.REDIS_HOST,
            port=Config.REDIS_PORT,
            decode_responses=True,
        )

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT connection."""
        if reason_code == 0:
            logger.info("Connected to MQTT broker")
            # Subscribe to FTS topics
            client.subscribe("fts/+/ftm")
            client.subscribe("fts/+/metrics")
            client.subscribe("fts/sdr/edges")
            logger.info("Subscribed to FTS topics")
        else:
            logger.error(f"MQTT connection failed: {reason_code}")

    def _on_mqtt_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode())

            if "/ftm" in topic:
                device_id = topic.split("/")[1]
                self._process_ftm(device_id, payload)
            elif "/metrics" in topic:
                device_id = topic.split("/")[1]
                self._process_metrics(device_id, payload)
            elif "/edges" in topic:
                self._process_edges(payload)

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

    def _get_or_create_state(self, device_id: str) -> DeviceState:
        """Get or create device state."""
        if device_id not in self.states:
            self.states[device_id] = DeviceState(device_id=device_id)
            logger.info(f"Created state for device: {device_id}")
        return self.states[device_id]

    def _process_ftm(self, device_id: str, payload: dict):
        """Process FTM report from device."""
        state = self._get_or_create_state(device_id)
        now = time.time()
        device_ts = payload.get("ts", now)

        # Update state
        state.last_ftm_ts = now
        state.ftm_buffer.append(payload)

        # Keep only recent FTM reports (use device_ts for relative comparison)
        cutoff = device_ts - 1.0
        state.ftm_buffer = [f for f in state.ftm_buffer if f.get("ts", 0) > cutoff]

        # Note: InfluxDB write handled by Telegraf

        # Update Redis cache
        self.redis.hset(
            f"device:{device_id}",
            mapping={
                "last_ftm": json.dumps(payload),
                "last_ftm_ts": str(device_ts),
            },
        )

        logger.debug(f"FTM from {device_id}: RTT={payload.get('rtt_ps')}ps")

    def _process_metrics(self, device_id: str, payload: dict):
        """Process timing metrics from device."""
        state = self._get_or_create_state(device_id)
        now = time.time()

        # Update state
        state.last_metrics_ts = now
        state.period_ticks = payload.get("period_ticks", state.period_ticks)

        # Note: InfluxDB write handled by Telegraf

        logger.debug(f"Metrics from {device_id}: period={payload.get('period_ticks')}")

    def _process_edges(self, payload: dict):
        """Process SDR edge data and correlate with device data."""
        ts = payload.get("ts", time.time())
        delay_ns = payload.get("delay_ns", 0)

        # Create edge event
        edge = EdgeEvent(
            ts=ts,
            channel_a_ns=payload.get("channel_a_edge_ns", 0),
            channel_b_ns=payload.get("channel_b_edge_ns", 0),
            delay_ns=delay_ns,
        )

        # Add to buffer
        self.edge_buffer.append(edge)

        # Prune old edges
        cutoff = ts - self.edge_buffer_max_age_s
        self.edge_buffer = [e for e in self.edge_buffer if e.ts > cutoff]

        # Note: InfluxDB write handled by Telegraf

        # Try to correlate with recent FTM data from each device
        self._correlate_and_update(edge)

        logger.debug(f"Edge: delay={delay_ns}ns")

    def _correlate_and_update(self, edge: EdgeEvent):
        """Correlate edge with FTM data and compute RL update."""
        correlation_window_s = Config.CORRELATION_WINDOW_MS / 1000.0

        for device_id, state in self.states.items():
            # Check if we have recent FTM data
            if abs(state.last_ftm_ts - edge.ts) > correlation_window_s:
                continue

            # Compute phase error (delay from target)
            phase_error_ns = edge.delay_ns - state.target_delay_ns

            # Compute reward (negative squared error)
            reward = -(phase_error_ns ** 2)

            # Update state
            state.phase_error_ns = phase_error_ns
            state.cumulative_reward += reward
            state.last_edge_ts = edge.ts

            # Compute and send correction
            self._compute_and_send_correction(device_id, state, reward)

            logger.info(
                f"Correlated {device_id}: phase_error={phase_error_ns:.1f}ns, "
                f"reward={reward:.0f}"
            )

    def _compute_and_send_correction(
        self, device_id: str, state: DeviceState, reward: float
    ):
        """Compute period correction using RL policy and send to device."""
        phase_error = state.phase_error_ns
        K = self.K[device_id]

        # Correction in ticks (convert from ns)
        # At 40MHz, 1 tick = 25ns
        correction_ticks = -K * phase_error / Config.NS_PER_TICK

        # Convert to FP16 format (for DTR)
        correction_fp16 = int(correction_ticks * 65536)

        # Clamp to reasonable range
        correction_fp16 = max(-1_000_000, min(1_000_000, correction_fp16))

        # Update gain using reward gradient (simple online learning)
        if state.last_reward is not None:
            reward_delta = reward - state.last_reward
            # Gradient update: increase K if reward improved with positive error
            self.K[device_id] += Config.LEARNING_RATE * reward_delta * phase_error * 1e-6
            self.K[device_id] = max(
                Config.MIN_GAIN, min(Config.MAX_GAIN, self.K[device_id])
            )
        state.last_reward = reward

        # Publish correction to device
        control_msg = {
            "ts": time.time(),
            "period_correction_fp16": correction_fp16,
            "action": "adjust_period",
            "phase_error_ns": phase_error,
            "gain_K": K,
        }
        self.mqtt.publish(
            f"fts/{device_id}/control",
            json.dumps(control_msg),
            qos=1,
        )

        # Log to InfluxDB
        point = (
            Point("rl_action")
            .tag("device", device_id)
            .field("correction_fp16", correction_fp16)
            .field("phase_error_ns", phase_error)
            .field("reward", reward)
            .field("gain_K", K)
            .field("cumulative_reward", state.cumulative_reward)
            .time(int(time.time() * 1e9))
        )
        self._write_influx(point)

        logger.info(
            f"Sent correction to {device_id}: fp16={correction_fp16}, K={K:.3f}"
        )

    def _write_influx(self, point: Point):
        """Write point to InfluxDB."""
        try:
            self.influx_write.write(bucket=Config.INFLUX_BUCKET, record=point)
        except Exception as e:
            logger.warning(f"Failed to write to InfluxDB: {e}")

    async def run(self):
        """Main event loop."""
        logger.info("Starting RL Engine...")

        # Start MQTT loop in background
        self.mqtt.loop_start()

        try:
            while True:
                await asyncio.sleep(10)
                # Periodic status logging
                n_devices = len(self.states)
                n_edges = len(self.edge_buffer)
                logger.info(
                    f"Status: {n_devices} devices, {n_edges} edges in buffer"
                )
                for device_id, state in self.states.items():
                    logger.info(
                        f"  {device_id}: K={self.K[device_id]:.3f}, "
                        f"cumulative_reward={state.cumulative_reward:.0f}"
                    )
        except asyncio.CancelledError:
            logger.info("Shutting down...")
        finally:
            self.mqtt.loop_stop()
            self.influx.close()


def main():
    """Entry point."""
    engine = RLEngine()
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
