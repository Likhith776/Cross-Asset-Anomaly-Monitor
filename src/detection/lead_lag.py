"""
Lead-lag annotation: hints which asset likely moved first.

When an anomaly fires on a symbol, the detector sees only that symbol.
If the symbol's recent returns consistently trail (or lead) another
tracked symbol's returns by a couple of ticks, the "anomaly" may be an
echo of the partner's earlier move — or the origin of one. This module
computes lagged cross-correlations between the anomaly symbol and its
universe-configured correlation partners and returns the strongest
nonzero-lag relationship, as context appended to the anomaly record.

Annotation only — detection behavior is never gated. Follows the
macro_calendar.py conventions: small, stdlib-only (statistics.correlation
for Pearson — no third-party dependency), pure function, returns None
when nothing clears the threshold.

Partner symbols come from config/universe.json via src/universe.py —
never hardcoded here.
"""

import logging
import re
from typing import Optional

from src.universe import load_universe

logger = logging.getLogger(__name__)

# Farthest shift (in ticks) examined in either direction.
MAX_LAG_TICKS = 5
# Minimum |correlation| at a nonzero lag for the signal to be reported —
# below this, lagged correlation on short windows is noise.
MIN_LEAD_LAG_CORRELATION = 0.5
# Both series need at least this many aligned observations per lag.
MIN_LEAD_LAG_SAMPLES = 30
# How many recent returns each insert site should hand over (covers
# max lag + minimum samples with margin).
DEFAULT_MAX_POINTS = 60

# Description marker: "[lead-lag: led by GC=F (2 ticks, r=0.71)]"
_LEAD_LAG_MARKER = re.compile(
    r"\[lead-lag: led by (\S+) \((\d+) ticks, r=(-?\d+\.?\d*)\)\]"
)


def _pearson(x: list[float], y: list[float]) -> Optional[float]:
    from statistics import StatisticsError, correlation

    try:
        return float(correlation(x, y))
    except (StatisticsError, ValueError):
        return None  # zero variance in either series


def lead_lag_for(
    symbol: str,
    returns_by_symbol: dict[str, list[float]],
    *,
    max_lag: int = MAX_LAG_TICKS,
    min_correlation: float = MIN_LEAD_LAG_CORRELATION,
    min_samples: int = MIN_LEAD_LAG_SAMPLES,
    pairs: Optional[list[tuple[str, str]]] = None,
) -> Optional[dict]:
    """
    Strongest nonzero-lag relationship between `symbol` and its
    universe-configured correlation partners, or None.

    Positive reported lag: the PARTNER moved first (its returns at
    t-lag align with the symbol's at t) — the anomaly on `symbol` is
    likely an echo. Negative internal lag: `symbol` moved first and
    the partner followed — reported with `leader` set to `symbol`.

    `returns_by_symbol` maps symbol -> per-tick returns, most recent
    last. `pairs` defaults to config/universe.json via load_universe();
    only partners paired with `symbol` in that config are considered.
    """
    if pairs is None:
        pairs = [tuple(p) for p in load_universe().pairs]

    partners: list[str] = []
    for a, b in pairs:
        if a == symbol and b != symbol:
            partners.append(b)
        elif b == symbol and a != symbol:
            partners.append(a)
    if not partners:
        return None

    own = returns_by_symbol.get(symbol)
    if not own or len(own) < min_samples + max_lag:
        return None

    best: Optional[dict] = None
    for partner in partners:
        other = returns_by_symbol.get(partner)
        if not other or len(other) < min_samples + max_lag:
            continue

        n = min(len(own), len(other))
        own_tail = own[-n:]
        other_tail = other[-n:]

        for lag in range(-max_lag, max_lag + 1):
            if lag == 0:
                continue  # contemporaneous correlation is not lead-lag
            if lag > 0:
                # partner's move at t-lag aligns with the symbol's at t
                x = own_tail[lag:]
                y = other_tail[: n - lag]
                leader = partner
            else:
                k = -lag
                x = own_tail[: n - k]
                y = other_tail[k:]
                leader = symbol
            if len(x) < min_samples:
                continue

            r = _pearson(x, y)
            if r is None or abs(r) < min_correlation:
                continue

            candidate = {
                "leader": leader,
                "partner": partner,
                "lag_ticks": abs(lag),
                "correlation": round(r, 3),
            }
            if best is None or abs(candidate["correlation"]) > abs(
                best["correlation"]
            ):
                best = candidate

    return best


def augment_description(description: str, result: dict) -> str:
    """Append the lead-lag marker (macro_calendar.augment_description's
    counterpart); the marker format is parseable by extract_lead_lag."""
    marker = (
        f" [lead-lag: led by {result['leader']} "
        f"({result['lag_ticks']} ticks, r={result['correlation']})]"
    )
    return f"{description}{marker}"


def extract_lead_lag(description: Optional[str]) -> Optional[dict]:
    """Structured lead-lag from a description marker, or None."""
    m = _LEAD_LAG_MARKER.search(description or "")
    if not m:
        return None
    return {
        "leader": m.group(1),
        "lag_ticks": int(m.group(2)),
        "correlation": float(m.group(3)),
    }
