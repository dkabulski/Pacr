"""HTML formatters and data helpers for Telegram messages."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _today_session() -> dict | None:
    """Match today's date against the training plan."""
    import plan as plan_mod

    p = plan_mod._load_plan()
    if not p or "weeks" not in p:
        return None

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    for week in p["weeks"]:
        for session in week.get("sessions", []):
            if session.get("date") == today:
                return session
    return None


def _weekly_summary() -> dict:
    """Summarise the last 7 days of cached Strava activities.

    Returns:
        {"runs": int, "total_km": float, "total_time_s": int,
         "avg_pace": str, "activities": [...]}
    """
    import strava_sync

    activities = strava_sync._load_cached()
    cutoff = datetime.now(tz=UTC) - timedelta(days=7)

    recent: list[dict] = []
    for act in activities:
        date_str = act.get("date", "")
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt >= cutoff:
            recent.append(act)

    total_km = sum(a.get("distance_km", 0) for a in recent)
    total_time = sum(a.get("moving_time_s", 0) for a in recent)
    avg_pace = (
        strava_sync.format_pace(total_km * 1000, total_time) if total_km > 0 else "N/A"
    )

    return {
        "runs": len(recent),
        "total_km": round(total_km, 1),
        "total_time_s": total_time,
        "avg_pace": avg_pace,
        "activities": recent,
    }


# ---------------------------------------------------------------------------
# HTML formatters
# ---------------------------------------------------------------------------


def _format_activity_summary(activity: dict) -> str:
    """Format a single activity as an HTML summary."""
    name = activity.get("name", "Untitled")
    dist = activity.get("distance_km", 0)
    pace = activity.get("pace", "N/A")
    hr = activity.get("avg_hr")
    date = activity.get("date", "")[:10]

    lines = [f"<b>{name}</b>"]
    if date:
        lines.append(f"Date: {date}")
    lines.append(f"Distance: {dist:.1f} km")
    lines.append(f"Pace: {pace}/km")
    if hr:
        lines.append(f"Avg HR: {hr:.0f} bpm")
    return "\n".join(lines)


def _format_today_session(session: dict | None) -> str:
    """Format today's prescribed session or rest day message."""
    if session is None:
        return "No session prescribed today — rest day or no plan set."

    session_type = session.get("type", "unknown")
    desc = session.get("description", "")
    dist = session.get("distance_km")

    lines = [f"<b>Today: {session_type.title()}</b>"]
    if desc:
        lines.append(desc)
    if dist:
        lines.append(f"Distance: {dist} km")
    return "\n".join(lines)


def _format_plan_summary(plan: dict) -> str:
    """Format the training plan as an HTML overview."""
    goal = plan.get("goal", "No goal set")
    weeks = plan.get("weeks", [])
    total_weeks = len(weeks)

    lines = ["<b>Training Plan</b>", f"Goal: {goal}", f"Weeks: {total_weeks}"]

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    current_week = None
    for i, week in enumerate(weeks):
        for session in week.get("sessions", []):
            if session.get("date", "") >= today:
                current_week = (i, week)
                break
        if current_week:
            break

    if current_week:
        idx, week = current_week
        phase = week.get("phase", "")
        lines.append(f"\n<b>Week {idx + 1}</b>" + (f" ({phase})" if phase else ""))
        for session in week.get("sessions", []):
            date = session.get("date", "")
            stype = session.get("type", "")
            desc = session.get("description", "")
            lines.append(f"  {date} — {stype}: {desc}")

    return "\n".join(lines)


def _format_results(results: list[dict], limit: int = 10) -> str:
    """Format race results as an HTML table."""
    if not results:
        return "No race results cached."

    lines = ["<b>Race Results</b>"]
    for r in results[:limit]:
        date = r.get("date", "?")
        event = r.get("event", "?")
        time_ = r.get("time", "?")
        pos = r.get("position")
        pos_str = f" #{pos}" if pos else ""
        lines.append(f"  {date}  {event} — {time_}{pos_str}")

    if len(results) > limit:
        lines.append(f"  ... and {len(results) - limit} more")
    return "\n".join(lines)


def _format_weekly_summary(summary: dict) -> str:
    """Format weekly summary as HTML."""
    runs = summary.get("runs", 0)
    km = summary.get("total_km", 0)
    time_s = summary.get("total_time_s", 0)
    pace = summary.get("avg_pace", "N/A")

    hours = time_s // 3600
    mins = (time_s % 3600) // 60

    lines = [
        "<b>Weekly Summary (last 7 days)</b>",
        f"Runs: {runs}",
        f"Distance: {km:.1f} km",
        f"Time: {hours}h {mins:02d}m",
        f"Avg pace: {pace}/km",
    ]
    return "\n".join(lines)


def _format_week_vs_plan() -> str:
    """Format this week's planned sessions vs completed activities."""
    import plan as plan_mod
    import strava_sync

    p = plan_mod._load_plan()
    if not p:
        return "No training plan set."

    today = datetime.now(tz=UTC)
    today_str = today.strftime("%Y-%m-%d")
    iso = today.isocalendar()

    week_sessions = []
    for week in p.get("weeks", []):
        for session in week.get("sessions", []):
            date_str = session.get("date", "")
            try:
                s_iso = datetime.strptime(date_str, "%Y-%m-%d").isocalendar()
                if s_iso[0] == iso[0] and s_iso[1] == iso[1]:
                    week_sessions.append(session)
            except ValueError:
                continue

    if not week_sessions:
        return "No sessions planned for this week."

    done_dates: set[str] = set()
    for act in strava_sync._load_cached():
        date_str = act.get("date", "")[:10]
        try:
            s_iso = datetime.strptime(date_str, "%Y-%m-%d").isocalendar()
            if s_iso[0] == iso[0] and s_iso[1] == iso[1]:
                done_dates.add(date_str)
        except ValueError:
            continue

    lines = [f"<b>This Week (W{iso[1]})</b>"]
    for session in sorted(week_sessions, key=lambda s: s.get("date", "")):
        date = session.get("date", "")
        stype = session.get("type", "")
        desc = session.get("description", "")
        if date in done_dates:
            marker = "✓"
        elif date < today_str:
            marker = "✗"
        elif date == today_str:
            marker = "→"
        else:
            marker = "·"
        lines.append(f"{marker} {date} — {stype}: {desc}")
    return "\n".join(lines)


def _format_next_sessions(n: int = 5) -> str:
    """Format the next N upcoming sessions from the plan."""
    import plan as plan_mod

    p = plan_mod._load_plan()
    if not p:
        return "No training plan set."

    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    upcoming = sorted(
        (s for w in p.get("weeks", []) for s in w.get("sessions", [])
         if s.get("date", "") >= today_str),
        key=lambda s: s.get("date", ""),
    )[:n]

    if not upcoming:
        return "No upcoming sessions in the plan."

    lines = ["<b>Upcoming Sessions</b>"]
    for session in upcoming:
        date = session.get("date", "")
        stype = session.get("type", "")
        desc = session.get("description", "")
        marker = "→" if date == today_str else "·"
        lines.append(f"{marker} {date} — {stype}: {desc}")
    return "\n".join(lines)


def _format_last_activity(activity: dict) -> str:
    """Format a single activity with full detail."""
    moving_s = activity.get("moving_time_s", 0)
    hours = moving_s // 3600
    mins = (moving_s % 3600) // 60
    secs = moving_s % 60
    time_str = f"{hours}h {mins:02d}m {secs:02d}s" if hours else f"{mins}:{secs:02d}"

    hr = activity.get("avg_hr")
    max_hr = activity.get("max_hr")
    hr_str = f"{hr:.0f} bpm" + (f" (max {max_hr:.0f})" if max_hr else "") if hr else None

    lines = [
        f"<b>{activity.get('name', 'Last Run')}</b>",
        f"Date: {activity.get('date', '')[:10]}",
        f"Distance: {activity.get('distance_km', 0):.2f} km",
        f"Time: {time_str}",
        f"Pace: {activity.get('pace', 'N/A')}/km",
    ]
    if hr_str:
        lines.append(f"Avg HR: {hr_str}")
    elev = activity.get("elevation_m")
    if elev:
        lines.append(f"Elevation: {elev:.0f} m")
    cadence = activity.get("avg_cadence")
    if cadence:
        lines.append(f"Cadence: {cadence:.0f} spm")
    calories = activity.get("calories")
    if calories:
        lines.append(f"Calories: {calories}")
    suffer = activity.get("suffer_score")
    if suffer:
        lines.append(f"Suffer score: {suffer}")
    return "\n".join(lines)


def _format_zones() -> str:
    """Format HR and pace zones as HTML."""
    import _token_utils

    zones_path = _token_utils.DATA_DIR / "athlete_zones.json"
    if not zones_path.exists():
        return "No zones configured. Run: <code>just zones &lt;maxhr&gt;</code>"

    zones = json.loads(zones_path.read_text())
    hr_labels = {
        "zone1": "Recovery",
        "zone2": "Easy Aerobic",
        "zone3": "Tempo",
        "zone4": "Threshold",
        "zone5": "VO2max",
    }
    lines = ["<b>Training Zones</b>"]
    hr_zones = zones.get("hr_zones", {})
    if hr_zones:
        lines.append("\n<b>HR Zones</b>")
        for key, label in hr_labels.items():
            if key in hr_zones:
                lo, hi = hr_zones[key]
                lines.append(f"  {label}: {lo}–{hi} bpm")
    pace_zones = zones.get("pace_zones", {})
    if pace_zones:
        lines.append("\n<b>Pace Zones</b>")
        for name, (lo, hi) in pace_zones.items():
            lo_str = f"{lo // 60}:{lo % 60:02d}"
            hi_str = f"{hi // 60}:{hi % 60:02d}"
            lines.append(f"  {name.title()}: {lo_str}–{hi_str}/km")
    return "\n".join(lines)


def _format_status() -> str:
    """Brief status: last sync, plan exists, today's session."""
    import strava_sync

    lines = ["<b>RunWhisperer Status</b>"]

    activities = strava_sync._load_cached()
    if activities:
        last_date = activities[0].get("date", "")[:10]
        lines.append(f"Last activity: {last_date} ({len(activities)} cached)")
    else:
        lines.append("No activities synced yet")

    import plan as plan_mod

    p = plan_mod._load_plan()
    if p:
        goal = p.get("goal", "")
        weeks = len(p.get("weeks", []))
        lines.append(f"Plan: {goal} ({weeks} weeks)")
    else:
        lines.append("Plan: none set")

    session = _today_session()
    if session:
        stype = session.get("type", "unknown")
        lines.append(f"Today: {stype.title()}")
    else:
        lines.append("Today: rest day / no plan")

    return "\n".join(lines)
