"""Tests for strava_sync.py."""

from __future__ import annotations

import strava_sync


def test_format_pace_normal() -> None:
    """5000m in 1500s = 5:00/km."""
    assert strava_sync.format_pace(5000, 1500) == "5:00"


def test_format_pace_zero_distance() -> None:
    """Zero distance returns N/A."""
    assert strava_sync.format_pace(0, 1500) == "N/A"


def test_format_pace_negative_distance() -> None:
    """Negative distance returns N/A."""
    assert strava_sync.format_pace(-100, 1500) == "N/A"


def test_normalize_activity(sample_strava_activity: dict) -> None:
    """Raw Strava activity normalises correctly."""
    result = strava_sync.normalize_activity(sample_strava_activity)
    assert result["id"] == 12345678
    assert result["name"] == "Morning Run"
    assert result["distance_m"] == 10000.0
    assert result["distance_km"] == 10.0
    assert result["moving_time_s"] == 3000
    assert result["pace"] == "5:00"
    assert result["avg_hr"] == 145.0
    assert result["elevation_m"] == 45.0


def test_merge_dedup(sample_activities: list[dict]) -> None:
    """Same ID keeps the newer (second) data."""
    old = [{"id": 12345678, "name": "Old Run", "date": "2025-01-14T00:00:00Z"}]
    new = [{"id": 12345678, "name": "Updated Run", "date": "2025-01-15T00:00:00Z"}]
    merged = strava_sync._merge(old, new)
    assert len(merged) == 1
    assert merged[0]["name"] == "Updated Run"


def test_merge_new(sample_activities: list[dict]) -> None:
    """New activities are appended."""
    existing = [{"id": 1, "name": "Run A", "date": "2025-01-14T00:00:00Z"}]
    new = [{"id": 2, "name": "Run B", "date": "2025-01-15T00:00:00Z"}]
    merged = strava_sync._merge(existing, new)
    assert len(merged) == 2
