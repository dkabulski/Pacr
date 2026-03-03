"""Tests for records.py."""

from __future__ import annotations

import json
from pathlib import Path

from coach_utils import records


def test_empty_activities(tmp_data_dir: Path) -> None:
    """No activities -> empty records."""
    result = records.scan_for_records([])
    assert result == {}


def test_race_5k_detected(tmp_data_dir: Path) -> None:
    """Race-flagged 5K activity -> fastest_5k recorded."""
    acts = [
        {
            "id": 1,
            "date": "2025-01-15",
            "distance_km": 5.01,
            "moving_time_s": 1200,
            "workout_type": 1,
        }
    ]
    result = records.scan_for_records(acts)
    assert "fastest_5k" in result
    assert result["fastest_5k"]["time_s"] == 1200


def test_non_race_excluded(tmp_data_dir: Path) -> None:
    """Non-race activity (workout_type != 1) excluded from race records."""
    acts = [
        {
            "id": 1,
            "date": "2025-01-15",
            "distance_km": 5.0,
            "moving_time_s": 1200,
            "workout_type": 0,  # default run, not a race
        }
    ]
    result = records.scan_for_records(acts)
    assert "fastest_5k" not in result


def test_longest_run(tmp_data_dir: Path) -> None:
    """Longest run is tracked regardless of workout type."""
    acts = [
        {"id": 1, "date": "2025-01-15", "distance_km": 15.0, "moving_time_s": 5400},
        {"id": 2, "date": "2025-01-16", "distance_km": 25.5, "moving_time_s": 9000},
    ]
    result = records.scan_for_records(acts)
    assert result["longest_run"]["distance_km"] == 25.5


def test_biggest_week(tmp_data_dir: Path) -> None:
    """Biggest week sums all activities in the same ISO week."""
    acts = [
        {"id": 1, "date": "2025-01-13", "distance_km": 10.0},  # Monday
        {"id": 2, "date": "2025-01-15", "distance_km": 12.0},  # Wednesday
        {"id": 3, "date": "2025-01-20", "distance_km": 5.0},  # Next Monday
    ]
    result = records.scan_for_records(acts)
    assert result["biggest_week"]["distance_km"] == 22.0


def test_longest_streak(tmp_data_dir: Path) -> None:
    """Consecutive days counted correctly."""
    acts = [
        {"id": 1, "date": "2025-01-13", "distance_km": 5.0},
        {"id": 2, "date": "2025-01-14", "distance_km": 5.0},
        {"id": 3, "date": "2025-01-15", "distance_km": 5.0},
        {"id": 4, "date": "2025-01-17", "distance_km": 5.0},  # gap
    ]
    result = records.scan_for_records(acts)
    assert result["longest_streak"]["days"] == 3


def test_check_new_records_detects_improvement(tmp_data_dir: Path) -> None:
    """check_new_records detects when a new PB is set."""
    old = {
        "fastest_5k": {
            "time_s": 1300,
            "date": "2025-01-01",
            "activity_id": 0,
            "time_str": "21:40",
        }
    }
    (tmp_data_dir / "records.json").write_text(json.dumps(old))

    acts = [
        {
            "id": 1,
            "date": "2025-02-01",
            "distance_km": 5.0,
            "moving_time_s": 1200,
            "workout_type": 1,
        }
    ]
    improvements = records.check_new_records(acts)
    assert any(i["category"] == "fastest_5k" for i in improvements)


def test_check_new_records_no_improvement(tmp_data_dir: Path) -> None:
    """check_new_records returns empty when no improvement."""
    old = {
        "fastest_5k": {
            "time_s": 1100,
            "date": "2025-01-01",
            "activity_id": 0,
            "time_str": "18:20",
        }
    }
    (tmp_data_dir / "records.json").write_text(json.dumps(old))

    acts = [
        {
            "id": 1,
            "date": "2025-02-01",
            "distance_km": 5.0,
            "moving_time_s": 1200,
            "workout_type": 1,
        }
    ]
    improvements = records.check_new_records(acts)
    # The 5K is slower, so no improvement for that; but longest_run might appear as new
    assert not any(i["category"] == "fastest_5k" for i in improvements)


def test_load_records_missing_file(tmp_data_dir: Path) -> None:
    """load_records returns empty dict when no file exists."""
    result = records.load_records()
    assert result == {}
