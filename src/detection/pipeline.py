"""
Detection pipeline that orchestrates multiple detectors.

Runs all configured detectors on each incoming price point
and aggregates the results.
"""

import logging
import time
from datetime import datetime
from typing import Any, Optional

import numpy as np

from src.detection.base import BaseDetector, AnomalyEvent
from src.detection.zscore import ZScoreDetector
from src.detection.isolation_forest import IsolationForestDetector
from src.detection.cross_asset import CrossAssetCorrelationDetector
from src.metrics import DETECTOR_LATENCY

logger = logging.getLogger(__name__)


class DetectionPipeline:
    """
    Orchestrates multiple anomaly detectors.

    Each incoming price tick is passed through all registered detectors.
    If multiple detectors flag the same asset, the highest-scoring anomaly
    is returned (or all anomalies if aggregate=False).

    Parameters:
        aggregate: If True, return only the highest-scoring anomaly per tick.
                   If False, return all detected anomalies.
    """

    def __init__(self, aggregate: bool = True):
        self.aggregate = aggregate
        self.detectors: list[BaseDetector] = []
        self._setup_default_detectors()

    def _setup_default_detectors(self) -> None:
        """Initialize the default set of detectors."""
        self.detectors = [
            ZScoreDetector(
                name="zscore_price",
                window_size=100,
                threshold=3.0,
                min_observations=20,
            ),
            ZScoreDetector(
                name="zscore_volume",
                window_size=50,
                threshold=2.5,
                min_observations=15,
            ),
            IsolationForestDetector(
                name="iforest_price",
                window_size=100,
                threshold=0.05,
                min_observations=30,
                retrain_interval=50,
            ),
            CrossAssetCorrelationDetector(
                name="cross_asset_corr",
                window_size=100,
                threshold=2.5,
                min_observations=50,
            ),
        ]
        logger.info(
            "Initialized %d detectors: %s",
            len(self.detectors),
            [d.name for d in self.detectors],
        )

    def warm_start(
        self,
        prices_by_symbol: dict[str, list[tuple[Any, float]]],
    ) -> dict[str, int]:
        """
        Preload detector state from recent prices so tick-level detection
        is fully active immediately after a restart, instead of after the
        20-50 tick cold start each detector requires.

        prices_by_symbol maps symbol -> oldest-first (timestamp, close)
        samples. Detector price windows are refilled by replaying each
        series; isolation forests are pre-trained per symbol when enough
        samples exist; cross-asset correlation histories are rebuilt from
        the warmed windows.

        Returns per-symbol counts of replayed prices (all zeros -> cold).
        """
        counts: dict[str, int] = {}

        # 1) Refill every detector's price windows by replaying each series
        for symbol, samples in prices_by_symbol.items():
            played = 0
            for _, price in samples:
                try:
                    price_val = float(price)
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(price_val) or price_val <= 0:
                    continue
                for detector in self.detectors:
                    try:
                        detector._update_window(symbol, price_val)
                    except Exception:
                        logger.debug(
                            "[WARM] window update failed on %s", detector.name,
                            exc_info=True,
                        )
                played += 1
            counts[symbol] = played

        # 2) Pre-train isolation forests so outliers are detectable at once
        from sklearn.ensemble import IsolationForest

        for detector in self.detectors:
            if not isinstance(detector, IsolationForestDetector):
                continue
            for symbol, window in detector._price_windows.items():
                if len(window) < detector.min_observations or symbol in detector._models:
                    continue
                try:
                    features = detector._extract_features(np.array(window))
                    detector._models[symbol] = IsolationForest(
                        contamination=detector.threshold,
                        n_estimators=100,
                        max_samples="auto",
                        random_state=42,
                        n_jobs=-1,
                    )
                    detector._models[symbol].fit(features)
                    detector._observation_counts[symbol] = len(window)
                    logger.debug(
                        "[WARM] pre-trained %s for %s (%d obs)",
                        detector.name, symbol, len(window),
                    )
                except Exception:
                    logger.debug(
                        "[WARM] iforest training failed for %s", symbol,
                        exc_info=True,
                    )

        # 3) Rebuild correlation-break histories from the warmed windows
        for detector in self.detectors:
            if not isinstance(detector, CrossAssetCorrelationDetector):
                continue
            for asset_a, asset_b in detector.pairs:
                pa = detector._price_windows.get(asset_a)
                pb = detector._price_windows.get(asset_b)
                if not pa or not pb:
                    continue
                min_len = min(len(pa), len(pb))
                ret_a = detector._compute_returns(pa[-min_len:])
                ret_b = detector._compute_returns(pb[-min_len:])
                common = min(len(ret_a), len(ret_b))
                ret_a, ret_b = ret_a[-common:], ret_b[-common:]
                rolling = min(50, common)
                step = 5
                history = detector._corr_history.setdefault((asset_a, asset_b), [])
                for end in range(rolling, common + 1, step):
                    corr = np.corrcoef(ret_a[end - rolling:end], ret_b[end - rolling:end])[0, 1]
                    if np.isnan(corr):
                        continue
                    history.append(float(corr))
                    if len(history) > detector._history_window:
                        history.pop(0)
                if history:
                    logger.debug(
                        "[WARM] rebuilt correlation history %s/%s: %d samples",
                        asset_a, asset_b, len(history),
                    )

        return counts

    def add_detector(self, detector: BaseDetector) -> None:
        """Add a custom detector to the pipeline."""
        self.detectors.append(detector)
        logger.info("Added detector: %s", detector.name)

    def remove_detector(self, name: str) -> None:
        """Remove a detector by name."""
        self.detectors = [d for d in self.detectors if d.name != name]
        logger.info("Removed detector: %s", name)

    def detect(
        self,
        asset: str,
        price: float,
        timestamp: datetime,
    ) -> list[dict[str, Any]]:
        """
        Run all detectors on a price point.

        Args:
            asset: Asset symbol (e.g., "BTC-USD")
            price: Current price
            timestamp: Timestamp of the price observation

        Returns:
            List of anomaly dictionaries. If aggregate=True, contains at most
            one entry (the highest-scoring anomaly).
        """
        anomalies: list[AnomalyEvent] = []

        for detector in self.detectors:
            started = time.perf_counter()
            try:
                event = detector.detect(asset=asset, price=price, timestamp=timestamp)
                if event is not None:
                    anomalies.append(event)
                    logger.debug(
                        "Detector %s flagged anomaly for %s: score=%.4f",
                        detector.name,
                        asset,
                        event.score,
                    )
            except Exception as e:
                logger.error(
                    "Error in detector %s for asset %s: %s",
                    detector.name,
                    asset,
                    e,
                    exc_info=True,
                )
            finally:
                DETECTOR_LATENCY.labels(detector.name).observe(
                    time.perf_counter() - started
                )

        if self.aggregate and anomalies:
            anomalies.sort(key=lambda a: a.score, reverse=True)
            anomalies = anomalies[:1]

        return [a.to_dict() for a in anomalies]

    def get_detector_info(self) -> list[dict[str, Any]]:
        """Return information about all registered detectors."""
        return [
            {
                "name": d.name,
                "type": d.__class__.__name__,
                "window_size": d.window_size,
                "threshold": d.threshold,
                "assets_tracked": list(d._price_windows.keys()),
            }
            for d in self.detectors
        ]