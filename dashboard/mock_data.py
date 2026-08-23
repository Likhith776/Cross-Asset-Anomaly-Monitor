"""
Mock data module for the Cross-Asset Anomaly Detection Dashboard.

Returns realistic static data for all API endpoints so the dashboard
renders fully on Streamlit Cloud without a running backend. Every
function mirrors the shape of the real API response exactly.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYMBOLS = ["^GSPC", "^IXIC", "BTC-USD", "GC=F", "CL=F", "EURUSD=X", "^TNX"]

BASE_PRICES = {
    "^GSPC": 5234.18,
    "^IXIC": 16399.52,
    "BTC-USD": 65432.10,
    "GC=F": 2312.45,
    "CL=F": 78.32,
    "EURUSD=X": 1.0847,
    "^TNX": 4.312,
}

BASELINE_CORRELATIONS = {
    ("^GSPC", "^IXIC"): 0.92,
    ("^GSPC", "BTC-USD"): 0.35,
    ("^GSPC", "GC=F"): -0.15,
    ("^GSPC", "CL=F"): 0.08,
    ("^GSPC", "EURUSD=X"): -0.05,
    ("^GSPC", "^TNX"): -0.30,
    ("^IXIC", "BTC-USD"): 0.40,
    ("^IXIC", "GC=F"): -0.10,
    ("^IXIC", "CL=F"): 0.05,
    ("^IXIC", "EURUSD=X"): -0.03,
    ("^IXIC", "^TNX"): -0.25,
    ("BTC-USD", "GC=F"): 0.12,
    ("BTC-USD", "CL=F"): 0.18,
    ("BTC-USD", "EURUSD=X"): 0.05,
    ("BTC-USD", "^TNX"): -0.10,
    ("GC=F", "CL=F"): 0.22,
    ("GC=F", "EURUSD=X"): 0.15,
    ("GC=F", "^TNX"): 0.08,
    ("CL=F", "EURUSD=X"): -0.12,
    ("CL=F", "^TNX"): -0.08,
    ("EURUSD=X", "^TNX"): -0.45,
}


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def get_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db": "mock",
    }


# ---------------------------------------------------------------------------
# /assets
# ---------------------------------------------------------------------------

def get_assets() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    asset_configs = [
        ("^GSPC",     0.12,  0.0008, 0.82,  0.00021, "low"),
        ("^IXIC",     0.08,  0.0011, 0.45,  0.00028, "low"),
        ("BTC-USD",   3.42,  0.0089, 3.21,  0.00115, "high"),
        ("GC=F",      0.05,  0.0006, 0.38,  0.00014, "low"),
        ("CL=F",      2.81,  0.0052, 2.67,  0.00042, "high"),
        ("EURUSD=X", -0.02,  0.0003, 0.12,  0.00005, "low"),
        ("^TNX",      1.38,  0.0018, 0.95,  0.00009, "medium"),
    ]

    results = []
    for i, (symbol, z, ret, pca, ewma, risk) in enumerate(asset_configs):
        price = BASE_PRICES[symbol] * (1 + ret)
        results.append({
            "symbol": symbol,
            "price": round(price, 4),
            "return_1m": ret,
            "z_score": z,
            "ewma_vol": round(ewma, 8),
            "pca_residual": round(pca, 4),
            "timestamp": (now - timedelta(seconds=i * 3)).isoformat(),
            "composite_score": round(
                0.35 * min(abs(z) / 4.0, 1.0)
                + 0.30 * min(pca / 5.0, 1.0),
                3,
            ),
            "risk_level": risk,
        })

    return results


# ---------------------------------------------------------------------------
# /anomalies
# ---------------------------------------------------------------------------

def get_anomalies(limit: int = 50, min_score: float = 0.0) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)

    all_events = [
        (45,  "BTC-USD",  0.912, True,  False, True,
         "Z-score spike (z=3.65), cross-asset factor breakdown, z_pca=4.12 "
         "detected on BTC-USD at 67182.3400 — possible correlation structure breakdown"),
        (120, "CL=F",     0.856, True,  True,  False,
         "Z-score spike (z=3.14), volatility surge (2.8x baseline) "
         "detected on CL=F at 81.0500 — possible regime shift or event-driven trading"),
        (180, "BTC-USD",  0.783, False, True,  True,
         "volatility surge (3.1x baseline), cross-asset factor breakdown, z_pca=3.87 "
         "detected on BTC-USD at 64890.2200 — possible correlation structure breakdown"),
        (300, "^GSPC",    0.641, True,  False, True,
         "Z-score spike (z=2.62), cross-asset factor breakdown, z_pca=3.05 "
         "detected on ^GSPC at 5198.3400 — possible correlation structure breakdown"),
        (420, "^TNX",     0.612, False, False, True,
         "cross-asset factor breakdown, z_pca=2.95 "
         "detected on ^TNX at 4.4280 — possible correlation structure breakdown"),
        (540, "GC=F",     0.578, True,  False, False,
         "Z-score spike (z=2.78) "
         "detected on GC=F at 2356.7800 — possible sudden price dislocation"),
        (660, "CL=F",     0.534, False, True,  False,
         "volatility surge (2.3x baseline) "
         "detected on CL=F at 76.1800 — possible regime shift or event-driven trading"),
        (780, "BTC-USD",  0.498, True,  False, False,
         "Z-score spike (z=2.55) "
         "detected on BTC-USD at 66210.8800 — possible sudden price dislocation"),
        (900, "^IXIC",    0.467, False, False, True,
         "cross-asset factor breakdown, z_pca=2.89 "
         "detected on ^IXIC at 16180.1200 — possible correlation structure breakdown"),
        (1020, "^GSPC",   0.423, True,  False, False,
         "Z-score spike (z=2.51) "
         "detected on ^GSPC at 5210.6700 — possible sudden price dislocation"),
        (1200, "BTC-USD", 0.398, False, True,  False,
         "volatility surge (2.1x baseline) "
         "detected on BTC-USD at 65890.4500 — possible regime shift or event-driven trading"),
        (1500, "EURUSD=X", 0.371, True, False, False,
         "Z-score spike (z=2.52) "
         "detected on EURUSD=X at 1.0912 — possible sudden price dislocation"),
        (1800, "^TNX",    0.345, False, False, True,
         "cross-asset factor breakdown, z_pca=2.82 "
         "detected on ^TNX at 4.3850 — possible correlation structure breakdown"),
        (2400, "GC=F",    0.332, False, True,  False,
         "volatility surge (2.0x baseline) "
         "detected on GC=F at 2298.1200 — possible regime shift or event-driven trading"),
        (3000, "CL=F",    0.318, True,  False, False,
         "Z-score spike (z=2.50) "
         "detected on CL=F at 77.9500 — possible sudden price dislocation"),
    ]

    events = []
    for mins_ago, symbol, score, z, ewma, pca, desc in all_events:
        if score < min_score:
            continue
        events.append({
            "id": mins_ago,
            "timestamp": (now - timedelta(minutes=mins_ago)).isoformat(),
            "symbol": symbol,
            "anomaly_score": score,
            "z_flag": z,
            "ewma_flag": ewma,
            "pca_flag": pca,
            "description": desc,
        })

    return events[:limit]


# ---------------------------------------------------------------------------
# /anomalies/{symbol}
# ---------------------------------------------------------------------------

def get_anomalies_by_symbol(symbol: str, days: int = 7) -> list[dict[str, Any]]:
    all_events = get_anomalies(limit=200, min_score=0.0)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    return [
        e for e in all_events
        if e["symbol"] == symbol
        and datetime.fromisoformat(e["timestamp"]) >= cutoff
    ]


# ---------------------------------------------------------------------------
# /correlations
# ---------------------------------------------------------------------------

def get_correlations() -> dict[str, Any]:
    n = len(SYMBOLS)
    matrix = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    idx = {s: i for i, s in enumerate(SYMBOLS)}

    for (a, b), corr in BASELINE_CORRELATIONS.items():
        i, j = idx[a], idx[b]
        matrix[i][j] = corr
        matrix[j][i] = corr

    # Add small noise for realism
    rng = np.random.RandomState(42)
    noise = rng.normal(0, 0.02, (n, n))
    noise = (noise + noise.T) / 2  # Keep symmetric
    for i in range(n):
        for j in range(n):
            if i != j:
                matrix[i][j] = round(np.clip(matrix[i][j] + noise[i][j], -1.0, 1.0), 4)

    return {"labels": SYMBOLS, "matrix": matrix}


# ---------------------------------------------------------------------------
# /chart/{symbol}
# ---------------------------------------------------------------------------

# Pre-generate chart data for every symbol (deterministic)
_chart_cache: dict[str, list[dict[str, Any]]] = {}


def _generate_chart_data(symbol: str, limit: int = 100) -> list[dict[str, Any]]:
    """Generate realistic chart data for one symbol."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = BASE_PRICES.get(symbol, 100.0)

    # Use symbol hash as seed for variety across symbols
    seed = abs(hash(symbol)) % (2**31)
    rng = np.random.RandomState(seed)

    # Price random walk
    vol = 0.0005 if symbol in ("EURUSD=X", "^TNX") else 0.0015
    if symbol == "BTC-USD":
        vol = 0.003
    elif symbol in ("CL=F", "GC=F"):
        vol = 0.001

    returns = rng.normal(0, vol, limit + 20)
    prices = base * np.cumprod(1 + returns)

    # EWMA volatility (slowly varying)
    ewma_base = 0.0003 if symbol in ("EURUSD=X", "^TNX") else 0.001
    if symbol == "BTC-USD":
        ewma_base = 0.004
    ewma_raw = ewma_base + rng.normal(0, ewma_base * 0.3, limit + 20)
    ewma_raw = np.clip(ewma_raw, 1e-6, None)

    # Z-scores (mostly small)
    z_scores = rng.normal(0, 0.8, limit + 20)

    # Composite scores (mostly low, with injected spikes)
    scores = np.abs(rng.normal(0.08, 0.06, limit + 20))
    scores = np.clip(scores, 0, 1)

    # Inject anomaly spikes at specific positions (varies by symbol)
    spike_positions = {
        "^GSPC":     {70: 0.45, 85: 0.32},
        "^IXIC":     {55: 0.28},
        "BTC-USD":   {35: 0.72, 80: 0.88, 92: 0.52},
        "GC=F":      {60: 0.35},
        "CL=F":      {45: 0.65, 75: 0.38},
        "EURUSD=X":  {},
        "^TNX":     {50: 0.42, 88: 0.31},
    }
    for pos, val in spike_positions.get(symbol, {}).items():
        if pos < len(scores):
            scores[pos] = val
            z_scores[pos] = val * 4.0 * rng.choice([-1, 1])
            ewma_raw[pos] = ewma_base * (1 + val * 3)

    # Slice to requested limit (drop the extra history rows)
    prices = prices[-limit:]
    ewma_raw = ewma_raw[-limit:]
    z_scores = z_scores[-limit:]
    scores = scores[-limit:]
    rets = returns[-limit:]

    points = []
    for i in range(limit):
        ts = now - timedelta(minutes=limit - 1 - i)
        points.append({
            "timestamp": ts.isoformat(),
            "price": round(float(prices[i]), 4),
            "return_1m": round(float(rets[i]), 6),
            "z_score": round(float(z_scores[i]), 4),
            "ewma_vol": round(float(ewma_raw[i]), 8),
            "pca_residual": round(float(scores[i] * 3), 4),
            "volume": int(rng.integers(10000, 500000)),
            "composite_score": round(float(scores[i]), 3),
        })

    return points


def get_chart(symbol: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return cached chart data for a symbol."""
    cache_key = f"{symbol}_{limit}"
    if cache_key not in _chart_cache:
        _chart_cache[cache_key] = _generate_chart_data(symbol, limit)
    return _chart_cache[cache_key]