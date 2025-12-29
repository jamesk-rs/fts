FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY phase_noise_hourly.py .

CMD ["python", "-u", "phase_noise_hourly.py"]
