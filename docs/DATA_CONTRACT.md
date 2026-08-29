# Live Site Data Contract

The hosted deployment runs entirely on GitHub: a scheduled workflow
(`.github/workflows/live.yml`) executes the detection pipeline and
publishes JSON snapshots to the `live-data` branch, which GitHub Pages
serves at:

```
https://likhith776.github.io/cross-asset-anomaly-monitor/
```

Any front-end — including the future UI/UX revamp — consumes only the
files below. Treat them as the system's public API.

Refresh cadence: every 30 minutes (GitHub Actions cron; occasional
runner-side jitter of a few minutes is normal).

---

## `data/latest.json`

Latest known state per symbol.

```json
{
  "schema": 1,
  "generated_at": "2026-08-26T10:04:22+00:00",
  "symbols": {
    "^GSPC": {
      "price": 5643.25,
      "return_1m": null,
      "return_5m": null,
      "volume": 1234567,
      "source": "yfinance",
      "timestamp": "2026-08-26T09:58:00+00:00",
      "freshness": "live",          // live | delayed | stale
      "age_seconds": 372
    }
  }
}
```

`freshness`: `live` ≤ 2 min since source timestamp, `delayed` ≤ 15 min,
otherwise `stale`. Equity/index/futures/yield quotes are naturally
delayed ~10–20 min by Yahoo regardless of poll speed.

## `data/anomalies.json`

Newest-first anomaly log (capped at 200 published / 500 retained).

```json
{
  "schema": 1,
  "generated_at": "...",
  "events": [
    {
      "timestamp": "2026-08-26T09:59:12+00:00",
      "symbol": "BTC-USD",
      "score": 0.87,
      "severity": "high",
      "description": "Tick-level zscore_spike (zscore_price detector), z=4.21 detected on BTC-USD at 71250.0 — high severity"
    }
  ]
}
```

`severity` is extracted from the description tail and promoted to a
top-level field. The dashboard's Anomaly Timeline and Recent Alerts
sections both filter on it.

## `data/correlations.json`

Pairwise Pearson correlation matrix from the most recent snapshot,
plus a tail of recent snapshots for drift visualization.

```json
{
  "schema": 1,
  "generated_at": "...",
  "symbols": ["^GSPC", "^IXIC", "BTC-USD", "GC=F", "CL=F", "EURUSD=X", "^TNX"],
  "matrix": [
    [1.0, 0.8868, -0.0223, 0.1701, 0.21, 0.0, 0.0],
    [0.8868, 1.0, -0.0445, 0.1084, 0.2, 0.0, 0.0]
    /* ... rows × symbols ... */
  ],
  "history": [
    {"timestamp": "...", "pairs": [["^GSPC", "^IXIC", 0.8868], ...]}
  ]
}
```

`matrix[i][j]` is the correlation between `symbols[i]` and `symbols[j]`;
`null` means the pair had insufficient returns for that window.
`history` holds the most recent 50 snapshots and is what the
correlation heatmap uses to track drift over time.

## `data/feedback.json`, `data/precision.json`

Human-in-the-loop labeling of anomalies, with the resulting real-usage
precision metric.

**Feedback** (`data/feedback.json`) — most-recent-wins per anomaly:

```json
{
  "schema": 1,
  "generated_at": "...",
  "feedback": [
    {
      "anomaly_event_id": null,
      "symbol": "BTC-USD",
      "timestamp": "2026-08-26T09:59:12+00:00",
      "label": "confirmed",
      "noted_at": "2026-08-26T11:02:00+00:00",
      "description": "Tick-level zscore_spike ... — high severity"
    }
  ]
}
```

**Precision** (`data/precision.json`) — rolling 30-day aggregation:

```json
{
  "schema": 1,
  "generated_at": "...",
  "window_days": 30,
  "total_labeled": 12,
  "total_confirmed": 8,
  "total_false_positive": 4,
  "overall_precision": 0.6667,
  "by_detector": [
    {"detector": "iforest", "labeled": 7, "confirmed": 5,
     "false_positive": 2, "precision": 0.7143}
  ]
}
```

### Recording feedback — two surfaces, two mechanisms

- **API profiles** (slim + docker-compose): `POST
  /anomalies/{anomaly_id}/feedback` with `{"label":
  "confirmed"|"false_positive", "note": "..."}` writes to the
  `anomaly_feedback` Postgres table (created by `scripts/init_db.py`).
  `GET /anomaly-precision?window_days=30` returns the same shape as
  `precision.json`, computed from the DB.

- **GitHub Pages live site**: the static dashboard renders the labels
  and precision already baked into the JSON, but **there is no write
  path and no user auth/session model on a static site** — any visitor
  could forge a label, so the site deliberately does not accept
  feedback from browsers. Labels on the live profile are recorded
  server-side during pipeline runs (e.g. via the maintainer editing
  `state/feedback.json`, or a future authenticated endpoint); the
  published JSON simply reflects whatever the last run loaded. This is
  a known limitation, accepted in exchange for zero infrastructure.

Detector attribution for `by_detector` comes from the anomaly's
description keywords (zscore_spike → `zscore`,
isolation_forest_outlier → `iforest`, correlation_break →
`correlation`; anything else → `unknown`), shared by both surfaces via
`src/precision.py`.

## `data/charts/{symbol}.json`

Downsampled close-price series per symbol (≤600 points, endpoints kept).
File names escape special characters: `^GSPC → idx_GSPC`, `GC=F → GC_F`.

```json
{
  "schema": 1,
  "symbol": "^GSPC",
  "points": [["2026-08-26T09:30:00+00:00", 5641.1], ["...", 5642.0]]
}
```

## `state/history.json`, `state/anomalies.json` (internal)

Pipeline memory between runs: rolling feature rows (capped 2000/symbol)
and the anomaly log used for cooldown suppression. Not part of the
public contract — consumed only by the workflow itself.
