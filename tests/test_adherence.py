"""Tests for plan adherence scoring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _today_str() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


def _date_ago(days: int) -> str:
    return (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# is_close_enough
# ---------------------------------------------------------------------------


def test_is_close_enough_within() -> None:
    """10km prescribed, 9km actual (10% off) is within 20% tolerance."""
    from coach_utils.adherence import is_close_enough

    assert is_close_enough(10.0, 9.0) is True


def test_is_close_enough_outside() -> None:
    """10km prescribed, 7km actual (30% off) is outside 20% tolerance."""
    from coach_utils.adherence import is_close_enough

    assert is_close_enough(10.0, 7.0) is False


# ---------------------------------------------------------------------------
# find_activity_on_date
# ---------------------------------------------------------------------------


def test_find_activity_on_date() -> None:
    """Finds activity matching the given date."""
    from coach_utils.adherence import find_activity_on_date

    activities = [
        {"date": "2025-01-15T07:30:00Z", "distance_km": 10.0},
        {"date": "2025-01-16T08:00:00Z", "distance_km": 5.0},
    ]
    result = find_activity_on_date(activities, "2025-01-15")
    assert result is not None
    assert result["distance_km"] == 10.0

    assert find_activity_on_date(activities, "2025-01-20") is None


# ---------------------------------------------------------------------------
# calculate_adherence
# ---------------------------------------------------------------------------


def test_no_plan(tmp_data_dir: Path) -> None:
    """No plan file results in adherence 0%, no crash."""
    from coach_utils.adherence import calculate_adherence

    data = calculate_adherence(4)
    assert data["adherence_pct"] == 0.0
    assert data["completed"] == 0
    assert data["partial"] == 0
    assert data["missed"] == 0


def test_all_completed(tmp_data_dir: Path) -> None:
    """All sessions completed within tolerance gives 100%."""
    from coach_utils.adherence import calculate_adherence

    d1 = _date_ago(3)
    d2 = _date_ago(1)

    plan = {
        "goal": "Test",
        "weeks": [
            {
                "phase": "base",
                "sessions": [
                    {
                        "date": d1,
                        "type": "easy",
                        "description": "Easy 10km",
                        "distance_km": 10,
                    },
                    {
                        "date": d2,
                        "type": "tempo",
                        "description": "Tempo 5km",
                        "distance_km": 5,
                    },
                ],
            }
        ],
    }
    activities = [
        {"date": d1 + "T07:30:00Z", "distance_km": 10.0},
        {"date": d2 + "T08:00:00Z", "distance_km": 5.5},
    ]

    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    (tmp_data_dir / "activities.json").write_text(json.dumps(activities))

    data = calculate_adherence(4)
    assert data["adherence_pct"] == pytest.approx(100.0)
    assert data["completed"] == 2
    assert data["partial"] == 0
    assert data["missed"] == 0


def test_partial_match(tmp_data_dir: Path) -> None:
    """Activity exists but distance is off, counts as partial credit."""
    from coach_utils.adherence import calculate_adherence

    d1 = _date_ago(2)

    plan = {
        "goal": "Test",
        "weeks": [
            {
                "phase": "base",
                "sessions": [
                    {
                        "date": d1,
                        "type": "easy",
                        "description": "Easy 10km",
                        "distance_km": 10,
                    },
                ],
            }
        ],
    }
    activities = [
        {"date": d1 + "T07:30:00Z", "distance_km": 6.0},  # 40% off — outside tolerance
    ]

    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    (tmp_data_dir / "activities.json").write_text(json.dumps(activities))

    data = calculate_adherence(4)
    assert data["partial"] == 1
    assert data["completed"] == 0
    assert data["adherence_pct"] == pytest.approx(50.0)


def test_missed_session(tmp_data_dir: Path) -> None:
    """No activity on plan date counts as missed."""
    from coach_utils.adherence import calculate_adherence

    d1 = _date_ago(2)

    plan = {
        "goal": "Test",
        "weeks": [
            {
                "phase": "base",
                "sessions": [
                    {
                        "date": d1,
                        "type": "easy",
                        "description": "Easy 10km",
                        "distance_km": 10,
                    },
                ],
            }
        ],
    }
    activities: list[dict] = []

    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    (tmp_data_dir / "activities.json").write_text(json.dumps(activities))

    data = calculate_adherence(4)
    assert data["missed"] == 1
    assert data["completed"] == 0
    assert data["adherence_pct"] == pytest.approx(0.0)


def test_rest_day_honoured(tmp_data_dir: Path) -> None:
    """Rest day with no activity increments rest_days_honoured."""
    from coach_utils.adherence import calculate_adherence

    d1 = _date_ago(2)

    plan = {
        "goal": "Test",
        "weeks": [
            {
                "phase": "base",
                "sessions": [
                    {"date": d1, "type": "rest", "description": "Rest day"},
                ],
            }
        ],
    }
    activities: list[dict] = []

    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    (tmp_data_dir / "activities.json").write_text(json.dumps(activities))

    data = calculate_adherence(4)
    assert data["rest_days_honoured"] == 1
    assert data["rest_days_total"] == 1
    # Rest days excluded from the denominator
    assert data["completed"] == 0
    assert data["adherence_pct"] == pytest.approx(0.0)
