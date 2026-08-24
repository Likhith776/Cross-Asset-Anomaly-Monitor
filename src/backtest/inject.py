"""Labeled anomaly injection for supervised evaluation of detectors.

Plants controlled, known events into a real price series so recall and
precision can be measured against ground truth. Magnitudes are expressed
in price-level sigmas (std of levels over the warm-up reference chunk),
which is detector-agnostic: both the level-based z-score detector and the
return-based isolation forest get a fair shot at the same physical move.
"""

from dataclasses import dataclass

import numpy as np

# Events are only planted after this many ticks so every detector's window
# is fully warmed by clean data before anything anomalous happens.
DEFAULT_WARMUP = 150


@dataclass(frozen=True)
class PlantedEvent:
    """A labeled region injected into a series (inclusive of `end`)."""

    start: int
    end: int
    kind: str  # "spike" | "dip" | "vol_burst"
    magnitude_sigma: float

    @property
    def duration(self) -> int:
        return self.end - self.start + 1


def choose_event_positions(
    n: int,
    n_events: int,
    first_valid: int,
    min_separation: int,
    rng: np.random.Generator,
) -> list[int]:
    """
    Pick ascending, mutually separated positions for planted events.

    Positions are drawn inside equal slots carved from the valid range,
    so neighbors are always at least `min_separation` apart (one event's
    price window returns to clean statistics before the next lands).
    If more events are requested than fit, the feasible maximum is
    placed instead. Returns [] when nothing fits.
    """
    upper = n - min_separation  # leave room for burst tails / windows
    width = upper - first_valid
    if width <= 0:
        return []
    feasible = min(n_events, width // max(min_separation, 1) + 1)
    if feasible <= 0:
        return []
    slot = width // feasible
    return sorted(
        int(
            first_valid
            + i * slot
            + rng.integers(0, max(slot - min_separation + 1, 1))
        )
        for i in range(feasible)
    )


def plant_anomalies(
    closes: np.ndarray,
    n_events: int,
    magnitude_sigma: float,
    *,
    warmup: int = DEFAULT_WARMUP,
    burst_len: int = 5,
    seed=None,
) -> tuple[np.ndarray, list[PlantedEvent]]:
    """
    Inject `n_events` labeled anomalies into a copy of `closes`.

    Kinds cycle spike -> dip -> vol_burst:
      - spike/dip: single tick moved ±magnitude_sigma level-sigmas off the
        previous close (a repricing gap).
      - vol_burst: `burst_len` consecutive moves of ~±0.5·k level-sigmas
        each (a volatility regime flare).

    The first `warmup` ticks are never touched. Event spacing keeps
    windows from overlapping. Returns (new_closes, events).
    """
    rng = np.random.default_rng(seed)
    closes = np.asarray(closes, dtype=float)
    out = closes.copy()

    # Level sigma from the clean reference chunk; guards degenerate input.
    ref = closes[:warmup] if len(closes) >= warmup else closes
    level_sigma = float(np.std(ref))
    if not np.isfinite(level_sigma) or level_sigma <= 0:
        level_sigma = max(abs(float(np.mean(ref))) * 1e-3, 1e-9)

    min_sep = warmup // 2 + burst_len
    positions = choose_event_positions(
        len(closes), n_events, first_valid=warmup, min_separation=min_sep, rng=rng
    )

    kinds = ("spike", "dip", "vol_burst")
    events: list[PlantedEvent] = []
    for i, pos in enumerate(positions):
        kind = kinds[i % len(kinds)]
        if kind == "spike":
            out[pos] = closes[pos - 1] + magnitude_sigma * level_sigma
            events.append(PlantedEvent(pos, pos, kind, magnitude_sigma))
        elif kind == "dip":
            out[pos] = closes[pos - 1] - magnitude_sigma * level_sigma
            events.append(PlantedEvent(pos, pos, kind, magnitude_sigma))
        else:
            for j in range(burst_len):
                sign = 1.0 if rng.random() < 0.5 else -1.0
                out[pos + j] = out[pos + j - 1] + sign * 0.5 * magnitude_sigma * level_sigma
            events.append(
                PlantedEvent(pos, pos + burst_len - 1, kind, magnitude_sigma)
            )

    return out, events
