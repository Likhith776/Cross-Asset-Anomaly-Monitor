"""
Traded-universe configuration — single source of truth for symbols,
their human-readable labels, and the monitored correlation pairs.

Loaded from config/universe.json once per process (module-level import
sites cache it). Adding or removing a symbol/pair is a config-file
edit only: no code changes, no redeploy of detector logic.

Validation is fail-fast: a pair referencing an unknown symbol, a
duplicate symbol, or an empty universe raises UniverseError with a
clear message at startup rather than misbehaving later.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

UNIVERSE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "universe.json",
)

_cache: Optional[Universe] = None
_cache_path: Optional[str] = None


class UniverseError(RuntimeError):
    """Raised when config/universe.json is missing, malformed, or invalid."""


@dataclass(frozen=True)
class Universe:
    symbols: tuple[str, ...]
    labels: dict[str, str]
    pairs: tuple[tuple[str, str], ...]


def load_universe(
    path: str = UNIVERSE_PATH,
    force_reload: bool = False,
) -> Universe:
    """
    Load, validate, and cache the universe. Raises UniverseError on any
    problem so callers fail fast at startup. The cache is path-aware:
    a different path always re-reads.
    """
    global _cache, _cache_path
    if _cache is not None and not force_reload and _cache_path == os.path.abspath(path):
        return _cache

    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError as exc:
        raise UniverseError(
            f"universe config not found at {path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise UniverseError(
            f"universe config at {path} is not valid JSON: {exc}"
        ) from exc

    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise UniverseError(
            "universe config must define a non-empty 'symbols' list of "
            "{symbol, label} entries"
        )

    symbols: list[str] = []
    labels: dict[str, str] = {}
    for entry in raw_symbols:
        if not isinstance(entry, dict) or "symbol" not in entry:
            raise UniverseError(
                f"malformed symbol entry {entry!r}: expected "
                '{"symbol": ..., "label": ...}'
            )
        symbol = entry["symbol"]
        if symbol in labels:
            raise UniverseError(f"duplicate symbol in universe: {symbol}")
        symbols.append(symbol)
        labels[symbol] = str(entry.get("label") or symbol)

    raw_pairs = payload.get("correlation_pairs", [])
    if not isinstance(raw_pairs, list):
        raise UniverseError("'correlation_pairs' must be a list of [a, b] pairs")

    pairs: list[tuple[str, str]] = []
    for pair in raw_pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or pair[0] not in labels
            or pair[1] not in labels
        ):
            raise UniverseError(
                f"correlation pair {pair!r} must be a two-element list "
                f"referencing symbols from the universe "
                f"({', '.join(symbols)})"
            )
        if pair[0] == pair[1]:
            raise UniverseError(
                f"correlation pair {pair!r} references the same symbol twice"
            )
        pairs.append((pair[0], pair[1]))

    universe = Universe(
        symbols=tuple(symbols), labels=labels, pairs=tuple(pairs)
    )
    _cache = universe
    _cache_path = os.path.abspath(path)
    logger.info(
        "[UNIVERSE] %d symbols, %d correlation pairs loaded from %s",
        len(universe.symbols), len(universe.pairs), path,
    )
    return universe


def reload_universe(path: str = UNIVERSE_PATH) -> Universe:
    """Force a re-read (used by tests and after manual config edits)."""
    return load_universe(path, force_reload=True)


_cache: Optional[Universe] = None
_cache_path: Optional[str] = None
