"""Plan adherence scoring — how closely the athlete follows their training plan."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import _token_utils

logger = logging.getLogger("pacr")


def is_close_enough(
    prescribed_km: float, actual_km: float, tolerance: float = 0.20
) -> bool:
    """Check if actual distance is within tolerance of prescribed distance.

    When no distance is prescribed (prescribed_km == 0), any activity counts
    as completed.  Rest-day compliance is handled separately by the caller.
    """
    if prescribed_km <= 0:
        return actual_km > 0
    return abs(actual_km - prescribed_km) / prescribed_km <= tolerance


def find_activity_on_date(activities: list[dict], target_date: str) -> dict | None:
    """Find an activity matching target_date (YYYY-MM-DD)."""
    for act in activities:
        if act.get("date", "")[:10] == target_date[:10]:
            return act
    return None


def calculate_adherence(n_weeks: int = 4) -> dict:
    """Calculate plan adherence over the last n_weeks.

    Returns dict with keys: adherence_pct, completed, partial, missed,
    rest_days_honoured, rest_days_total, details
    """
    plan_path = _token_utils.DATA_DIR / "training_plan.json"
    activities_path = _token_utils.DATA_DIR / "activities.json"

    if not plan_path.exists():
        return {
            "adherence_pct": 0.0,
            "completed": 0,
            "partial": 0,
            "missed": 0,
            "rest_days_honoured": 0,
            "rest_days_total": 0,
            "details": [],
        }

    with open(plan_path) as f:
        plan = json.load(f)

    if activities_path.exists():
        with open(activities_path) as f:
            activities: list[dict] = json.load(f)
    else:
        activities = []

    today = datetime.now(tz=UTC).date()
    window_start = today - timedelta(days=n_weeks * 7)

    completed = 0
    partial = 0
    missed = 0
    rest_days_honoured = 0
    rest_days_total = 0
    details: list[dict] = []

    for week in plan.get("weeks", []):
        for session in week.get("sessions", []):
            session_date = session.get("date", "")
            if not session_date:
                continue
            try:
                d = datetime.strptime(session_date[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if d < window_start or d > today:
                continue

            session_type = session.get("type", "").lower()
            prescribed_km = session.get("distance_km", 0) or 0

            # Rest day handling
            if session_type == "rest":
                rest_days_total += 1
                activity = find_activity_on_date(activities, session_date)
                if activity is None:
                    rest_days_honoured += 1
                    details.append(
                        {
                            "date": session_date[:10],
                            "status": "rest_honoured",
                        }
                    )
                else:
                    details.append(
                        {
                            "date": session_date[:10],
                            "status": "rest_violated",
                        }
                    )
                continue

            # Non-rest session
            activity = find_activity_on_date(activities, session_date)
            if activity is None:
                missed += 1
                details.append(
                    {
                        "date": session_date[:10],
                        "status": "missed",
                        "prescribed_km": prescribed_km,
                    }
                )
            else:
                actual_km = activity.get("distance_km", 0) or 0
                if is_close_enough(prescribed_km, actual_km):
                    completed += 1
                    details.append(
                        {
                            "date": session_date[:10],
                            "status": "completed",
                            "prescribed_km": prescribed_km,
                            "actual_km": actual_km,
                        }
                    )
                else:
                    partial += 1
                    details.append(
                        {
                            "date": session_date[:10],
                            "status": "partial",
                            "prescribed_km": prescribed_km,
                            "actual_km": actual_km,
                        }
                    )

    total_sessions = completed + partial + missed
    if total_sessions > 0:
        adherence_pct = (completed + 0.5 * partial) / total_sessions * 100
    else:
        adherence_pct = 0.0

    return {
        "adherence_pct": adherence_pct,
        "completed": completed,
        "partial": partial,
        "missed": missed,
        "rest_days_honoured": rest_days_honoured,
        "rest_days_total": rest_days_total,
        "details": details,
    }
