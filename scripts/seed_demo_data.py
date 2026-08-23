#!/usr/bin/env python3
"""
Demo data seeder for the Cross-Asset Anomaly Detection System.

Generates 7 days of realistic per-minute market data for 7 assets,
along with anomaly events and correlation snapshots. Designed so the
dashboard looks populated and useful immediately after a fresh install.

Usage:
    python scripts/seed_demo_data.py
    # To re-seed (wipes existing data first):
    $env DROP_EXISTING=1; python scripts/seed_demo_data.py

Data volumes:
    - market_features:     7 assets × 7 days × 1440 min/day = 70,560 rows
    - anomaly_events:      ~15 rows
    - correlation_snapshots: 21 pairs × 7 days × 24 hr/day = 3,528 rows
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations

import numpy as np

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ASSETS = ["^GSPC", "BTC-USD", "GC=F", "CL=F", "EURUSD=X", "^TNX", "^IXIC"]

# Realistic starting prices (as of mid-2024 ranges)
BASE_PRICES = {
    "^GSPC":     5234.18,
    "BTC-USD":   65432.10,
    "GC=F":      2312.45,
    "CL=F":      78.32,
    "EURUSD=X":  1.0847,
    "^TNX":      4.312,
    "^IXIC":     16399.52,
}

# Per-minute return volatilities (annualized-equivalent scaled to 1-min bars)
# These are rough: equities ~15% annual → sqrt(1/525600)*0.15 ≈ 0.000207
RETURN_VOLATILITY = {
    "^GSPC":     0.000220,
    "BTC-USD":   0.001200,
    "GC=F":      0.000150,
    "CL=F":      0.000380,
    "EURUSD=X":  0.000060,
    "^TNX":      0.000080,
    "^IXIC":     0.000280,
}

# Typical volume per minute (approximate, for realism)
BASE_VOLUMES = {
    "^GSPC":     125000,
    "BTC-USD":   850,
    "GC=F":      42000,
    "CL=F":      38000,
    "EURUSD=X":  2200000,
    "^TNX":      95000,
    "^IXIC":     210000,
}

DAYS = 7
MINUTES_PER_DAY = 1440
ANOMALY_FRACTION = 0.035  # 3.5% of rows will have |z| > 2.5
ANOMALY_EVENT_COUNT = 15
CORRELATION_INTERVAL_MINUTES = 60

# Base cross-asset correlations (rough, realistic)
BASE_CORRELATIONS = {
    ("^GSPC", "^IXIC"):      0.92,
    ("^GSPC", "BTC-USD"):    0.35,
    ("^GSPC", "GC=F"):      -0.15,
    ("^GSPC", "CL=F"):       0.08,
    ("^GSPC", "EURUSD=X"):  -0.05,
    ("^GSPC", "^TNX"):      -0.30,
    ("^IXIC", "BTC-USD"):    0.40,
    ("^IXIC", "GC=F"):      -0.10,
    ("^IXIC", "CL=F"):       0.05,
    ("^IXIC", "EURUSD=X"):  -0.03,
    ("^IXIC", "^TNX"):      -0.25,
    ("BTC-USD", "GC=F"):     0.12,
    ("BTC-USD", "CL=F"):     0.18,
    ("BTC-USD", "EURUSD=X"): 0.05,
    ("BTC-USD", "^TNX"):    -0.10,
    ("GC=F", "CL=F"):        0.22,
    ("GC=F", "EURUSD=X"):    0.15,
    ("GC=F", "^TNX"):        0.08,
    ("CL=F", "EURUSD=X"):   -0.12,
    ("CL=F", "^TNX"):       -0.08,
    ("EURUSD=X", "^TNX"):   -0.45,
}

# Description templates for anomaly events
ANOMALY_DESCRIPTIONS = {
    "zscore": [
        "Price spike detected: {symbol} moved {pct_change:+.2f}% in 1 minute, "
        "z-score = {z_score:.2f}. Possible institutional flow or data irregularity.",
        "Sharp move in {symbol}: {pct_change:+.2f}% deviation from rolling mean "
        "(z = {z_score:.2f}). Cross-referencing with volume surge.",
        "Statistical outlier in {symbol} 1-minute return: {pct_change:+.3f}% "
        "exceeds {z_score:.1f}σ threshold. Flagged for review.",
    ],
    "ewma": [
        "EWMA volatility surge in {symbol}: vol rose to {vol:.6f}, "
        "exceeding adaptive threshold. Regime shift suspected.",
        "Rapid volatility expansion in {symbol}: EWMA vol {vol:.6f} vs "
        "recent average. May indicate event-driven trading.",
    ],
    "pca": [
        "PCA residual spike for {symbol}: residual = {residual:.4f}, "
        "suggesting divergence from cross-asset factor structure.",
        "Cross-asset factor breakdown: {symbol} deviating from PCA-explained "
        "behavior (residual = {residual:.4f}). Correlation structure shift.",
    ],
    "correlation": [
        "Correlation breakdown between {symbol} and peer: observed corr = {corr:.3f} "
        "vs expected ~{expected:.2f}. Possible regime change or stress event.",
        "Divergent price action: {symbol} decoupling from correlated assets. "
        "Correlation dropped to {corr:.3f}. Monitoring for persistence.",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_connection():
    """Create a psycopg2 connection from the DATABASE_URL environment variable."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("[ERROR] DATABASE_URL environment variable is not set.")
        print("        Example: export DATABASE_URL=postgresql://postgres:password@localhost:5432/market_anomalies")
        sys.exit(1)
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except psycopg2.OperationalError as e:
        print(f"[ERROR] Failed to connect to database: {e}")
        sys.exit(1)


def generate_fat_tail_return(vol: float, rng: np.random.Generator, anomaly_prob: float = ANOMALY_FRACTION):
    """
    Generate a single return with fat tails.

    Most draws come from N(0, vol), but with probability anomaly_prob,
    the draw comes from N(0, vol * 5), producing occasional large moves.
    """
    if rng.random() < anomaly_prob:
        return rng.normal(0, vol * 5.0)
    return rng.normal(0, vol)


def compute_ewma(returns: np.ndarray, span: int = 100) -> np.ndarray:
    """Compute exponentially weighted moving average volatility of returns."""
    alpha = 2.0 / (span + 1)
    ewma = np.empty_like(returns)
    ewma[0] = np.abs(returns[0])
    for i in range(1, len(returns)):
        ewma[i] = alpha * (returns[i] ** 2) + (1 - alpha) * ewma[i - 1]
    return np.sqrt(ewma)


def compute_z_scores(returns: np.ndarray, window: int = 100) -> np.ndarray:
    """Compute rolling z-scores for a return series."""
    n = len(returns)
    z = np.full(n, 0.0)
    for i in range(window, n):
        window_slice = returns[i - window:i]
        mu = np.mean(window_slice)
        sigma = np.std(window_slice, ddof=1)
        if sigma > 1e-12:
            z[i] = (returns[i] - mu) / sigma
        else:
            z[i] = 0.0
    return z


def generate_pca_residuals(returns_by_asset: dict[str, np.ndarray], rng: np.random.Generator) -> dict[str, np.ndarray]:
    """
    Generate synthetic PCA residuals.

    In a real system this would come from actual PCA decomposition.
    Here we simulate it: most residuals are small noise, with occasional
    spikes that indicate the asset is deviating from the factor structure.
    """
    residuals = {}
    for symbol, returns in returns_by_asset.items():
        n = len(returns)
        res = rng.normal(0, 0.3, size=n)  # Small noise base
        # Inject occasional spikes (0.5% chance per bar)
        spike_mask = rng.random(n) < 0.005
        res[spike_mask] = rng.normal(0, 4.0, size=spike_mask.sum())
        residuals[symbol] = res
    return residuals


def format_description(template_key: str, **kwargs):
    """Pick a random description template and format it."""
    templates = ANOMALY_DESCRIPTIONS.get(template_key, ["Anomaly detected in {symbol}."])
    template = templates[np.random.randint(len(templates))]
    try:
        return template.format(**kwargs)
    except KeyError:
        return template


# ---------------------------------------------------------------------------
# Data Generators
# ---------------------------------------------------------------------------

def generate_market_features(rng: np.random.Generator) -> list[tuple]:
    """Generate 7 days of per-minute market features for all assets."""
    print(f"[GEN] Generating market_features: "
          f"{len(ASSETS)} assets × {DAYS} days × {MINUTES_PER_DAY} min = "
          f"{len(ASSETS) * DAYS * MINUTES_PER_DAY:,} rows")

    total_minutes = DAYS * MINUTES_PER_DAY
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start_time = now - timedelta(minutes=total_minutes)

    # Generate returns for all assets first (needed for EWMA and z-score)
    returns_by_asset: dict[str, np.ndarray] = {}
    for symbol in ASSETS:
        vol = RETURN_VOLATILITY[symbol]
        returns = np.array([
            generate_fat_tail_return(vol, rng)
            for _ in range(total_minutes)
        ])
        returns_by_asset[symbol] = returns

    # Generate PCA residuals
    pca_residuals = generate_pca_residuals(returns_by_asset, rng)

    # Build rows
    rows = []
    row_count = 0

    for symbol in ASSETS:
        base_price = BASE_PRICES[symbol]
        returns = returns_by_asset[symbol]
        ewma_vol = compute_ewma(returns, span=100)
        z_scores = compute_z_scores(returns, window=100)
        pca_res = pca_residuals[symbol]

        # Simulate price path
        prices = np.empty(total_minutes)
        prices[0] = base_price
        for i in range(1, total_minutes):
            prices[i] = prices[i - 1] * (1.0 + returns[i])

        # Simulate volume (lognormal around base, with spikes)
        base_vol = BASE_VOLUMES[symbol]
        volumes = rng.lognormal(
            mean=np.log(base_vol),
            sigma=0.5,
            size=total_minutes,
        ).astype(np.int64)

        for i in range(total_minutes):
            ts = start_time + timedelta(minutes=i)

            # Compute 5-minute return if we have enough history
            ret_5m = None
            if i >= 5:
                ret_5m = prices[i] / prices[i - 5] - 1.0

            row = (
                ts,
                symbol,
                round(float(prices[i]), 4),
                round(float(returns[i]), 6),
                round(float(ret_5m), 6) if ret_5m is not None else None,
                round(float(z_scores[i]), 4),
                round(float(ewma_vol[i]), 8),
                round(float(pca_res[i]), 4),
                int(volumes[i]),
            )
            rows.append(row)

            row_count += 1
            if row_count % 1000 == 0:
                print(f"       ... {row_count:,} / {len(ASSETS) * total_minutes:,} rows generated")

    print(f"       ... {row_count:,} / {row_count:,} rows generated — complete")
    return rows


def generate_anomaly_events(
    rng: np.random.Generator,
    feature_rows: list[tuple],
) -> list[tuple]:
    """Generate ~15 realistic anomaly events spread across the 7-day window."""
    print(f"[GEN] Generating anomaly_events: ~{ANOMALY_EVENT_COUNT} rows")

    total_minutes = DAYS * MINUTES_PER_DAY
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start_time = now - timedelta(minutes=total_minutes)

    # Pick random timestamps spread across the 7 days
    # Use a stratified approach: at least 1 per day, rest random
    event_minutes = []
    for day in range(DAYS):
        day_start = day * MINUTES_PER_DAY
        day_end = (day + 1) * MINUTES_PER_DAY - 1
        event_minutes.append(rng.integers(day_start, day_end))

    remaining = ANOMALY_EVENT_COUNT - DAYS
    if remaining > 0:
        extra = rng.integers(0, total_minutes, size=remaining)
        event_minutes.extend(extra.tolist())

    event_minutes = sorted(set(event_minutes))[:ANOMALY_EVENT_COUNT]

    # Build a lookup for prices at specific minutes per asset
    # feature_rows are ordered: all minutes for asset 0, then asset 1, etc.
    price_lookup: dict[str, dict[int, float]] = {}
    return_lookup: dict[str, dict[int, float]] = {}
    z_lookup: dict[str, dict[int, float]] = {}
    ewma_lookup: dict[str, dict[int, float]] = {}
    pca_lookup: dict[str, dict[int, float]] = {}

    for row in feature_rows:
        ts, symbol, price, ret_1m, ret_5m, z, ewma, pca, vol = row
        minute_idx = int((ts - start_time).total_seconds() / 60)
        if symbol not in price_lookup:
            price_lookup[symbol] = {}
            return_lookup[symbol] = {}
            z_lookup[symbol] = {}
            ewma_lookup[symbol] = {}
            pca_lookup[symbol] = {}
        price_lookup[symbol][minute_idx] = float(price)
        return_lookup[symbol][minute_idx] = float(ret_1m) if ret_1m else 0.0
        z_lookup[symbol][minute_idx] = float(z)
        ewma_lookup[symbol][minute_idx] = float(ewma)
        pca_lookup[symbol][minute_idx] = float(pca)

    rows = []
    detector_types = ["zscore", "ewma", "pca", "correlation"]

    for minute_idx in event_minutes:
        ts = start_time + timedelta(minutes=int(minute_idx))
        symbol = rng.choice(ASSETS)

        # Determine which flags to set based on the data at this minute
        z_val = z_lookup.get(symbol, {}).get(minute_idx, 0.0)
        ewma_val = ewma_lookup.get(symbol, {}).get(minute_idx, 0.001)
        pca_val = pca_lookup.get(symbol, {}).get(minute_idx, 0.0)
        ret_val = return_lookup.get(symbol, {}).get(minute_idx, 0.0)
        price_val = price_lookup.get(symbol, {}).get(minute_idx, 0.0)

        z_flag = abs(z_val) > 2.5
        ewma_flag = ewma_val > 0.018
        pca_flag = abs(pca_val) > 2.5

        # If no natural flags, force at least one
        if not (z_flag or ewma_flag or pca_flag):
            forced = rng.choice(["z", "ewma", "pca"])
            if forced == "z":
                z_flag = True
                z_val = rng.uniform(2.5, 4.5) * rng.choice([-1, 1])
            elif forced == "ewma":
                ewma_flag = True
                ewma_val = rng.uniform(0.019, 0.025)
            else:
                pca_flag = True
                pca_val = rng.uniform(2.5, 5.0) * rng.choice([-1, 1])

        # Compute anomaly score: max of normalized flag indicators
        score_components = []
        if z_flag:
            score_components.append(min(abs(z_val) / 5.0, 1.0))
        if ewma_flag:
            score_components.append(min(ewma_val / 0.025, 1.0))
        if pca_flag:
            score_components.append(min(abs(pca_val) / 5.0, 1.0))

        if score_components:
            raw_score = max(score_components)
        else:
            raw_score = rng.uniform(0.4, 0.6)

        # Clamp to [0.4, 0.95] as specified
        anomaly_score = round(float(np.clip(raw_score, 0.4, 0.95)), 3)

        # Pick a description based on the primary flag
        pct_change = ret_val * 100
        if z_flag and abs(z_val) >= max(abs(pca_val) if pca_flag else 0, ewma_val / 0.025 if ewma_flag else 0):
            desc = format_description(
                "zscore",
                symbol=symbol,
                pct_change=pct_change,
                z_score=abs(z_val),
            )
        elif ewma_flag:
            desc = format_description(
                "ewma",
                symbol=symbol,
                vol=ewma_val,
            )
        elif pca_flag:
            desc = format_description(
                "pca",
                symbol=symbol,
                residual=pca_val,
            )
        else:
            desc = format_description(
                "zscore",
                symbol=symbol,
                pct_change=pct_change,
                z_score=abs(z_val),
            )

        row = (
            ts,
            symbol,
            anomaly_score,
            z_flag,
            ewma_flag,
            pca_flag,
            desc,
        )
        rows.append(row)

    print(f"       ... {len(rows)} anomaly events generated")
    return rows


def generate_correlation_snapshots(
    rng: np.random.Generator,
) -> list[tuple]:
    """Generate hourly correlation snapshots for all 21 asset pairs."""
    pair_list = list(combinations(sorted(ASSETS), 2))
    total_hours = DAYS * 24
    total_rows = len(pair_list) * total_hours

    print(f"[GEN] Generating correlation_snapshots: "
          f"{len(pair_list)} pairs × {total_hours} hours = {total_rows:,} rows")

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start_time = now - timedelta(hours=total_hours)

    rows = []
    row_count = 0

    for hour_idx in range(total_hours):
        ts = start_time + timedelta(hours=hour_idx)

        # Slowly drift correlations over time with mean-reversion
        for symbol_a, symbol_b in pair_list:
            key = (symbol_a, symbol_b)
            if key in BASE_CORRELATIONS:
                base_corr = BASE_CORRELATIONS[key]
            else:
                # Try reversed
                key_rev = (symbol_b, symbol_a)
                base_corr = BASE_CORRELATIONS.get(key_rev, 0.0)

            # Add realistic noise: smaller noise for highly correlated pairs
            noise_std = 0.03 if abs(base_corr) > 0.5 else 0.08
            noise = rng.normal(0, noise_std)

            # Occasional correlation breaks (1% chance per snapshot)
            if rng.random() < 0.01:
                # Flip or crush the correlation
                if rng.random() < 0.5:
                    correlation = base_corr * -0.3 + rng.normal(0, 0.15)
                else:
                    correlation = rng.normal(0, 0.1)
            else:
                # Mean-reverting random walk component
                walk_component = rng.normal(0, 0.005)
                correlation = base_corr + noise + walk_component

            # Clamp to valid range
            correlation = float(np.clip(correlation, -1.0, 1.0))

            row = (ts, symbol_a, symbol_b, round(correlation, 4))
            rows.append(row)

            row_count += 1
            if row_count % 1000 == 0:
                print(f"       ... {row_count:,} / {total_rows:,} rows generated")

    print(f"       ... {row_count:,} / {total_rows:,} rows generated — complete")
    return rows


# ---------------------------------------------------------------------------
# Insertion
# ---------------------------------------------------------------------------

def insert_market_features(cur, rows: list[tuple]):
    """Bulk insert market_features rows using COPY-style execute_values."""
    print(f"[INSERT] market_features: {len(rows):,} rows ...", end=" ", flush=True)
    start = time.time()

    sql = """
        INSERT INTO market_features
            (timestamp, symbol, price, return_1m, return_5m, z_score, ewma_vol, pca_residual, volume)
        VALUES %s
    """
    execute_values(cur, sql, rows, page_size=5000)

    elapsed = time.time() - start
    rate = len(rows) / elapsed if elapsed > 0 else 0
    print(f"done ({elapsed:.1f}s, {rate:,.0f} rows/s)")


def insert_anomaly_events(cur, rows: list[tuple]):
    """Insert anomaly_events rows."""
    print(f"[INSERT] anomaly_events: {len(rows)} rows ...", end=" ", flush=True)
    start = time.time()

    sql = """
        INSERT INTO anomaly_events
            (timestamp, symbol, anomaly_score, z_flag, ewma_flag, pca_flag, description)
        VALUES %s
    """
    execute_values(cur, sql, rows, page_size=100)

    elapsed = time.time() - start
    print(f"done ({elapsed:.2f}s)")


def insert_correlation_snapshots(cur, rows: list[tuple]):
    """Bulk insert correlation_snapshots rows."""
    print(f"[INSERT] correlation_snapshots: {len(rows):,} rows ...", end=" ", flush=True)
    start = time.time()

    sql = """
        INSERT INTO correlation_snapshots
            (timestamp, symbol_a, symbol_b, correlation)
        VALUES %s
        ON CONFLICT (timestamp, symbol_a, symbol_b) DO NOTHING
    """
    execute_values(cur, sql, rows, page_size=5000)

    elapsed = time.time() - start
    rate = len(rows) / elapsed if elapsed > 0 else 0
    print(f"done ({elapsed:.1f}s, {rate:,.0f} rows/s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 64)
    print("  Cross-Asset Anomaly Detection — Demo Data Seeder")
    print("=" * 64)
    print()

    # Seed numpy for reproducibility (but still looks random)
    rng = np.random.default_rng(seed=42)

    # Connect
    conn = get_connection()
    cur = conn.cursor()

    try:
        # Optionally wipe existing data
        drop_existing = os.environ.get("DROP_EXISTING", "").lower() in ("1", "true", "yes")
        if drop_existing:
            print("[WIPE] Dropping existing data ...")
            cur.execute("TRUNCATE TABLE correlation_snapshots CASCADE;")
            cur.execute("TRUNCATE TABLE anomaly_events CASCADE;")
            cur.execute("TRUNCATE TABLE market_features CASCADE;")
            conn.commit()
            print("       All tables truncated.")
            print()

        # Generate all data
        t0 = time.time()
        print("--- Generation Phase ---")
        feature_rows = generate_market_features(rng)
        print()
        anomaly_rows = generate_anomaly_events(rng, feature_rows)
        print()
        corr_rows = generate_correlation_snapshots(rng)
        gen_time = time.time() - t0
        print(f"\n[GEN] Total generation time: {gen_time:.1f}s")
        print()

        # Insert all data
        print("--- Insertion Phase ---")
        t0 = time.time()
        insert_market_features(cur, feature_rows)
        insert_anomaly_events(cur, anomaly_rows)
        insert_correlation_snapshots(cur, corr_rows)
        conn.commit()
        insert_time = time.time() - t0
        print(f"\n[INSERT] Total insertion time: {insert_time:.1f}s")
        print()

        # Verify
        print("--- Verification ---")
        tables = ["market_features", "anomaly_events", "correlation_snapshots"]
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"         {table:<30} {count:>10,} rows")

        # Sample checks
        print()
        print("--- Sample Data ---")

        cur.execute("""
            SELECT timestamp, symbol, price, return_1m, z_score, ewma_vol
            FROM market_features
            ORDER BY timestamp DESC
            LIMIT 5
        """)
        print("\n  Latest market_features:")
        for row in cur.fetchall():
            print(f"    {row[0]} | {row[1]:<12} | {row[2]:>12.4f} | "
                  f"ret={row[3]:>10.6f} | z={row[4]:>7.3f} | vol={row[5]:.8f}")

        cur.execute("""
            SELECT timestamp, symbol, anomaly_score, z_flag, ewma_flag, pca_flag
            FROM anomaly_events
            ORDER BY anomaly_score DESC
            LIMIT 5
        """)
        print("\n  Top anomaly_events by score:")
        for row in cur.fetchall():
            flags = []
            if row[3]: flags.append("Z")
            if row[4]: flags.append("EWMA")
            if row[5]: flags.append("PCA")
            print(f"    {row[0]} | {row[1]:<12} | score={row[2]:.3f} | flags=[{', '.join(flags)}]")

        cur.execute("""
            SELECT timestamp, symbol_a, symbol_b, correlation
            FROM correlation_snapshots
            ORDER BY timestamp DESC
            LIMIT 5
        """)
        print("\n  Latest correlation_snapshots:")
        for row in cur.fetchall():
            print(f"    {row[0]} | {row[1]:<12} / {row[2]:<12} | corr={row[3]:.4f}")

        # Z-score distribution check
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE ABS(z_score) > 2.0) as above_2,
                COUNT(*) FILTER (WHERE ABS(z_score) > 2.5) as above_25,
                COUNT(*) FILTER (WHERE ABS(z_score) > 3.0) as above_3,
                AVG(z_score) as mean_z,
                STDDEV(z_score) as std_z
            FROM market_features
            WHERE z_score IS NOT NULL AND z_score != 0
        """)
        stats = cur.fetchone()
        print(f"\n  Z-score distribution:")
        print(f"    Total non-zero:  {stats[0]:>10,}")
        print(f"    |z| > 2.0:       {stats[1]:>10,} ({100*stats[1]/stats[0]:.1f}%)")
        print(f"    |z| > 2.5:       {stats[2]:>10,} ({100*stats[2]/stats[0]:.1f}%)")
        print(f"    |z| > 3.0:       {stats[3]:>10,} ({100*stats[3]/stats[0]:.1f}%)")
        print(f"    Mean z:          {stats[4]:>10.4f}")
        print(f"    Std z:           {stats[5]:>10.4f}")

        total_time = gen_time + insert_time
        print()
        print("=" * 64)
        print(f"  DONE — Total time: {total_time:.1f}s")
        print(f"  Dashboard is ready at http://localhost:8501")
        print("=" * 64)

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] Seeding failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cur.close()
        conn.close()
        print("\n[INFO] Database connection closed.")


if __name__ == "__main__":
    main()