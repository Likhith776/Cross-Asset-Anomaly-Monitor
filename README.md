<div align="center">

# Cross-Asset Anomaly Monitor

**Unattended anomaly detection across seven markets — fetched, detected and published by scheduled CI.**

No server. No database to keep alive. No paid tier. No uptime to babysit.

[![LIVE](https://img.shields.io/badge/live-dashboard-000000?style=flat-square)](https://likhith776.github.io/Cross-Asset-Anomaly-Monitor/dashboard.html)
[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-000000?style=flat-square)](https://github.com/Likhith776/Cross-Asset-Anomaly-Monitor/actions)
[![Python](https://img.shields.io/badge/python-3.11-000000?style=flat-square)](https://www.python.org/)
[![Hosting](https://img.shields.io/badge/hosting-GitHub%20Pages-000000?style=flat-square)](https://pages.github.com/)

</div>

---

## Live

| | URL |
|---|---|
| **Landing page** | https://likhith776.github.io/Cross-Asset-Anomaly-Monitor/ |
| **Live dashboard** | https://likhith776.github.io/Cross-Asset-Anomaly-Monitor/dashboard.html |
| **Incident log** | https://likhith776.github.io/Cross-Asset-Anomaly-Monitor/dashboard.html#incidents |
| **Raw snapshot** | https://likhith776.github.io/Cross-Asset-Anomaly-Monitor/data/latest.json |
| **Source** | https://github.com/Likhith776/Cross-Asset-Anomaly-Monitor |

Everything above is served from a single static branch. The publish job runs
**every 30 minutes** and on every push to `main`; there is no manual deploy step.

> The landing page no longer hosts the dashboard. It was split on 2026-09-16:
> `/` is now the project front door and `/dashboard.html` is the live console.

### Screenshots

| Landing page (1440) | Dashboard (1440) |
|---|---|
| ![Landing page](docs/screenshots/landing-desktop.png) | ![Dashboard](docs/screenshots/dashboard-desktop.png) |

| Landing page (390) | Dashboard (390) |
|---|---|
| ![Landing page mobile](docs/screenshots/landing-mobile.png) | ![Dashboard mobile](docs/screenshots/dashboard-mobile.png) |

---

## What it does

Seven instruments, four independent detectors, one published record — from a
scheduled GitHub Actions job that needs nothing but a free account.

- **Composite market coverage** — S&P 500, Nasdaq Composite, Bitcoin, Gold
  futures, Crude oil futures, EUR/USD and the 10-year Treasury yield, from a
  single universe file.
- **Four detectors** vote on the same windows and are cross-checked against each
  other: univariate z-score spikes, isolation-forest outliers, cross-asset
  correlation breaks, and a multivariate Mahalanobis joint detector.
- **Regime-aware thresholds** — a volatility-regime classifier scales detector
  sensitivity so quiet and turbulent markets are not judged by the same ruler.
- **Context on every alert** — volatility regime, lead-lag attribution (which
  asset moved first), scheduled macro releases, and optional LLM explanations.
- **Human feedback loop** — alerts can be labelled confirmed / false positive,
  and the rolling 30-day precision per detector is published with the data.
- **Detector quality report** — a committed, regenerable precision/recall/F1
  evaluation against planted anomalies ([`docs/backtest_report.md`](docs/backtest_report.md)).
- **Static-by-default web presence** — a landing page and a data console, both
  plain HTML/CSS/JS, both free to host forever.

## Architecture

Two deployment profiles share one implementation of every detector. The slim
profile is composition glue over the same modules, never a fork.

### Slim profile — one process, no containers

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
```

Detector state warm-starts from `market_features` at boot, so a restart is
detected-on-tick-one — no cold ramp. Working set ≈ 0.9 GB including the OS.

### Full streaming profile — Docker, 8 containers

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

### The hosted profile — the one that is live

```
  GitHub Actions (every 30 min, free)
        │
        ├─ fetch quotes  →  features  →  four detectors  →  state/
        ├─ write data/*.json  +  the static pages
        │
        └─ force-push one commit to the `live-data` branch
                    │
                    ▼
          GitHub Pages serves the branch
          (index.html · dashboard.html · incidents.html · assets/ · data/)
```

The publisher rewrites `live-data` as a **single amended commit** every run, so
the branch never grows and the site is always exactly the last snapshot. Live
state (`state/`) travels inside that branch, so each run warm-starts from the
previous one — identical behaviour to the database-backed profiles.

## Repository layout

```
cross-asset-anomaly-monitor-v6.html   landing page (published as /index.html)
site/
  dashboard.html                      the live console (published as /dashboard.html)
  incidents.html                      redirect shim → dashboard.html#incidents
  assets/css/codapress.css            the design system (tokens + components)
  assets/css/dashboard.css            dashboard-only layout on top of it
  assets/js/lenis-lite.js             RAF smooth scroll
  assets/js/reveal.js                 IntersectionObserver reveals
  assets/js/cursor.js                 two-shape blend-mode cursor
  assets/js/nav.js                    header, mobile menu, active-section underline
  assets/js/carousel.js               arc carousel (drag + keyboard)
  assets/js/dither-hero.js            WebGL2 dithered plasma hero
  assets/js/landing.js                landing page data binding
  assets/js/dashboard.js              dashboard logic and states
src/
  detection/                          z-score, isolation forest, correlation break,
                                      joint Mahalanobis, regime, lead-lag, macro
                                      calendar, LLM explain, pipeline, scheduler
  producers/                          composite quote feed (keyless primaries)
  consumers/                          streaming feature + anomaly consumers
  api/                                FastAPI service
  slim.py                             single-process profile
  livesite.py                         the static publisher (data + pages)
  precision.py                        rolling precision from human labels
  backtest/                           replay harness and planted-event scoring
config/universe.json                  the single source of truth: symbols + pairs
scripts/                              init_db, seed_demo_data, publish_live_site,
                                      run_backtest_report, patch-publish-layout
docs/                                 data contract, backtest report, design docs
.github/workflows/                    ci.yml · live.yml · backtest-report.yml
```

## Data sources and methodology

**Quote feed.** A composite provider polls keyless sources on a 30-second cadence
in the slim profile and on each scheduled run in the hosted profile, preferring
the primary source per instrument class and falling back automatically. US
equities, indices, futures and yields are delayed ~10–20 minutes at the source
regardless of poll speed; crypto and FX are near real-time. Symbol prices that
have not changed are de-duplicated each cycle. An optional Finnhub key upgrades
the routed symbols to real-time quotes; nothing requires it.

**Features.** Per symbol, on every tick and on restart: rolling z-score, EWMA
volatility, PCA residual against the panel, and pairwise correlation snapshots
written every 60 messages in the streaming profile (and on the same cadence in
the slim profile).

**Detection.** Two complementary paths write `anomaly_events`: a real-time
tick-level pass (z-score, isolation forest, correlation break) and a batch
composite pass every five minutes that scores against the last 100 feature rows
per symbol. A volatility-regime classifier rescales thresholds; a joint
Mahalanobis detector catches multi-symbol co-movement that no univariate test
sees. Every alert carries severity, score, regime, optional lead-lag attribution,
optional macro-release context, and an optional LLM explanation.

**Precision.** Alerts can be labelled by a human. The rolling 30-day precision
overall and per detector is computed by `src/precision.py` and published next to
the data, so the site reports how well the detectors are actually doing rather
than only what they found.

**Full schema:** [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md).

## The web presence

Both pages are static files — no framework, no bundler, no build step.

### Landing page — `/`

The project front door. A dithered greyscale plasma field carries the headline
(rendered inside the WebGL canvas, with the real `<h1>` retained for assistive
technology and as the no-WebGL fallback), followed by the lede, a live
**Tracked market** arc carousel of every instrument with its real sparkline,
price, 1-month return and z-score, the **Latest signal** feature block, a
**Method** grid explaining the four pipeline stages, the **Recent incidents**
record grid, and the footer.

### Dashboard — `/dashboard.html`

The console: live asset-status cards, a symbol selector with price history and
marked anomalies, the correlation heatmap and summary, a filtered anomaly
timeline, a sortable and filterable alerts table with drill-down detail,
rolling detector precision, the incident log, and links to every raw JSON
endpoint. Each panel resolves to an explicit **ready / empty / error** state, and
a stale-data warning appears if the freshest snapshot is older than 15 minutes.
It reloads itself every 5 minutes, matching the publisher cadence.

### Design system

The visual language is a measured reproduction of
[**codapress.co.uk**](https://codapress.co.uk/) — one monospace family
(JetBrains Mono 400/700), a black canvas, a fluid `clamp(min, x·lvw, max)` scale,
spacing and height tokens keyed to a stable 900px viewport, sharp corners with
hairline borders and no shadows, a single accent reserved for the cursor, and a
digitally-dithered hero.

| Document | What it covers |
|---|---|
| [`docs/design/CODAPRESS-DESIGN-SPEC.md`](docs/design/CODAPRESS-DESIGN-SPEC.md) | Every measured token, component rule and behaviour |
| [`docs/design/CODAPRESS-DESIGN-PARITY.md`](docs/design/CODAPRESS-DESIGN-PARITY.md) | Side-by-side parity checklist, responsive/accessibility/state evidence, deliberate deviations |

No reference copy, imagery, logo or source code is reused.

## Local setup

### Slim profile (no Docker)

Prerequisites: Python 3.11+, a PostgreSQL server reachable at `DATABASE_URL`,
and the production pins.

```bash
git clone https://github.com/Likhith776/Cross-Asset-Anomaly-Monitor.git
cd Cross-Asset-Anomaly-Monitor

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
cp .env.example .env            # then set DATABASE_URL

python scripts/init_db.py       # first run only: create tables
python -m src.slim              # terminal 1: fetch + detect + API
streamlit run dashboard/app.py  # terminal 2: dashboard
```

- Dashboard: http://localhost:8501
- API docs: http://localhost:8000/docs

### Full streaming stack (Docker)

```bash
cp .env.example .env
docker compose up -d --build
DATABASE_URL=postgresql://postgres:***@localhost:5432/market_anomalies python scripts/init_db.py
DATABASE_URL=postgresql://postgres:***@localhost:5432/market_anomalies python scripts/seed_demo_data.py
```

Grafana (http://localhost:3000, `admin` / `admin`) and Prometheus
(http://localhost:9090) come up with it; remove those two services and
everything else runs identically.

### Previewing the static site locally

The hosted pages are plain files, so any static server works:

```bash
python -m http.server 8899 --directory .
# then open http://127.0.0.1:8899/cross-asset-anomaly-monitor-v6.html
# and  http://127.0.0.1:8899/site/dashboard.html
```

Without `data/*.json` present both pages render their explicit empty states —
useful for checking the fallback path.

### Tests

```bash
pytest                    # full suite, including slim orchestration
python scripts/run_backtest_report.py   # regenerate the detector quality report
```

## Deployment and free hosting

**This project runs on GitHub Pages and nothing else.** It is free forever, needs
no account beyond GitHub, no billing method, and no server. The `.github/workflows/live.yml`
job runs the whole pipeline on GitHub's runners and force-pushes the result to
the `live-data` branch, which Pages serves.

One-time enablement (already done for this repository):

1. **Settings → Pages → Source: Deploy from a branch**
2. Branch **`live-data`**, folder **`/ (root)`** → Save
3. Run the **LIVE** workflow once (Actions → LIVE → Run workflow)

After that, every push to `main` and every 30-minute schedule republishes
automatically. Nothing needs to be running on your machine.

**Why not Vercel/Netlify?** Their free tiers are fine, but they add a second
account and a second place for the deployment to break for no functional gain:
the pipeline already publishes static files to a branch, and Pages serves a
branch natively with zero configuration and zero cost. If you later want preview
deployments per pull request, both providers can be pointed at the same
repository as an additive step.

**Fully self-hosted alternative.** The slim profile needs only Python, Postgres
and ~1 GB of RAM. To share it from your own machine, front Streamlit with a
Cloudflare Tunnel (`cloudflared tunnel --url http://localhost:8501`) — free, no
card, no port forwarding. Liveness then equals that machine's uptime.

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection — required by both server profiles |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka address (Docker profile / host-run consumers) |
| `FINNHUB_API_KEY` | Optional real-time upgrade; empty = keyless sources only |
| `FINNHUB_SYMBOL_MAP` | Optional JSON to route more symbols through Finnhub |
| `GEMINI_API_KEY` | Optional LLM explanations; empty = feature stays off |
| `SLIM_FETCH_INTERVAL` | Seconds between fetch/detect cycles (default 30) |
| `SLIM_BATCH_INTERVAL` | Seconds between batch-scoring cycles (default 300) |
| `SLIM_SYMBOLS` | Space-separated subset to track (default: all seven) |
| `SLIM_BACKUPS` | `1` enables nightly `pg_dump` |
| `API_PORT` | FastAPI port (default 8000) |
| `BACKUP_DIR` | Backup destination (default `./backups`) |

The tracked universe — symbols, labels and correlation pairs — lives in
[`config/universe.json`](config/universe.json) and is loaded once at startup by
every entry point. Adding or removing an instrument is an edit to that file
alone; the loader fails fast on pairs that reference untracked symbols.

The LLM explanation feature is off by default, pinned to a free-tier model, and
budgeted by a process-wide sliding window (6/hour, 30/day) enforced before any
network call. Any failure stores `null` and never blocks the anomaly record.

## Detector quality

[`docs/backtest_report.md`](docs/backtest_report.md) is a committed, regenerable
evaluation of every detector — precision, recall and F1 per detector against
planted anomalies (single-symbol spikes, dips and volatility bursts at varying
magnitudes, plus joint-flip scenarios only the multivariate detector can catch),
a false-positive rate from clean replays, and a recall-by-magnitude chart. It is
deterministic (fixed seeds, no network), so it changes exactly when detector code
changes. Regenerate it with `python scripts/run_backtest_report.py`, or dispatch
the **BACKTEST-REPORT** workflow.

## Roadmap

- [x] Composite keyless quote feed with automatic fallback
- [x] Four detectors, regime-scaled thresholds, joint multivariate detector
- [x] Lead-lag attribution and macro-release context on alerts
- [x] Human labels and a published rolling precision metric
- [x] Zero-infrastructure publishing from scheduled CI
- [x] Design-system revamp of the landing page and dashboard
- [ ] Self-hosted JetBrains Mono instead of the Google Fonts CDN
- [ ] Per-pull-request preview deployments
- [ ] Alert subscriptions (email / webhook) as an opt-in
- [ ] Additional instruments via `config/universe.json` only

## Licence

No `LICENSE` file is present in this repository yet, so all rights are reserved
by the author by default. If you intend to let others reuse this work, add a
`LICENSE` file — MIT is the conventional choice for a project like this:

```text
MIT License · Copyright (c) 2026 Likhith
```

Until that file exists, treat the code as source-available, not open source.

## Credits

- Detection, pipeline, API and dashboard: built by **Likhith**
  ([@Likhith776](https://github.com/Likhith776)).
- Visual language: measured from [codapress.co.uk](https://codapress.co.uk/) —
  structure, tokens and behaviour only; no copy, imagery or code reused. The
  parity evidence is documented in [`docs/design/`](docs/design/CODAPRESS-DESIGN-PARITY.md).
- Typeface: [JetBrains Mono](https://www.jetbrains.com/lp/mono/) (SIL Open Font
  License).
- Charts: [Apache ECharts](https://echarts.apache.org/) 5.5 via jsDelivr.
- Detector evaluation methodology: replay harness with planted events
  (`src/backtest/`), in the spirit of the standard precision/recall-first
  approach to anomaly-detection evaluation.
