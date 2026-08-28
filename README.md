# Cross-Asset Anomaly Detection System

Real-time anomaly detection across 7 asset classes — equities (S&P 500,
Nasdaq), crypto (BTC), commodities (gold, crude oil), FX (EUR/USD), and
rates (10Y Treasury yield) — with a live Streamlit dashboard.

The system ships in two deployment profiles built on the same detection
core:

| Profile | What it is | For whom |
|---------|-----------|----------|
| **Slim** (`python -m src.slim`) | One Python process: fetch → features → detection → API, plus Postgres and Streamlit beside it. No Docker, no broker. | Hosting on any machine or tiny VM; simplest way to run |
| **Full streaming** (`docker compose up`) | 8-container event-driven topology: producer → Kafka → consumers → DB, plus scheduler/API/dashboard | Development, scaling out, or anyone who wants the full pipeline |

Both share one implementation of every detector — the slim profile is
composition glue over the same modules, never a fork.

## Architecture

### Slim profile (single process)

```
  Finnhub / Yahoo ──▶ ┌────────────────────────────────────────────┐
  (30s fetch loop)    │ src/slim.py (one process)                  │
                      │  ingest thread: quotes → features → DB     │
                      │                └──▶ 4-detector pipeline    │
                      │              ─────▶ alerts → DB            │
                      │  batch thread: composite scoring / 5 min   │
                      │  daily threads: retention cleanup, backup  │
                      │  FastAPI :8000                             │
                      └──────────────┬─────────────────┬───────────┘
                                     │                 │
                      ┌──────────────▼───┐   ┌─────────▼───────────┐
                      │ PostgreSQL       │   │ Streamlit dashboard │
                      │ market_anomalies │   │ :8501               │
                      └──────────────────┘   └─────────────────────┘

  Detector state warm-starts from market_features at boot, so a restart
  is fully detected-on-tick-one — no cold ramp.
```

Working set ≈ 0.9 GB including OS — runs comfortably on any small box,
laptop, or 1 GB free-tier VM.

### Full streaming profile (Docker)

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

## Quickstart (slim profile, no Docker)

Prerequisites: Python 3.11+, a PostgreSQL server reachable at
`DATABASE_URL` (any local or remote install), and `pip install -r requirements.txt`
(the production-pinned file; `requirements-dev.txt` is the loose set
used by CI for tests).

```bash
# 1. Configure — DATABASE_URL is the only required value
cp .env.example .env            # then edit DATABASE_URL (+ optional Finnhub key)

# 2. First run only: create tables and seed demo data
python scripts/init_db.py

# 3. Start the app: fetch + detect + API in one process
python -m src.slim

# 4. Dashboard in a second terminal
streamlit run dashboard/app.py
```

- **Dashboard**: http://localhost:8501
- **API docs**: http://localhost:8000/docs

## Quickstart (full streaming stack, Docker)

```bash
# 1. Configure (optional: add a free Finnhub key for real-time crypto/FX)
cp .env.example .env

# 2. Start everything
docker compose up -d --build

# 3. (First run only) create tables and seed 7 days of demo data
DATABASE_URL=postgresql://postgres:password@localhost:5432/market_anomalies python scripts/init_db.py
DATABASE_URL=postgresql://postgres:password@localhost:5432/market_anomalies python scripts/seed_demo_data.py
```

Same dashboard/API URLs as above.

## Slim profile details

Environment knobs (all optional beyond `DATABASE_URL`):

| Variable              | Default | Purpose                                  |
|-----------------------|---------|------------------------------------------|
| `SLIM_FETCH_INTERVAL` | 30      | Seconds between fetch/detect cycles      |
| `SLIM_BATCH_INTERVAL` | 300     | Seconds between batch-scoring cycles     |
| `SLIM_SYMBOLS`        | all 7   | Space-separated subset to track          |
| `API_PORT`            | 8000    | FastAPI port                             |
| `SLIM_BACKUPS`        | 0       | `1` enables nightly `pg_dump` (needs `pg_dump` on PATH) |

What each cycle does per tick: append to rolling windows → write
z-score/EWMA/PCA features → run the four-detector pipeline → persist
alerts through the same cooldown rule as the full stack. Every five
minutes the batch composite engine runs; retention cleanup runs nightly.
Correlation snapshots are written every 60 messages, matching the
Kafka profile.

**Hosting it publicly without paying anyone:** run slim + Streamlit on
any machine you keep powered on and expose it with a Cloudflare Tunnel
(`cloudflared tunnel --url http://localhost:8501`) — free, no card,
no port forwarding. Liveness equals that machine's uptime; disable
sleep if you want it truly always-on. The same containerless build also
runs unchanged on any tiny Linux VM if you ever choose to rent one.

## Demo dataset (kept separate from live data)

Synthetic demo data lives in its own database (`market_anomalies_demo`)
and is never blended with live pipeline data. Toggle **LIVE / DEMO** in
the dashboard header; demo mode shows a synthetic-data banner on every
view. All API endpoints accept `?dataset=live|demo` (default `live`).

The `scheduler` service keeps the demo dataset looking current
automatically in the Docker profile: it reseeds (wipe + regenerate with
fresh timestamps) every `DEMO_REFRESH_SECONDS` (default 6 hours),
alongside its detection and cleanup cycles. One-time setup:

```bash
docker exec postgres createdb -U postgres market_anomalies_demo   # first time only
```

Manual immediate refresh: `docker exec demo-refresher sh -c "DROP_EXISTING=1 python scripts/seed_demo_data.py"`

(The slim profile serves live data only and does not run a demo DB.)

## Services

Docker profile:

| Service           | What it does                                  | Schedule/Trigger      |
|-------------------|-----------------------------------------------|-----------------------|
| `producer`        | Fetch quotes → Kafka `market-data`            | every 30s             |
| `feature-consumer`| Features (z/EWMA/PCA) + correlation snapshots | per message           |
| `anomaly-consumer`| Tick-level anomaly detection                  | per message           |
| `scheduler`       | Batch detection engine + retention cleanup    | 5 min / daily 00:00 UTC |
| `fastapi`         | REST API over the database                    | on request            |
| `streamlit`       | Dashboard                                     | on request            |
| `kafka`/`postgres`| Infrastructure (KRaft mode, no Zookeeper)     | —                     |

Slim profile processes:

| Process                    | What it does                                        | Schedule/Trigger        |
|----------------------------|-----------------------------------------------------|-------------------------|
| `python -m src.slim`       | Fetch → features → detection → API, batch engine    | 30s ticks / 5-min batch / daily cleanup |
| `streamlit run dashboard/app.py` | Dashboard                                     | on request              |

Retention: `market_features` 30 days, `correlation_snapshots` 7 days.

## Backups

**Slim profile:** set `SLIM_BACKUPS=1` (requires `pg_dump` on PATH) —
nightly dumps at 01:00 UTC plus once at startup, keeping the newest 7,
written to `./backups/`. Restore with:

```bash
pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" backups/<file>.dump
```

**Docker profile:** the scheduler dumps the live database daily at
01:00 UTC (plus once at startup) to the `anomaly-backups` volume in
pg_dump custom format — compressed, retention keeps the newest 7.
Restore with:

```bash
docker exec scheduler pg_restore --clean --if-exists --no-owner \
  -d "$DATABASE_URL" /backups/<file>.dump
```

The demo database is regenerable from the seeder and is not backed up.

## Live deployment (GitHub Actions + Pages) — zero infrastructure

The hosted instance runs entirely on GitHub: a scheduled workflow
executes the detection pipeline on GitHub's runners and publishes the
dashboard + data snapshots to the `live-data` branch, served by GitHub
Pages. No server, no database, no card, and no dependence on any
personal machine being on.

- **Live URL**: `https://likhith776.github.io/cross-asset-anomaly-monitor/`
- **Cadence**: every 30 minutes (`LIVE` workflow; also runs on every push)
- **Data contract**: [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md) —
  the JSON schemas the dashboard (and the upcoming UI revamp) consume

One-time enablement: Settings → Pages → Source *Deploy from a branch* →
`live-data` / `(root)`.

Detection state persists between runs inside the published tree
(`state/`), so each run warm-starts from the previous one — identical
behavior to the DB-backed profiles.

## Deployment (hosted VPS, alternative)

**Slim profile, self-hosted.** The slim profile needs only
Python, Postgres, and ~1 GB of RAM — no containers, no cloud account,
no card anywhere:

```bash
git clone <repo> && cd cross-asset-anomaly-detection
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt                    # production-pinned
cp .env.example .env                                # set DATABASE_URL (+ strong password)
python scripts/init_db.py                           # first run only
python -m src.slim                                  # terminal 1
streamlit run dashboard/app.py                      # terminal 2 (or a service)
```

To share it publicly from your own machine, front the dashboard with a
Cloudflare Tunnel (`cloudflared tunnel --url http://localhost:8501`) —
free, no signup card, no port forwarding, HTTPS included. Liveness then
equals that machine's uptime, so disable sleep while showcasing.

For unattended hosting on rented hardware (a $4–6/mo VPS or any 1 GB
free-tier VM), run the exact same two commands under systemd/supervisor;
nothing in the slim profile assumes Docker or a specific OS. The Docker
profile remains fully supported on any Docker host for those who prefer
it: `POSTGRES_PASSWORD` comes from `.env`, and `docker compose up -d
--build` is the whole deployment. Set `POSTGRES_PASSWORD` before the
first start (it only applies when the data volume is initialized).
Passwords with special characters must be URL-encoded in connection
strings.

## Configuration (`.env`)

| Variable                 | Purpose                                              |
|--------------------------|------------------------------------------------------|
| `DATABASE_URL`           | Postgres connection — required by both profiles      |
| `KAFKA_BOOTSTRAP_SERVERS`| Kafka address (Docker profile / host-run consumers)  |
| `FINNHUB_API_KEY`        | Finnhub key; empty = Yahoo Finance only              |
| `FINNHUB_SYMBOL_MAP`     | Optional JSON to route more symbols through Finnhub  |
| `SLIM_*`, `API_PORT`, `BACKUP_DIR` | Slim-profile knobs (see table above)       |

## Airflow

`dags/` contains equivalent Airflow 2.x DAGs (`cross_asset_anomaly_detection`,
`market_data_cleanup`) for deployment to a real Airflow environment. On a
single machine, the scheduler (Docker profile) or the slim process's batch
thread runs the same logic with no extra infrastructure. When deploying to
Airflow, create the `market_anomalies_db` Postgres connection.

## Development

```bash
# Full streaming stack locally (mirrors the Kafka topology)
docker compose up -d --build

# Or individual host-run components
python -m src.producers.market_data_producer
python -m src.consumers.feature_consumer
python -m src.consumers.anomaly_consumer
python -m src.detection.scheduler            # 5-min detection + daily cleanup
python -m src.detection.anomaly_engine       # one-shot detection cycle

# Tests / sanity checks
pytest                                       # full suite incl. slim orchestration
```
