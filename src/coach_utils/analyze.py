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


# Sport type mapping
_RUN_TYPES = {"Run", "VirtualRun", "TrailRun"}
_RIDE_TYPES = {"Ride", "VirtualRide", "MountainBikeRide", "EBikeRide", "GravelRide"}
_SWIM_TYPES = {"Swim"}
_HIKE_TYPES = {"Hike", "Walk"}


def _analyze_activity(activity: dict) -> dict:
    """Analyse a single activity — routes to sport-specific analyser."""
    sport = activity.get("type", "Run")
    zones_data = _load_json(_zones_path())
    plan_data = _load_json(_plan_path())

    if sport in _RIDE_TYPES:
        return _analyze_ride(activity, zones_data)
    elif sport in _SWIM_TYPES:
        return _analyze_swim(activity, zones_data)
    elif sport in _HIKE_TYPES:
        return _analyze_hike(activity, zones_data)
    else:
        return _analyze_run(activity, zones_data, plan_data)


def _analyze_run(
    activity: dict,
    zones_data: dict | list | None,
    plan_data: dict | list | None,
) -> dict:
    """Analyse a running activity against zones and plan."""
    analysis: dict = {
        "activity_id": activity["id"],
        "name": activity.get("name", ""),
        "date": activity.get("date", ""),
        "sport": "run",
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

        # Flag quality sessions done too easy
        if prescribed_type in ("tempo", "intervals", "race"):
            hr_zone = analysis.get("hr_zone", {})
            zone = hr_zone.get("zone", "")
            if zone in ("zone1", "zone2", "below_zone1"):
                analysis["flags"].append(
                    f"{prescribed_type.title()} session done in "
                    f"{hr_zone.get('label', zone)} — too easy"
                )
                analysis["recommendations"].append(
                    f"{prescribed_type.title()} sessions require sustained effort. "
                    "Half-hearted quality work is worse than rest."
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


def _analyze_ride(activity: dict, zones_data: dict | list | None) -> dict:
    """Analyse a cycling activity — power zones, speed, cadence."""
    distance_km = activity.get("distance_km", 0)
    moving_time_s = activity.get("moving_time_s", 0)
    speed_kmh = (distance_km / (moving_time_s / 3600)) if moving_time_s > 0 else 0

    analysis: dict = {
        "activity_id": activity["id"],
        "name": activity.get("name", ""),
        "date": activity.get("date", ""),
        "sport": "ride",
        "distance_km": distance_km,
        "moving_time_s": moving_time_s,
        "speed_kmh": round(speed_kmh, 1),
        "elevation_m": activity.get("elevation_m", 0),
        "avg_cadence": activity.get("avg_cadence"),
        "flags": [],
        "recommendations": [],
    }

    # HR analysis
    avg_hr = activity.get("avg_hr")
    if avg_hr and zones_data and "hr_zones" in zones_data:
        hr_zone = classify_hr_zone(avg_hr, zones_data["hr_zones"])
        analysis["hr_zone"] = hr_zone
        analysis["avg_hr"] = avg_hr

    # Power zones (if configured)
    if zones_data and "cycling" in zones_data:
        power_zones = zones_data["cycling"].get("power_zones", {})
        if power_zones:
            analysis["power_zones_configured"] = True

    return analysis


def _analyze_hike(activity: dict, zones_data: dict | list | None) -> dict:
    """Analyse a hiking activity — elevation gain/km, rest ratio."""
    distance_km = activity.get("distance_km", 0)
    elevation_m = activity.get("elevation_m", 0)
    moving_time_s = activity.get("moving_time_s", 0)
    elapsed_time_s = activity.get("elapsed_time_s", moving_time_s)
    elev_per_km = (elevation_m / distance_km) if distance_km > 0 else 0
    rest_ratio = (
        (elapsed_time_s - moving_time_s) / elapsed_time_s if elapsed_time_s > 0 else 0
    )

    analysis: dict = {
        "activity_id": activity["id"],
        "name": activity.get("name", ""),
        "date": activity.get("date", ""),
        "sport": "hike",
        "distance_km": distance_km,
        "moving_time_s": moving_time_s,
        "elevation_m": elevation_m,
        "elevation_per_km": round(elev_per_km, 1),
        "rest_ratio": round(rest_ratio, 3),
        "flags": [],
        "recommendations": [],
    }

    # HR analysis
    avg_hr = activity.get("avg_hr")
    if avg_hr and zones_data and "hr_zones" in zones_data:
        hr_zone = classify_hr_zone(avg_hr, zones_data["hr_zones"])
        analysis["hr_zone"] = hr_zone
        analysis["avg_hr"] = avg_hr

    return analysis


def _analyze_swim(activity: dict, zones_data: dict | list | None) -> dict:
    """Analyse a swimming activity — pace per 100m, CSS zones."""
    distance_m = activity.get("distance_m", 0)
    moving_time_s = activity.get("moving_time_s", 0)
    pace_per_100m = (moving_time_s / (distance_m / 100)) if distance_m > 0 else 0

    analysis: dict = {
        "activity_id": activity["id"],
        "name": activity.get("name", ""),
        "date": activity.get("date", ""),
        "sport": "swim",
        "distance_m": distance_m,
        "distance_km": activity.get("distance_km", 0),
        "moving_time_s": moving_time_s,
        "pace_per_100m_s": round(pace_per_100m, 1),
        "flags": [],
        "recommendations": [],
    }

    # Format pace per 100m as mm:ss
    if pace_per_100m > 0:
        m = int(pace_per_100m // 60)
        s = int(pace_per_100m % 60)
        analysis["pace_per_100m"] = f"{m}:{s:02d}"

    # HR analysis
    avg_hr = activity.get("avg_hr")
    if avg_hr and zones_data and "hr_zones" in zones_data:
        hr_zone = classify_hr_zone(avg_hr, zones_data["hr_zones"])
        analysis["hr_zone"] = hr_zone
        analysis["avg_hr"] = avg_hr

    # CSS zones (if configured)
    if zones_data and "swimming" in zones_data:
        pace_zones = zones_data["swimming"].get("pace_zones", {})
        if pace_zones:
            analysis["css_zones_configured"] = True

    return analysis


def analyse_splits(activity: dict) -> dict:
    """Analyse per-km splits for pacing patterns.

    Returns dict with keys: split_count, split_paces, mean_pace_s,
    cv (coefficient of variation), flags, lap_summary.
    """
    splits = activity.get("splits_metric", [])

    if not splits:
        return {
            "split_count": 0,
            "split_paces": [],
            "mean_pace_s": 0,
            "cv": 0,
            "flags": [],
            "lap_summary": [],
        }

    # Calculate pace in s/km for each split
    split_paces: list[float] = []
    for sp in splits:
        dist = sp.get("distance_m", 0)
        time_s = sp.get("moving_time_s", 0)
        if dist > 0 and time_s > 0:
            pace_s = time_s / (dist / 1000)
            split_paces.append(round(pace_s, 1))

    if not split_paces:
        return {
            "split_count": 0,
            "split_paces": [],
            "mean_pace_s": 0,
            "cv": 0,
            "flags": [],
            "lap_summary": [],
        }

    mean_pace = sum(split_paces) / len(split_paces)
    std_dev = (sum((p - mean_pace) ** 2 for p in split_paces) / len(split_paces)) ** 0.5
    cv = std_dev / mean_pace if mean_pace > 0 else 0

    flags: list[str] = []

    # Detect pacing patterns (need at least 3 splits)
    if len(split_paces) >= 3:
        first_half = split_paces[: len(split_paces) // 2]
        second_half = split_paces[len(split_paces) // 2 :]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        # Negative split: second half faster (lower pace) by >2%
        if avg_second < avg_first * 0.98:
            flags.append("negative_split")
        # Positive split: second half slower by >2%
        elif avg_second > avg_first * 1.02:
            flags.append("positive_split")

        # Fast start: first split >5% faster than mean
        if split_paces[0] < mean_pace * 0.95:
            flags.append("fast_start")

        # Fade: last 2 splits both >5% slower than mean
        if all(p > mean_pace * 1.05 for p in split_paces[-2:]):
            flags.append("fade")

    # Consistent pacing: CV < 3%
    if cv < 0.03:
        flags.append("consistent_pacing")

    # Lap summary
    lap_summary = []
    laps = activity.get("laps", [])
    for i, lap in enumerate(laps):
        lap_summary.append(
            {
                "lap": i + 1,
                "distance_m": lap.get("distance_m", 0),
                "pace": lap.get("pace", "N/A"),
                "avg_hr": lap.get("avg_hr"),
            }
        )

    return {
        "split_count": len(split_paces),
        "split_paces": split_paces,
        "mean_pace_s": round(mean_pace, 1),
        "cv": round(cv, 4),
        "flags": flags,
        "lap_summary": lap_summary,
    }


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
