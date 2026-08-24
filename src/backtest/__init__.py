"""Detector backtesting: replay real history and planted anomalies through
the detection pipeline to measure false-positive rates, recall, and
detection lag per detector."""

from src.backtest.inject import PlantedEvent, plant_anomalies
from src.backtest.harness import replay_panel, score_trial

__all__ = ["PlantedEvent", "plant_anomalies", "replay_panel", "score_trial"]
