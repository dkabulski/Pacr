"""Personal records and milestone detection."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("pacr")


def _records_path() -> Path:
    import _token_utils

    return _token_utils.DATA_DIR / "records.json"


def load_records() -> dict:
    """Load saved personal records from disk."""
    path = _records_path()
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def save_records(records: dict) -> None:
    """Persist personal records to disk."""
    import _token_utils

    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_records_path(), "w") as f:
        json.dump(records, f, indent=2)


def _time_for_distance(
    activity: dict, target_km: float, tolerance: float = 0.05
) -> float | None:
    """Extract time in seconds if activity distance is close to target_km.

    Uses tolerance (default 5%) to account for GPS drift.
    """
    dist = activity.get("distance_km", 0)
    if dist <= 0:
        return None
    if abs(dist - target_km) / target_km > tolerance:
        return None
    return activity.get("moving_time_s")


def scan_for_records(activities: list[dict]) -> dict:
    """Full rescan of all activities to compute personal records.

    Categories:
    - fastest_5k, fastest_10k, fastest_half, fastest_marathon:
      Race-flagged only (workout_type == 1). Time in seconds.
    - longest_run: Biggest single-run distance
    - biggest_week: Most km in a calendar week
    - biggest_month: Most km in a calendar month
    - longest_streak: Most consecutive days with an activity
    """
    records: dict = {}

    # Race distance records (workout_type == 1 only)
    race_distances = {
        "fastest_5k": 5.0,
        "fastest_10k": 10.0,
        "fastest_half": 21.0975,
        "fastest_marathon": 42.195,
    }

    for key, target_km in race_distances.items():
        best_time: float | None = None
        best_activity: dict | None = None
        for act in activities:
            if act.get("workout_type") != 1:
                continue
            t = _time_for_distance(act, target_km)
            if t is not None and (best_time is None or t < best_time):
                best_time = t
                best_activity = act
        if best_time is not None and best_activity is not None:
            mins = int(best_time // 60)
            secs = int(best_time % 60)
            records[key] = {
                "time_s": best_time,
                "time_str": f"{mins}:{secs:02d}",
                "date": best_activity.get("date", "")[:10],
                "activity_id": best_activity.get("id"),
            }

    # Longest run
    best_dist = 0.0
    best_dist_act: dict | None = None
    for act in activities:
        dist = act.get("distance_km", 0)
        if dist > best_dist:
            best_dist = dist
            best_dist_act = act
    if best_dist > 0 and best_dist_act is not None:
        records["longest_run"] = {
            "distance_km": round(best_dist, 2),
            "date": best_dist_act.get("date", "")[:10],
            "activity_id": best_dist_act.get("id"),
        }

    # Biggest week
    weeks: dict[str, float] = defaultdict(float)
    for act in activities:
        date_str = act.get("date", "")[:10]
        try:
            d = date.fromisoformat(date_str)
        except (ValueError, AttributeError):
            continue
        iso = d.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        weeks[week_key] += act.get("distance_km", 0)
    if weeks:
        best_week = max(weeks.items(), key=lambda x: x[1])
        records["biggest_week"] = {
            "week": best_week[0],
            "distance_km": round(best_week[1], 2),
        }

    # Biggest month
    months: dict[str, float] = defaultdict(float)
    for act in activities:
        date_str = act.get("date", "")[:10]
        if len(date_str) >= 7:
            months[date_str[:7]] += act.get("distance_km", 0)
    if months:
        best_month = max(months.items(), key=lambda x: x[1])
        records["biggest_month"] = {
            "month": best_month[0],
            "distance_km": round(best_month[1], 2),
        }

    # Longest streak (consecutive days with activity)
    activity_dates: set[date] = set()
    for act in activities:
        date_str = act.get("date", "")[:10]
        try:
            activity_dates.add(date.fromisoformat(date_str))
        except (ValueError, AttributeError):
            continue

    if activity_dates:
        sorted_dates = sorted(activity_dates)
        best_streak = 1
        current_streak = 1
        for i in range(1, len(sorted_dates)):
            if sorted_dates[i] - sorted_dates[i - 1] == timedelta(days=1):
                current_streak += 1
                best_streak = max(best_streak, current_streak)
            else:
                current_streak = 1
        records["longest_streak"] = {"days": best_streak}

    return records


def check_new_records(activities: list[dict]) -> list[dict]:
    """Incremental check: rescan, compare with saved, save if improved.

    Returns list of new PB dicts with keys: category, old, new.
    """
    old_records = load_records()
    new_records = scan_for_records(activities)

    improvements: list[dict] = []

    for key, new_val in new_records.items():
        old_val = old_records.get(key)
        improved = False

        if old_val is None:
            improved = True
        elif "time_s" in new_val:
            improved = new_val["time_s"] < old_val.get("time_s", float("inf"))
        elif "distance_km" in new_val:
            improved = new_val["distance_km"] > old_val.get("distance_km", 0)
        elif "days" in new_val:
            improved = new_val["days"] > old_val.get("days", 0)

        if improved:
            improvements.append(
                {
                    "category": key,
                    "old": old_val,
                    "new": new_val,
                }
            )

    if improvements:
        save_records(new_records)

    return improvements
