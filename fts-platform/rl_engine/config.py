"""
Configuration for FTS RL Engine.
"""

import os


class Config:
    """Configuration loaded from environment variables."""

    # MQTT settings
    MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
    MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

    # InfluxDB settings
    INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
    INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "fts-token-change-me")
    INFLUX_ORG = os.getenv("INFLUX_ORG", "fts")
    INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "fts")

    # Redis settings
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

    # RL parameters
    LEARNING_RATE = float(os.getenv("RL_LEARNING_RATE", "0.01"))
    DISCOUNT_FACTOR = float(os.getenv("RL_DISCOUNT_FACTOR", "0.99"))
    INITIAL_GAIN = float(os.getenv("RL_INITIAL_GAIN", "1.0"))
    MIN_GAIN = float(os.getenv("RL_MIN_GAIN", "0.1"))
    MAX_GAIN = float(os.getenv("RL_MAX_GAIN", "10.0"))

    # Correlation settings
    CORRELATION_WINDOW_MS = float(os.getenv("CORRELATION_WINDOW_MS", "100"))

    # Timer settings (40MHz resolution)
    TIMER_RESOLUTION_HZ = 40_000_000
    NS_PER_TICK = 1e9 / TIMER_RESOLUTION_HZ  # 25ns

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
