"""Goal progress and race readiness assessment."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("pacr")

# Volume benchmarks by race distance (weekly km needed)
_VOLUME_BENCHMARKS: dict[str, float] = {
    "5k": 30.0,
    "10k": 40.0,
    "half marathon": 50.0,
    "marathon": 65.0,
}

# Long run benchmarks (proportion of race distance)
_LONG_RUN_PCT = 0.75


def assess_readiness(goal: str | None = None) -> dict:
    """Comprehensive race readiness assessment.

    Pulls from: training plan, activities, zones, records, training load.

    Args:
        goal: Optional goal override string (e.g. "half marathon in 1:21h").
              If None, reads from current training plan.

    Returns dict with keys:
        goal, vdot, predicted_time, goal_time, vdot_gap,
        weekly_avg_km, volume_status, longest_recent_run_km,
        long_run_status, ctl, ctl_trend,
        signals (positive, negative, neutral), overall
    """
    from coach_utils import plan as plan_mod
    from coach_utils.training_load import calculate_load_metrics
    from strava_utils import strava_sync

    result: dict = {
        "goal": None,
        "vdot": None,
        "predicted_time": None,
        "goal_time": None,
        "vdot_gap": None,
        "weekly_avg_km": 0.0,
        "volume_status": "unknown",
        "longest_recent_run_km": 0.0,
        "long_run_status": "unknown",
        "ctl": 0.0,
        "ctl_trend": "unknown",
        "signals": {"positive": [], "negative": [], "neutral": []},
        "overall": "insufficient_data",
    }

    # Determine goal
    p = plan_mod._load_plan()
    if goal:
        result["goal"] = goal
    elif p:
        result["goal"] = p.get("goal", "")

    if not result["goal"]:
        return result

    # Load activities
    activities = strava_sync._load_cached()
    if not activities:
        return result

    # Parse goal distance for benchmarks
    goal_lower = (result["goal"] or "").lower()
    race_distance_km: float | None = None
    race_label: str = ""
    distance_map = {
        "marathon": 42.195,
        "half marathon": 21.0975,
        "half-marathon": 21.0975,
        "10k": 10.0,
        "10km": 10.0,
        "5k": 5.0,
        "5km": 5.0,
    }
    for name, km in sorted(distance_map.items(), key=lambda x: -len(x[0])):
        if name in goal_lower:
            race_distance_km = km
            race_label = name
            break

    # VDOT from race results
    try:
        from strava_utils import pot10
        from tgbot.context import _calculate_vdot

        race_results = pot10._load_results()
        dist_map = {
            "5k": 5.0,
            "5km": 5.0,
            "10k": 10.0,
            "10km": 10.0,
            "hm": 21.0975,
            "half marathon": 21.0975,
            "marathon": 42.195,
        }
        best_vdot: float | None = None
        for r in race_results or []:
            event = r.get("event", "").lower()
            time_str = r.get("time", "")
            dist_km = next((v for k, v in dist_map.items() if k in event), None)
            if dist_km and time_str:
                parts = time_str.split(":")
                try:
                    if len(parts) == 2:
                        time_s = int(parts[0]) * 60 + float(parts[1])
                    elif len(parts) == 3:
                        time_s = (
                            int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                        )
                    else:
                        continue
                    v = _calculate_vdot(dist_km, time_s)
                    if v and (best_vdot is None or v > best_vdot):
                        best_vdot = v
                except (ValueError, IndexError):
                    pass
        if best_vdot:
            result["vdot"] = best_vdot
            result["signals"]["positive"].append(f"VDOT {best_vdot}")
    except Exception:
        pass

    # Weekly average km (last 4 weeks)
    cutoff_28d = (datetime.now(tz=UTC) - timedelta(days=28)).strftime("%Y-%m-%d")
    recent = [a for a in activities if a.get("date", "")[:10] >= cutoff_28d]
    total_km = sum(a.get("distance_km", 0) for a in recent)
    weekly_avg = total_km / 4.0
    result["weekly_avg_km"] = round(weekly_avg, 1)

    # Volume status
    if race_label:
        benchmark_key = race_label if race_label in _VOLUME_BENCHMARKS else None
        if not benchmark_key:
            # Try normalising
            for k in _VOLUME_BENCHMARKS:
                if k in goal_lower:
                    benchmark_key = k
                    break
        if benchmark_key:
            benchmark = _VOLUME_BENCHMARKS[benchmark_key]
            if weekly_avg >= benchmark:
                result["volume_status"] = "sufficient"
                result["signals"]["positive"].append(
                    f"Weekly volume {weekly_avg:.0f} km (target {benchmark:.0f} km)"
                )
            elif weekly_avg >= benchmark * 0.75:
                result["volume_status"] = "building"
                result["signals"]["neutral"].append(
                    f"Weekly volume {weekly_avg:.0f} km (target {benchmark:.0f} km)"
                )
            else:
                result["volume_status"] = "low"
                result["signals"]["negative"].append(
                    f"Weekly volume {weekly_avg:.0f} km (target {benchmark:.0f} km)"
                )

    # Longest recent run (last 4 weeks)
    longest = max((a.get("distance_km", 0) for a in recent), default=0)
    result["longest_recent_run_km"] = round(longest, 1)

    if race_distance_km and race_distance_km > 0:
        target_long = race_distance_km * _LONG_RUN_PCT
        if longest >= target_long:
            result["long_run_status"] = "sufficient"
            result["signals"]["positive"].append(
                f"Long run {longest:.1f} km (target {target_long:.1f} km)"
            )
        else:
            result["long_run_status"] = "short"
            result["signals"]["negative"].append(
                f"Long run {longest:.1f} km (needs {target_long:.1f} km)"
            )

    # CTL trend (compare today vs 28 days ago)
    load_now = calculate_load_metrics(activities)
    result["ctl"] = load_now["ctl"]

    # Calculate CTL from 28 days ago by excluding recent activities
    cutoff_date = (datetime.now(tz=UTC) - timedelta(days=28)).strftime("%Y-%m-%d")
    older_activities = [a for a in activities if a.get("date", "")[:10] < cutoff_date]
    load_then = calculate_load_metrics(older_activities)

    if load_then["ctl"] > 0:
        ctl_change = load_now["ctl"] - load_then["ctl"]
        if ctl_change > 2:
            result["ctl_trend"] = "rising"
            result["signals"]["positive"].append(
                f"CTL rising ({load_then['ctl']:.0f} -> {load_now['ctl']:.0f})"
            )
        elif ctl_change < -2:
            result["ctl_trend"] = "falling"
            result["signals"]["negative"].append(
                f"CTL falling ({load_then['ctl']:.0f} -> {load_now['ctl']:.0f})"
            )
        else:
            result["ctl_trend"] = "stable"
            result["signals"]["neutral"].append(f"CTL stable at {load_now['ctl']:.0f}")

    # Overall rating
    pos = len(result["signals"]["positive"])
    neg = len(result["signals"]["negative"])

    if pos >= 3 and neg == 0:
        result["overall"] = "race_ready"
    elif pos >= 2 and neg <= 1:
        result["overall"] = "on_track"
    elif neg >= 2:
        result["overall"] = "needs_work"
    elif pos == 0 and neg == 0:
        result["overall"] = "insufficient_data"
    else:
        result["overall"] = "building"

    return result
