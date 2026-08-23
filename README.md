# Cross-Asset Anomaly Detection System

Real-time anomaly detection across 7 asset classes — equities (S&P 500,
Nasdaq), crypto (BTC), commodities (gold, crude oil), FX (EUR/USD), and
rates (10Y Treasury yield) — with a live Streamlit dashboard.

## Architecture

```
                      ┌─────────────────────────────────────────────────┐
                      │                    Kafka                        │
  Finnhub / Yahoo ──▶ │              topic: market-data                │
  (producer)          └───────────┬─────────────────────┬───────────────┘
                                  │                     │
                      ┌───────────▼──────────┐ ┌────────▼─────────────┐
                      │  feature-consumer    │ │  anomaly-consumer    │
                      │  z-score, EWMA vol,  │ │  tick-level pipeline │
                      │  PCA residual, corr  │ │  (z-score, iForest,  │
                      │  snapshots → DB      │ │  corr-break) → DB    │
                      └───────────┬──────────┘ └────────┬─────────────┘
                                  │                     │
                      ┌───────────▼─────────────────────▼─────────────┐
                      │           PostgreSQL (market_anomalies)       │
                      │  market_features · anomaly_events ·           │
                      │  correlation_snapshots                        │
                      └──────┬───────────────────────────▲────────────┘
                             │                           │
                  ┌──────────▼─────────┐      ┌──────────┴──────────┐
                  │  scheduler         │      │  FastAPI (port 8000)│
                  │  every 5 min:      │      │  /assets /anomalies │
                  │  batch detection   │      │  /correlations      │
                  │  daily: cleanup +  │      │  /chart/{symbol}    │
                  │  retention purge   │      └──────────┬──────────┘
                  └────────────────────┘                 │
                                             ┌──────────▼──────────┐
                                             │ Streamlit dashboard │
                                             │   (port 8501)       │
                                             └─────────────────────┘
```

**Data sources.** The producer polls Yahoo Finance every 30 seconds for
all 7 symbols: live prices come from the light `fast_info` endpoint,
while per-ticker 1-minute bars supply volume and exact 1m/5m returns.
Symbols whose price hasn't changed are deduped each cycle. Freshness is
bounded by the source feed: crypto (BTC) and FX are near real-time;
US equities, indices, futures, and yields are delayed ~10–20 minutes at
Yahoo's source regardless of poll speed. Finnhub remains an optional
upgrade path — set `FINNHUB_API_KEY` to route mapped symbols (e.g.
crypto) through its real-time quotes, with automatic per-symbol fallback.

**Detection paths.** Two complementary detectors write `anomaly_events`:

- *Real-time* (`anomaly-consumer`): tick-level price detectors as each
  Kafka message arrives — z-score spike, isolation forest, cross-asset
  correlation break.
- *Batch* (`scheduler`, every 5 min): feature-based scoring against the
  last 100 feature rows per symbol — weighted composite of z-score,
  EWMA volatility surge, and PCA residual, with generated descriptions.

## Quickstart

```bash
# 1. Configure (optional: add a free Finnhub key for real-time crypto/FX)
cp .env.example .env

# 2. Start everything
docker compose up -d --build

# 3. (First run only) create tables and seed 7 days of demo data
DATABASE_URL=postgresql://postgres:password@localhost:5432/market_anomalies python scripts/init_db.py
DATABASE_URL=postgresql://postgres:password@localhost:5432/market_anomalies python scripts/seed_demo_data.py
```

- **Dashboard**: http://localhost:8501 — live asset status with per-symbol
  data freshness (live / delayed / stale), correlation heatmap, and an
  anomaly timeline with selectable time windows (1H–7D/ALL). The seeded
  demo-data region is shaded automatically so it's distinguishable from
  live pipeline data.
- **API docs**: http://localhost:8000/docs

## Services

| Service           | What it does                                  | Schedule/Trigger      |
|-------------------|-----------------------------------------------|-----------------------|
| `producer`        | Fetch quotes → Kafka `market-data`            | every 60s             |
| `feature-consumer`| Features (z/EWMA/PCA) + correlation snapshots | per message           |
| `anomaly-consumer`| Tick-level anomaly detection                  | per message           |
| `scheduler`       | Batch detection engine + retention cleanup    | 5 min / daily 00:00 UTC |
| `fastapi`         | REST API over the database                    | on request            |
| `streamlit`       | Dashboard                                     | on request            |
| `kafka`/`zookeeper`/`postgres` | Infrastructure                   | —                     |

Retention: `market_features` 30 days, `correlation_snapshots` 7 days.

## Configuration (`.env`)

| Variable                 | Purpose                                              |
|--------------------------|------------------------------------------------------|
| `DATABASE_URL`           | Postgres connection (host-run scripts use `localhost`) |
| `KAFKA_BOOTSTRAP_SERVERS`| Kafka address (host-run scripts use `localhost:9092`) |
| `FINNHUB_API_KEY`        | Finnhub key; empty = Yahoo Finance only              |
| `FINNHUB_SYMBOL_MAP`     | Optional JSON to route more symbols through Finnhub  |

## Airflow

`dags/` contains equivalent Airflow 2.x DAGs (`cross_asset_anomaly_detection`,
`market_data_cleanup`) for deployment to a real Airflow environment. On a
single machine, the `scheduler` service runs the same logic with no extra
infrastructure. When deploying to Airflow, create the `market_anomalies_db`
Postgres connection.

## Development

```bash
# Run components on the host instead of containers
python -m src.producers.market_data_producer
python -m src.consumers.feature_consumer
python -m src.consumers.anomaly_consumer
python -m src.detection.scheduler            # 5-min detection + daily cleanup
python -m src.detection.anomaly_engine       # one-shot detection cycle

# Tests / sanity checks
python -c "from src.detection.pipeline import DetectionPipeline; print('ok')"
```
