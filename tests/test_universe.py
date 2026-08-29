"""Universe config: validated loading, fail-fast errors, config-only edits."""

import json

import pytest

from src.universe import Universe, UniverseError, load_universe


VALID = {
    "symbols": [
        {"symbol": "AAA", "label": "Asset A"},
        {"symbol": "BBB", "label": "Asset B"},
    ],
    "correlation_pairs": [["AAA", "BBB"]],
}


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_repo_config_loads_with_labels_and_pairs():
    u = load_universe()
    assert len(u.symbols) >= 5
    assert u.labels["^GSPC"] == "S&P 500"
    # Every configured pair references tracked symbols.
    for a, b in u.pairs:
        assert a in u.symbols and b in u.symbols and a != b


def test_happy_path_round_trip(tmp_path):
    u = load_universe(_write(tmp_path, "valid.json", VALID))
    assert u.symbols == ("AAA", "BBB")
    assert u.labels["AAA"] == "Asset A"
    assert u.pairs == (("AAA", "BBB"),)


def test_missing_file_fails_fast(tmp_path):
    with pytest.raises(UniverseError, match="not found"):
        load_universe(str(tmp_path / "nope.json"))


def test_pair_referencing_unknown_symbol_fails_fast(tmp_path):
    payload = {
        "symbols": [{"symbol": "AAA", "label": "A"}],
        "correlation_pairs": [["AAA", "NOT-TRACKED"]],
    }
    with pytest.raises(UniverseError, match="NOT-TRACKED"):
        load_universe(_write(tmp_path, "u.json", payload))


def test_duplicate_symbol_fails_fast(tmp_path):
    payload = {
        "symbols": [
            {"symbol": "AAA", "label": "A"},
            {"symbol": "AAA", "label": "A again"},
        ],
    }
    with pytest.raises(UniverseError, match="duplicate symbol"):
        load_universe(_write(tmp_path, "u.json", payload))


def test_self_pair_fails_fast(tmp_path):
    payload = {
        "symbols": [{"symbol": "AAA", "label": "A"}],
        "correlation_pairs": [["AAA", "AAA"]],
    }
    with pytest.raises(UniverseError, match="same symbol twice"):
        load_universe(_write(tmp_path, "u.json", payload))


def test_empty_symbols_fails_fast(tmp_path):
    with pytest.raises(UniverseError, match="non-empty"):
        load_universe(_write(tmp_path, "empty.json", {"symbols": []}))


def test_malformed_entry_fails_fast(tmp_path):
    with pytest.raises(UniverseError, match="malformed symbol entry"):
        load_universe(_write(tmp_path, "malformed.json", {"symbols": ["AAA", "BBB"]}))


def test_missing_json_fails_fast(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(UniverseError, match="not valid JSON"):
        load_universe(str(path))


def test_adding_a_symbol_requires_only_config_edit(tmp_path):
    """The point of the config file: no code change to extend the universe."""
    cfg_one = {
        "symbols": [{"symbol": "AAA", "label": "A"}],
        "correlation_pairs": [],
    }
    u1 = load_universe(_write(tmp_path, "u1.json", cfg_one))
    assert u1.symbols == ("AAA",)
    assert u1.pairs == ()

    cfg_two = {
        "symbols": [
            {"symbol": "AAA", "label": "A"},
            {"symbol": "BBB", "label": "B"},
        ],
        "correlation_pairs": [["AAA", "BBB"]],
    }
    u2 = load_universe(_write(tmp_path, "u2.json", cfg_two))
    assert u2.symbols == ("AAA", "BBB")
    assert u2.pairs == (("AAA", "BBB"),)
    assert isinstance(u2, Universe)
