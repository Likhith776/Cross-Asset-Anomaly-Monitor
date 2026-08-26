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
      "description": "Tick-level zscore_spike (zscore_price detector), z=4.21 detected on BTC-USD at 71250.0 — high severity"
    }
  ]
}
```

Severity (`low|medium|high|critical`) is embedded in the description
tail until the UI revamp promotes it to a field.

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
