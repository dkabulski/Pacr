"""Tests for multi-sport analysis."""

from __future__ import annotations

import json
from pathlib import Path

from coach_utils.analyze import _analyze_activity


def test_run_dispatch(tmp_data_dir: Path) -> None:
    """Run activity dispatches to running analyser."""
    activity = {
        "id": 1,
        "type": "Run",
        "date": "2025-01-15T07:30:00Z",
        "distance_km": 10.0,
        "distance_m": 10000,
        "moving_time_s": 3000,
        "pace": "5:00",
    }
    result = _analyze_activity(activity)
    assert result["sport"] == "run"


def test_ride_dispatch(tmp_data_dir: Path) -> None:
    """Ride activity dispatches to cycling analyser."""
    activity = {
        "id": 2,
        "type": "Ride",
        "date": "2025-01-15T07:30:00Z",
        "distance_km": 40.0,
        "distance_m": 40000,
        "moving_time_s": 5400,
        "elevation_m": 300,
    }
    result = _analyze_activity(activity)
    assert result["sport"] == "ride"
    assert "speed_kmh" in result


def test_hike_dispatch(tmp_data_dir: Path) -> None:
    """Hike activity dispatches to hiking analyser."""
    activity = {
        "id": 3,
        "type": "Hike",
        "date": "2025-01-15T07:30:00Z",
        "distance_km": 12.0,
        "distance_m": 12000,
        "moving_time_s": 7200,
        "elapsed_time_s": 9000,
        "elevation_m": 600,
    }
    result = _analyze_activity(activity)
    assert result["sport"] == "hike"
    assert result["elevation_per_km"] == 50.0
    assert result["rest_ratio"] > 0


def test_swim_dispatch(tmp_data_dir: Path) -> None:
    """Swim activity dispatches to swimming analyser."""
    activity = {
        "id": 4,
        "type": "Swim",
        "date": "2025-01-15T07:30:00Z",
        "distance_km": 2.0,
        "distance_m": 2000,
        "moving_time_s": 2400,
    }
    result = _analyze_activity(activity)
    assert result["sport"] == "swim"
    assert "pace_per_100m" in result
    # 2400s / (2000m / 100m) = 120s per 100m = 2:00
    assert result["pace_per_100m"] == "2:00"


def test_ride_with_power_zones(tmp_data_dir: Path, sample_zones: dict) -> None:
    """Ride with cycling power zones configured shows flag."""
    zones = sample_zones.copy()
    zones["cycling"] = {
        "ftp": 250,
        "power_zones": {
            "recovery": [0, 137],
            "endurance": [138, 187],
            "tempo": [188, 225],
            "threshold": [226, 262],
        },
    }
    (tmp_data_dir / "athlete_zones.json").write_text(json.dumps(zones))
    activity = {
        "id": 5,
        "type": "Ride",
        "date": "2025-01-15T07:30:00Z",
        "distance_km": 50.0,
        "distance_m": 50000,
        "moving_time_s": 7200,
        "avg_hr": 150,
    }
    result = _analyze_activity(activity)
    assert result["sport"] == "ride"
    assert result.get("power_zones_configured") is True


def test_swim_pace_per_100m(tmp_data_dir: Path) -> None:
    """Swim pace per 100m calculated correctly."""
    activity = {
        "id": 6,
        "type": "Swim",
        "date": "2025-01-15T07:30:00Z",
        "distance_km": 1.5,
        "distance_m": 1500,
        "moving_time_s": 1800,
    }
    result = _analyze_activity(activity)
    # 1800 / (1500/100) = 120s = 2:00/100m
    assert result["pace_per_100m_s"] == 120.0
    assert result["pace_per_100m"] == "2:00"
