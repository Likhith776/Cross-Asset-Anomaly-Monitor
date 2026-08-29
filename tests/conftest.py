"""Shared test fixtures.

The suite is fully offline by default: the optional LLM-explanation
feature (src/detection/explain.py) reads GEMINI_API_KEY from the
environment, and a developer's local .env may contain a real key —
which would make tests hit the live Gemini API and consume its
budget. This autouse fixture force-disables the feature for every
test unless a test explicitly sets its own key via monkeypatch.
"""

import pytest


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
