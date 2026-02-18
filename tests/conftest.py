"""Shared test fixtures for running-coach."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary data directory and monkeypatch _token_utils.DATA_DIR."""
    import _token_utils

    monkeypatch.setattr(_token_utils, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture()
def sample_strava_activity() -> dict:
    """Raw Strava API activity response."""
    return {
        "id": 12345678,
        "name": "Morning Run",
        "type": "Run",
        "sport_type": "Run",
        "start_date_local": "2025-01-15T07:30:00Z",
        "distance": 10000.0,
        "moving_time": 3000,
        "elapsed_time": 3100,
        "total_elevation_gain": 45.0,
        "average_heartrate": 145.0,
        "max_heartrate": 165.0,
        "average_cadence": 170.0,
        "suffer_score": 75,
        "calories": 650,
    }


@pytest.fixture()
def sample_activities() -> list[dict]:
    """List of normalised activity dicts."""
    return [
        {
            "id": 12345678,
            "name": "Morning Run",
            "type": "Run",
            "sport_type": "Run",
            "date": "2025-01-15T07:30:00Z",
            "distance_m": 10000.0,
            "distance_km": 10.0,
            "moving_time_s": 3000,
            "elapsed_time_s": 3100,
            "pace": "5:00",
            "elevation_m": 45.0,
            "avg_hr": 145.0,
            "max_hr": 165.0,
            "avg_cadence": 170.0,
            "suffer_score": 75,
            "calories": 650,
        },
        {
            "id": 12345679,
            "name": "Easy Jog",
            "type": "Run",
            "sport_type": "Run",
            "date": "2025-01-14T18:00:00Z",
            "distance_m": 5000.0,
            "distance_km": 5.0,
            "moving_time_s": 1650,
            "elapsed_time_s": 1700,
            "pace": "5:30",
            "elevation_m": 20.0,
            "avg_hr": 130.0,
            "max_hr": 145.0,
            "avg_cadence": 168.0,
            "suffer_score": 30,
            "calories": 320,
        },
    ]


@pytest.fixture()
def sample_zones() -> dict:
    """Athlete zones configuration."""
    return {
        "hr_zones": {
            "zone1": [100, 130],
            "zone2": [131, 145],
            "zone3": [146, 160],
            "zone4": [161, 175],
            "zone5": [176, 200],
        },
        "pace_zones": {
            "easy": [300, 360],
            "tempo": [255, 299],
            "threshold": [240, 254],
            "interval": [210, 239],
            "repetition": [180, 209],
        },
    }


@pytest.fixture()
def sample_plan() -> dict:
    """Training plan with one week."""
    return {
        "goal": "Sub-45 10K",
        "weeks": [
            {
                "week_number": 1,
                "phase": "base",
                "sessions": [
                    {
                        "date": "2025-01-15",
                        "type": "easy",
                        "description": "Easy 8km @ 5:30-5:50/km",
                        "distance_km": 8,
                    },
                    {
                        "date": "2025-01-16",
                        "type": "rest",
                        "description": "Rest day",
                    },
                    {
                        "date": "2025-01-17",
                        "type": "tempo",
                        "description": "Tempo 5km @ 4:40/km",
                        "distance_km": 5,
                    },
                ],
            }
        ],
    }


@pytest.fixture()
def sample_po10_html() -> str:
    """Minimal HTML mimicking Power of 10 results page."""
    return """
    <html>
    <body>
    <table id="cphBody_pnlPerformances">
        <tr>
            <th>Event</th>
            <th>Perf</th>
            <th>Date</th>
            <th>Pos</th>
            <th>Venue</th>
        </tr>
        <tr>
            <td>5K</td>
            <td>17:30</td>
            <td>15 Jun 24</td>
            <td>3</td>
            <td>Battersea Park</td>
        </tr>
        <tr>
            <td>10K</td>
            <td>36:45</td>
            <td>01 Apr 24</td>
            <td>12</td>
            <td>London</td>
        </tr>
        <tr>
            <td>HM</td>
            <td>1:22:00</td>
            <td>10 Mar 24</td>
            <td>25</td>
            <td>Reading</td>
        </tr>
    </table>
    </body>
    </html>
    """
