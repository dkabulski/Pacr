"""CTL/ATL/TSB training load metrics from cached Strava activities."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta


def _estimate_tss(activity: dict, lthr: float | None = None) -> float:
    """Estimate Training Stress Score for a single activity.

    If avg_hr and lthr are available, uses IF = avg_hr / lthr.
    Otherwise falls back to distance_km * 6.0.
    Capped at 300.
    """
    hours = activity.get("moving_time_s", 0) / 3600.0
    avg_hr = activity.get("avg_hr")
    if avg_hr and lthr and lthr > 0:
        intensity_factor = avg_hr / lthr
        tss = hours * intensity_factor**2 * 100
        return min(tss, 300.0)
    dist_km = activity.get("distance_km", 0)
    return float(dist_km) * 6.0


def _get_lthr() -> float:
    """Read LTHR from athlete_zones.json zone4 lower bound; fallback 170.0."""
    try:
        import _token_utils

        zones_path = _token_utils.DATA_DIR / "athlete_zones.json"
        if zones_path.exists():
            zones = json.loads(zones_path.read_text())
            hr_zones = zones.get("hr_zones", {})
            zone4 = hr_zones.get("zone4")
            if zone4:
                return float(zone4[0])
    except Exception:
        pass
    return 170.0


def calculate_load_metrics(activities: list[dict], lthr: float | None = None) -> dict:
    """Calculate CTL, ATL, TSB using exponentially-weighted moving averages.

    CTL: 42-day constant (chronic training load).
    ATL: 7-day constant (acute training load).
    TSB: CTL - ATL (training stress balance).
    """
    if lthr is None:
        lthr = _get_lthr()

    k_ctl = 1 - math.exp(-1 / 42)
    k_atl = 1 - math.exp(-1 / 7)

    tss_by_date: dict[date, float] = {}
    for act in activities:
        date_str = act.get("date", "")[:10]
        try:
            d = date.fromisoformat(date_str)
        except (ValueError, AttributeError):
            continue
        tss = _estimate_tss(act, lthr)
        tss_by_date[d] = tss_by_date.get(d, 0.0) + tss

    today = datetime.now(tz=UTC).date()
    start = today - timedelta(days=364)

    ctl = 0.0
    atl = 0.0
    for i in range(365):
        d = start + timedelta(days=i)
        tss = tss_by_date.get(d, 0.0)
        ctl = ctl + k_ctl * (tss - ctl)
        atl = atl + k_atl * (tss - atl)

    return {"ctl": round(ctl, 1), "atl": round(atl, 1), "tsb": round(ctl - atl, 1)}


def weekly_km_trend(activities: list[dict], n_weeks: int = 12) -> list[dict]:
    """Return weekly km totals for the last n_weeks (oldest→newest).

    Fills ALL n_weeks slots including zero-km weeks.
    """
    today = datetime.now(tz=UTC).date()
    current_monday = today - timedelta(days=today.weekday())

    weeks: list[dict] = []
    for i in range(n_weeks - 1, -1, -1):
        week_start = current_monday - timedelta(weeks=i)
        iso = week_start.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        weeks.append({"week": week_key, "km": 0.0})

    week_index: dict[str, int] = {w["week"]: j for j, w in enumerate(weeks)}

    for act in activities:
        date_str = act.get("date", "")[:10]
        try:
            d = date.fromisoformat(date_str)
        except (ValueError, AttributeError):
            continue
        iso = d.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"
        if week_key in week_index:
            weeks[week_index[week_key]]["km"] += act.get("distance_km", 0.0)

    for w in weeks:
        w["km"] = round(w["km"], 1)

    return weeks


def volume_spike_check(activities: list[dict]) -> str | None:
    """Detect if this week's volume is >10% above last week's.

    Returns None if no baseline (last_week_km == 0) or if no spike detected.
    """
    today = datetime.now(tz=UTC).date()
    current_monday = today - timedelta(days=today.weekday())
    last_monday = current_monday - timedelta(weeks=1)

    this_week_km = 0.0
    last_week_km = 0.0

    for act in activities:
        date_str = act.get("date", "")[:10]
        try:
            d = date.fromisoformat(date_str)
        except (ValueError, AttributeError):
            continue
        if current_monday <= d <= today:
            this_week_km += act.get("distance_km", 0.0)
        elif last_monday <= d < current_monday:
            last_week_km += act.get("distance_km", 0.0)

    if last_week_km == 0.0:
        return None
    if this_week_km > last_week_km * 1.10:
        pct = (this_week_km / last_week_km - 1) * 100
        return (
            f"Volume spike: {this_week_km:.1f} km this week vs "
            f"{last_week_km:.1f} km last week (+{pct:.0f}%)."
        )
    return None
