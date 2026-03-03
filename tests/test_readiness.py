"""Tests for race readiness assessment."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from coach_utils.readiness import assess_readiness


def test_no_plan_insufficient_data(tmp_data_dir: Path) -> None:
    """No plan and no goal -> insufficient_data."""
    (tmp_data_dir / "activities.json").write_text("[]")
    result = assess_readiness()
    assert result["overall"] == "insufficient_data"


def test_sufficient_volume(tmp_data_dir: Path) -> None:
    """Sufficient weekly volume for half marathon -> positive signal."""
    plan = {"goal": "half marathon in 1:30h", "weeks": []}
    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    # Create activities totalling ~60km over 4 weeks (15km/wk avg)
    acts = []
    for i in range(12):
        d = datetime.now(tz=UTC) - timedelta(days=i * 2)
        acts.append(
            {
                "id": i,
                "date": d.strftime("%Y-%m-%dT00:00:00Z"),
                "distance_km": 5.0,
                "moving_time_s": 1500,
                "type": "Run",
            }
        )
    (tmp_data_dir / "activities.json").write_text(json.dumps(acts))

    result = assess_readiness()
    # 60km / 4 weeks = 15km avg -- below 50km benchmark for HM
    assert result["volume_status"] == "low"
    assert result["goal"] == "half marathon in 1:30h"


def test_low_volume_negative(tmp_data_dir: Path) -> None:
    """Very low volume -> negative signal."""
    plan = {"goal": "10k in 45min", "weeks": []}
    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    # Only 10km total in 4 weeks
    acts = [
        {
            "id": 1,
            "date": datetime.now(tz=UTC).strftime("%Y-%m-%dT00:00:00Z"),
            "distance_km": 10.0,
            "moving_time_s": 3000,
            "type": "Run",
        }
    ]
    (tmp_data_dir / "activities.json").write_text(json.dumps(acts))

    result = assess_readiness()
    assert result["volume_status"] == "low"
    neg_signals = result["signals"]["negative"]
    assert any("volume" in s.lower() for s in neg_signals)


def test_with_goal_override(tmp_data_dir: Path) -> None:
    """Goal override takes precedence over plan goal."""
    plan = {"goal": "5k in 20min", "weeks": []}
    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    acts = [
        {
            "id": 1,
            "date": datetime.now(tz=UTC).strftime("%Y-%m-%dT00:00:00Z"),
            "distance_km": 10.0,
            "moving_time_s": 3000,
            "type": "Run",
        }
    ]
    (tmp_data_dir / "activities.json").write_text(json.dumps(acts))

    result = assess_readiness(goal="marathon in 3:30h")
    assert result["goal"] == "marathon in 3:30h"


def test_ctl_trend_computed(tmp_data_dir: Path) -> None:
    """CTL trend is computed when activities span enough time."""
    plan = {"goal": "10k in 40min", "weeks": []}
    (tmp_data_dir / "training_plan.json").write_text(json.dumps(plan))
    # Create activities spread across 60 days
    acts = []
    for i in range(30):
        d = datetime.now(tz=UTC) - timedelta(days=i * 2)
        acts.append(
            {
                "id": i,
                "date": d.strftime("%Y-%m-%dT00:00:00Z"),
                "distance_km": 8.0,
                "moving_time_s": 2400,
                "avg_hr": 150,
                "type": "Run",
            }
        )
    (tmp_data_dir / "activities.json").write_text(json.dumps(acts))

    result = assess_readiness()
    assert result["ctl"] > 0
    assert result["ctl_trend"] in ("rising", "falling", "stable")
