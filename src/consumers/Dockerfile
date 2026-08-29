FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY src/consumers/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/consumers/ ./src/consumers/
COPY src/api/ ./src/api/
COPY src/metrics.py ./src/metrics.py
COPY src/universe.py ./src/universe.py
COPY config/ ./config/
COPY src/detection/ ./src/detection/

# Default to the feature consumer; the anomaly consumer overrides
# the command in docker-compose.
CMD ["python", "-m", "src.consumers.feature_consumer"]
