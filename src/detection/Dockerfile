FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev postgresql-client && \
    rm -rf /var/lib/apt/lists/*

COPY src/detection/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/detection/ ./src/detection/
COPY src/metrics.py ./src/metrics.py
# Seeding scripts for the scheduled demo-dataset refresh cycle
COPY scripts/ ./scripts/

CMD ["python", "-m", "src.detection.scheduler"]
