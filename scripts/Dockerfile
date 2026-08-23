FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY scripts/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/

# Loop: ensure schema exists, then reseed (wipe + regenerate with fresh
# timestamps) every DEMO_REFRESH_SECONDS. The compose command runs the loop.
CMD ["python", "scripts/init_db.py"]
