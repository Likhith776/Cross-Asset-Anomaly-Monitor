FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY src/producers/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/producers/ ./src/producers/
COPY src/metrics.py ./src/metrics.py

CMD ["python", "-m", "src.producers.market_data_producer"]
