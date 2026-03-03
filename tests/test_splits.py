"""Tests for split-level analysis."""

from __future__ import annotations

from coach_utils.analyze import analyse_splits


def _make_splits(paces_s_per_km: list[float]) -> list[dict]:
    """Helper: create splits_metric from pace values."""
    return [
        {"distance_m": 1000, "moving_time_s": p, "elapsed_time_s": p}
        for p in paces_s_per_km
    ]


def test_negative_split() -> None:
    """Second half faster -> negative_split flag."""
    # 6:00, 5:50, 5:40, 5:20, 5:00, 4:50 (getting faster)
    paces = [360, 350, 340, 320, 300, 290]
    act = {"splits_metric": _make_splits(paces), "laps": []}
    result = analyse_splits(act)
    assert "negative_split" in result["flags"]
    assert "positive_split" not in result["flags"]


def test_positive_split() -> None:
    """Second half slower -> positive_split flag."""
    # 4:50, 5:00, 5:10, 5:30, 5:50, 6:00 (getting slower)
    paces = [290, 300, 310, 330, 350, 360]
    act = {"splits_metric": _make_splits(paces), "laps": []}
    result = analyse_splits(act)
    assert "positive_split" in result["flags"]
    assert "negative_split" not in result["flags"]


def test_consistent_pacing() -> None:
    """Very even pacing -> consistent_pacing flag."""
    # All splits within 1% of each other
    paces = [300, 301, 299, 300, 301, 300]
    act = {"splits_metric": _make_splits(paces), "laps": []}
    result = analyse_splits(act)
    assert "consistent_pacing" in result["flags"]


def test_fast_start() -> None:
    """First split much faster -> fast_start flag."""
    # First km at 4:30, rest at ~5:30
    paces = [270, 330, 330, 330, 330, 330]
    act = {"splits_metric": _make_splits(paces), "laps": []}
    result = analyse_splits(act)
    assert "fast_start" in result["flags"]


def test_no_splits_graceful() -> None:
    """No splits data -> empty result, no crash."""
    act = {"splits_metric": [], "laps": []}
    result = analyse_splits(act)
    assert result["split_count"] == 0
    assert result["flags"] == []
