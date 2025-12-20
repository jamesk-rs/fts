FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install system packages including UHD
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    uhd-host \
    python3-uhd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create venv with access to system site-packages (for uhd)
RUN python3 -m venv --system-site-packages /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Set PYTHONPATH so imports work correctly
ENV PYTHONPATH="/app/src"

ENTRYPOINT ["python3", "-u", "src/cli.py"]
CMD ["stream-mqtt", "--mqtt-host", "mosquitto"]
