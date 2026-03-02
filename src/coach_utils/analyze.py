# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "fire>=0.7",
# ]
# ///
"""Session analysis — rate activities against plan and zones."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fire

import _token_utils


def _activities_path() -> Path:
    return _token_utils.DATA_DIR / "activities.json"


def _zones_path() -> Path:
    return _token_utils.DATA_DIR / "athlete_zones.json"


def _plan_path() -> Path:
    return _token_utils.DATA_DIR / "training_plan.json"


def _log_path() -> Path:
    return _token_utils.DATA_DIR / "training_log.json"


def _load_json(path: Path) -> dict | list | None:
    """Load JSON from a file, return None if missing."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _save_log(entry: dict) -> None:
    """Append analysis entry to the training log."""
    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []
    if _log_path().exists():
        with open(_log_path()) as f:
            log = json.load(f)
    log.append(entry)
    with open(_log_path(), "w") as f:
        json.dump(log, f, indent=2)


def classify_hr_zone(hr: float, zones: dict) -> dict:
    """Classify a heart rate value into a training zone.

    Zones format: {"zone1": [min, max], "zone2": [min, max], ...}
    Returns: {"zone": "zone2", "label": "Easy Aerobic", "in_range": true}
    """
    zone_labels = {
        "zone1": "Recovery",
        "zone2": "Easy Aerobic",
        "zone3": "Tempo",
        "zone4": "Threshold",
        "zone5": "VO2max",
    }

    for zone_name in ["zone1", "zone2", "zone3", "zone4", "zone5"]:
        if zone_name not in zones:
            continue
        bounds = zones[zone_name]
        if bounds[0] <= hr <= bounds[1]:
            return {
                "zone": zone_name,
                "label": zone_labels.get(zone_name, zone_name),
                "in_range": True,
            }

    # Above all zones
    if hr > zones.get("zone5", [0, 999])[1]:
        return {"zone": "above_zone5", "label": "Above VO2max", "in_range": False}

    # Below all zones
    return {"zone": "below_zone1", "label": "Below Recovery", "in_range": False}


def classify_pace_zone(pace_s_per_km: float, zones: dict) -> dict:
    """Classify a pace (seconds/km) into a training zone.

    Zones format: {"easy": [min_s, max_s], "tempo": [min_s, max_s], ...}
    Note: Lower pace = faster. Zone bounds are [fast, slow].
    Returns: {"zone": "easy", "in_range": true}
    """
    pace_zones = zones.get("pace_zones", {})
    for zone_name, bounds in pace_zones.items():
        if bounds[0] <= pace_s_per_km <= bounds[1]:
            return {"zone": zone_name, "in_range": True}

    return {"zone": "unknown", "in_range": False}


def _find_prescribed_session(plan: dict, activity_date: str) -> dict | None:
    """Try to match an activity date to a prescribed session in the plan."""
    if not plan or "weeks" not in plan:
        return None

    for week in plan["weeks"]:
        for session in week.get("sessions", []):
            if session.get("date") == activity_date[:10]:
                return session

    return None


def _analyze_activity(activity: dict) -> dict:
    """Analyse a single activity against zones and plan."""
    zones_data = _load_json(_zones_path())
    plan_data = _load_json(_plan_path())

    analysis: dict = {
        "activity_id": activity["id"],
        "name": activity.get("name", ""),
        "date": activity.get("date", ""),
        "distance_km": activity.get("distance_km", 0),
        "moving_time_s": activity.get("moving_time_s", 0),
        "pace": activity.get("pace", "N/A"),
        "flags": [],
        "recommendations": [],
    }

    # Heart rate analysis
    avg_hr = activity.get("avg_hr")
    if avg_hr and zones_data and "hr_zones" in zones_data:
        hr_zone = classify_hr_zone(avg_hr, zones_data["hr_zones"])
        analysis["hr_zone"] = hr_zone
        analysis["avg_hr"] = avg_hr
    elif avg_hr:
        analysis["avg_hr"] = avg_hr
        analysis["hr_zone"] = {
            "zone": "unknown",
            "label": "No zones configured",
            "in_range": False,
        }

    # Pace analysis
    distance_m = activity.get("distance_m", 0)
    moving_time = activity.get("moving_time_s", 0)
    if distance_m > 0 and moving_time > 0:
        pace_s_per_km = moving_time / (distance_m / 1000)
        analysis["pace_s_per_km"] = round(pace_s_per_km, 1)
        if zones_data and "pace_zones" in zones_data:
            pace_zone = classify_pace_zone(pace_s_per_km, zones_data)
            analysis["pace_zone"] = pace_zone

    # Compare against plan
    prescribed = _find_prescribed_session(plan_data, activity.get("date", ""))
    if prescribed:
        analysis["prescribed"] = prescribed
        prescribed_type = prescribed.get("type", "").lower()

        # Flag easy runs done too fast
        if prescribed_type in ("easy", "recovery"):
            hr_zone = analysis.get("hr_zone", {})
            zone = hr_zone.get("zone", "")
            if zone in ("zone3", "zone4", "zone5", "above_zone5"):
                analysis["flags"].append(
                    f"Easy run done in {hr_zone.get('label', zone)} — too hard"
                )
                analysis["recommendations"].append(
                    "Slow down on easy days. "
                    "Easy effort should feel comfortable and conversational."
                )
    else:
        analysis["prescribed"] = None

    # No zones warning
    if zones_data is None:
        analysis["flags"].append("No zones configured — analysis limited to pace only")
        analysis["recommendations"].append(
            "Set up athlete zones in data/athlete_zones.json for full analysis."
        )

    return analysis


def latest() -> None:
    """Analyse the most recent cached activity."""
    activities = _load_json(_activities_path())
    if not activities or not isinstance(activities, list) or len(activities) == 0:
        print("No activities cached. Run: uv run strava_sync.py sync")
        return

    activity = activities[0]  # Sorted newest first
    result = _analyze_activity(activity)
    _save_log(result)
    print(json.dumps(result, indent=2))


def activity(id: int) -> None:
    """Analyse a specific activity by ID."""
    activities = _load_json(_activities_path())
    if not activities or not isinstance(activities, list):
        print("No activities cached. Run: uv run strava_sync.py sync")
        return

    match = [a for a in activities if a["id"] == id]
    if not match:
        print(f"Activity {id} not found in cache.")
        return

    result = _analyze_activity(match[0])
    _save_log(result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    fire.Fire({"latest": latest, "activity": activity})
