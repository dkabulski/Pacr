"""Tests for analyze.py."""

from __future__ import annotations

import json
from pathlib import Path

import analyze


def test_classify_hr_zone_recovery(sample_zones: dict) -> None:
    """HR 120 is in zone 1 (Recovery)."""
    result = analyze.classify_hr_zone(120, sample_zones["hr_zones"])
    assert result["zone"] == "zone1"
    assert result["label"] == "Recovery"
    assert result["in_range"] is True


def test_classify_hr_zone_easy(sample_zones: dict) -> None:
    """HR 140 is in zone 2 (Easy Aerobic)."""
    result = analyze.classify_hr_zone(140, sample_zones["hr_zones"])
    assert result["zone"] == "zone2"
    assert result["label"] == "Easy Aerobic"
    assert result["in_range"] is True


def test_classify_hr_zone_tempo(sample_zones: dict) -> None:
    """HR 155 is in zone 3 (Tempo)."""
    result = analyze.classify_hr_zone(155, sample_zones["hr_zones"])
    assert result["zone"] == "zone3"
    assert result["label"] == "Tempo"
    assert result["in_range"] is True


def test_classify_hr_zone_above(sample_zones: dict) -> None:
    """HR 210 is above zone 5."""
    result = analyze.classify_hr_zone(210, sample_zones["hr_zones"])
    assert result["zone"] == "above_zone5"
    assert result["in_range"] is False


def test_no_zones_file(tmp_data_dir: Path, sample_activities: list[dict]) -> None:
    """No zones file produces a clear flag, not a silent fallback."""
    # Write activities but no zones
    activities_path = tmp_data_dir / "activities.json"
    activities_path.write_text(json.dumps(sample_activities))

    result = analyze._analyze_activity(sample_activities[0])
    flags = result.get("flags", [])
    assert any("No zones configured" in f for f in flags)


def test_easy_run_in_zone(
    tmp_data_dir: Path,
    sample_activities: list[dict],
    sample_zones: dict,
    sample_plan: dict,
) -> None:
    """Easy run with HR in zone 2 gets no flags about being too fast."""
    (tmp_data_dir / "activities.json").write_text(json.dumps(sample_activities))
    (tmp_data_dir / "athlete_zones.json").write_text(json.dumps(sample_zones))
    (tmp_data_dir / "training_plan.json").write_text(json.dumps(sample_plan))

    # Activity date matches plan's easy session and HR 145 is in zone 2
    activity = sample_activities[0].copy()
    activity["date"] = "2025-01-15T07:30:00Z"
    activity["avg_hr"] = 140.0  # Zone 2 — appropriate for easy

    result = analyze._analyze_activity(activity)
    too_hard_flags = [f for f in result.get("flags", []) if "too hard" in f]
    assert len(too_hard_flags) == 0


def test_easy_run_too_fast(
    tmp_data_dir: Path,
    sample_activities: list[dict],
    sample_zones: dict,
    sample_plan: dict,
) -> None:
    """Easy run with HR in zone 4 gets flagged."""
    (tmp_data_dir / "activities.json").write_text(json.dumps(sample_activities))
    (tmp_data_dir / "athlete_zones.json").write_text(json.dumps(sample_zones))
    (tmp_data_dir / "training_plan.json").write_text(json.dumps(sample_plan))

    activity = sample_activities[0].copy()
    activity["date"] = "2025-01-15T07:30:00Z"
    activity["avg_hr"] = 170.0  # Zone 4 — too hard for easy

    result = analyze._analyze_activity(activity)
    too_hard_flags = [f for f in result.get("flags", []) if "too hard" in f]
    assert len(too_hard_flags) > 0


def test_no_heartrate(tmp_data_dir: Path, sample_activities: list[dict]) -> None:
    """Activity without HR still produces a pace-based analysis."""
    (tmp_data_dir / "activities.json").write_text(json.dumps(sample_activities))

    activity = sample_activities[0].copy()
    activity["avg_hr"] = None
    activity["max_hr"] = None

    result = analyze._analyze_activity(activity)
    assert result["pace"] == "5:00"
    assert "hr_zone" not in result or result.get("hr_zone", {}).get("zone") == "unknown"
