"""HTML formatters and data helpers for Telegram messages."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _today_session() -> dict | None:
    """Match today's date against the training plan."""
    from coach_utils import plan as plan_mod

    p = plan_mod._load_plan()
    if not p or "weeks" not in p:
        return None

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    for week in p["weeks"]:
        for session in week.get("sessions", []):
            if session.get("date") == today:
                return session
    return None


def _weekly_summary(sport_types: set[str] | None = None) -> dict:
    """Summarise the last 7 days of cached Strava activities.

    Args:
        sport_types: Strava type strings to include, or None for all sports.

    Returns:
        {"runs": int, "total_km": float, "total_time_s": int,
         "avg_pace": str, "activities": [...], "sport_types": set|None}
    """
    from strava_utils import strava_sync

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
        if (
            sport_types
            and act.get("type") not in sport_types
            and act.get("sport_type") not in sport_types
        ):
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
        "sport_types": sport_types,
    }


# ---------------------------------------------------------------------------
# Session emoji helper
# ---------------------------------------------------------------------------

_SESSION_EMOJI: dict[str, str] = {
    "easy": "🍃",
    "long": "⏳",
    "tempo": "🔥",
    "intervals": "🔥",
    "race": "🔥",
}


def _session_emoji(session_type: str) -> str:
    """Return an emoji prefix for a session type, or '' for rest/unknown."""
    return _SESSION_EMOJI.get(session_type.lower(), "")


def _session_line(session: dict, marker: str = "") -> str:
    """Format a single session as a text line with optional marker."""
    stype = session.get("type", "")
    date = session.get("date", "")
    desc = session.get("description", "")
    emoji = _session_emoji(stype)
    prefix = f"{emoji} " if emoji else ""
    km = session.get("distance_km")
    km_str = f" — {km:.0f} km" if km else ""
    lead = f"{marker} " if marker else "  "
    return f"{lead}{date} — {prefix}{stype}: {desc}{km_str}"


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
    if session_type == "rest":
        return "Rest day — no session prescribed today."
    desc = session.get("description", "")
    dist = session.get("distance_km")
    emoji = _session_emoji(session_type)
    prefix = f"{emoji} " if emoji else ""

    lines = [f"<b>Today: {prefix}{session_type.title()}</b>"]
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
            if session.get("type") == "rest":
                continue
            lines.append(_session_line(session))

    return "\n".join(lines)


def _parse_session_km(session: dict) -> float | None:
    """Best-effort extraction of distance in km from a plan session."""
    import re

    if "distance_km" in session:
        return float(session["distance_km"])
    desc = session.get("description", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*km", desc, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _format_week_by_number(week_num: int) -> str:
    """Format a specific week (1-based) from the training plan."""
    from coach_utils import plan as plan_mod
    from strava_utils import strava_sync

    p = plan_mod._load_plan()
    if not p:
        return "No training plan set."

    weeks = p.get("weeks", [])
    if week_num < 1 or week_num > len(weeks):
        return f"Week {week_num} not found — plan has {len(weeks)} weeks."

    week = weeks[week_num - 1]
    phase = week.get("phase", "")
    sessions = sorted(week.get("sessions", []), key=lambda s: s.get("date", ""))

    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    # Dates that have a completed Strava activity
    done_dates: set[str] = set()
    if sessions:
        first_date = sessions[0].get("date", "")
        last_date = sessions[-1].get("date", "")
        for act in strava_sync._load_cached():
            d = act.get("date", "")[:10]
            if first_date <= d <= last_date:
                done_dates.add(d)

    header = f"<b>Week {week_num}</b>" + (f" — {phase}" if phase else "")
    lines = [header]
    for session in sessions:
        if session.get("type") == "rest":
            continue
        date = session.get("date", "")
        if date in done_dates:
            marker = "✓"
        elif date < today_str:
            marker = "✗"
        elif date == today_str:
            marker = "→"
        else:
            marker = "·"
        lines.append(_session_line(session, marker))

    return "\n".join(lines)


def _format_plan_overview() -> str:
    """Format a compact week-by-week overview of the full training plan."""
    from coach_utils import plan as plan_mod

    p = plan_mod._load_plan()
    if not p:
        return "No training plan set."

    goal = p.get("goal", "No goal set")
    weeks = p.get("weeks", [])
    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    lines = ["<b>Plan Overview</b>", f"Goal: {goal}", ""]

    for i, week in enumerate(weeks, start=1):
        phase = week.get("phase", "")
        sessions = week.get("sessions", [])
        dates = [s.get("date", "") for s in sessions if s.get("date")]

        # Date range label
        if dates:
            first = min(dates)
            last = max(dates)
            d_from = datetime.strptime(first, "%Y-%m-%d").strftime("%b %d")
            d_to = datetime.strptime(last, "%Y-%m-%d").strftime("%b %d")
            date_range = f"{d_from}–{d_to}"
        else:
            date_range = "—"

        # km total (best-effort from descriptions)
        km_parts = [_parse_session_km(s) for s in sessions if s.get("type") != "rest"]
        km_values = [k for k in km_parts if k is not None]
        km_str = f"~{sum(km_values):.0f} km" if km_values else "—"

        # Current week marker
        is_current = any(d >= today_str for d in dates) and any(
            d <= today_str for d in dates
        )
        marker = " ◀" if is_current else ""

        training_sessions = sum(1 for s in sessions if s.get("type") != "rest")
        lines.append(
            f"W{i:02d}  {phase:<10}  {date_range:<15}  "
            f"{training_sessions} sessions  {km_str}{marker}"
        )

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
    from tgbot.km_query import sport_label

    runs = summary.get("runs", 0)
    km = summary.get("total_km", 0)
    time_s = summary.get("total_time_s", 0)
    pace = summary.get("avg_pace", "N/A")
    sport_types = summary.get("sport_types")

    hours = time_s // 3600
    mins = (time_s % 3600) // 60

    label = sport_label(sport_types).title() + "s" if sport_types else "Activities"
    lines = [
        "<b>Weekly Summary (last 7 days)</b>",
        f"{label}: {runs}",
        f"Distance: {km:.1f} km",
        f"Time: {hours}h {mins:02d}m",
        f"Avg pace: {pace}/km",
    ]
    return "\n".join(lines)


def _format_week_vs_plan() -> str:
    """Format this week's planned sessions vs completed activities."""
    from coach_utils import plan as plan_mod
    from strava_utils import strava_sync

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
        if session.get("type") == "rest":
            continue
        date = session.get("date", "")
        if date in done_dates:
            marker = "✓"
        elif date < today_str:
            marker = "✗"
        elif date == today_str:
            marker = "→"
        else:
            marker = "·"
        lines.append(_session_line(session, marker))
    return "\n".join(lines)


def _format_next_sessions(n: int = 5) -> str:
    """Format the next N upcoming sessions from the plan."""
    from coach_utils import plan as plan_mod

    p = plan_mod._load_plan()
    if not p:
        return "No training plan set."

    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    upcoming = sorted(
        (
            s
            for w in p.get("weeks", [])
            for s in w.get("sessions", [])
            if s.get("date", "") >= today_str
        ),
        key=lambda s: s.get("date", ""),
    )[:n]

    if not upcoming:
        return "No upcoming sessions in the plan."

    lines = ["<b>Upcoming Sessions</b>"]
    for session in upcoming:
        if session.get("type") == "rest":
            continue
        date = session.get("date", "")
        marker = "→" if date == today_str else "·"
        lines.append(_session_line(session, marker))
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
    hr_str = (
        f"{hr:.0f} bpm" + (f" (max {max_hr:.0f})" if max_hr else "") if hr else None
    )

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


_SPARK_CHARS = " ▁▂▃▄▅▆▇█"  # index 0 = space (empty column)


def _sparkline(values: list[float]) -> str:
    """Return a single-line sparkline string from a list of float values."""
    if not values:
        return ""
    max_v = max(values) or 1.0
    n = len(_SPARK_CHARS) - 1
    return "".join(_SPARK_CHARS[min(round(v / max_v * n), n)] for v in values)


def _sparkline2(values: list[float]) -> tuple[str, str]:
    """Return a 2-row tall sparkline as (top_row, bottom_row), bottom-anchored.

    Each column is split at 50% of max: the bottom row covers 0-50% and the
    top row covers 50-100%, so every bar grows upward from the baseline.
    """
    if not values:
        return "", ""
    max_v = max(values) or 1.0
    chars = _SPARK_CHARS  # ' ▁▂▃▄▅▆▇█'
    n = 8
    top: list[str] = []
    bot: list[str] = []
    for v in values:
        f = v / max_v  # 0.0-1.0
        if f < 0.5:
            bot.append(chars[round(f / 0.5 * n)])
            top.append(" ")
        else:
            bot.append("█")
            top_level = max(1, round((f - 0.5) / 0.5 * n))
            top.append(chars[top_level])
    return "".join(top), "".join(bot)


def _format_training_load(metrics: dict, trend: list[dict]) -> str:
    """Format CTL/ATL/TSB and weekly km bar chart as HTML."""
    ctl = metrics.get("ctl", 0.0)
    atl = metrics.get("atl", 0.0)
    tsb = metrics.get("tsb", 0.0)

    if tsb > 5:
        label = "fresh"
    elif tsb > -10:
        label = "neutral"
    elif tsb > -25:
        label = "productive"
    else:
        label = "high fatigue"

    km_values = [w["km"] for w in trend]
    spark_top, spark_bot = _sparkline2(km_values)

    lines = [
        "<b>Training Load (PMC)</b>",
        f"CTL (fitness): {ctl:.1f}",
        f"ATL (fatigue): {atl:.1f}",
        f"TSB (form): {tsb:+.1f} — {label}",
        "",
        "<b>Weekly km (last 12 weeks)</b>",
        f"<code>{spark_top}</code>",
        f"<code>{spark_bot}</code>",
    ]
    for w in trend:
        week = w["week"]
        km = w["km"]
        bar = "█" * min(int(km / 5), 20)
        lines.append(f"<code>{week}  {km:5.1f} km  {bar}</code>")

    return "\n".join(lines)


def _format_readiness(data: dict) -> str:
    """Format race readiness assessment as HTML."""
    overall = data.get("overall", "insufficient_data")
    goal = data.get("goal", "No goal set")

    overall_labels = {
        "race_ready": "Race ready ✓",
        "on_track": "On track",
        "building": "Building",
        "needs_work": "Needs work",
        "insufficient_data": "Insufficient data",
    }
    label = overall_labels.get(overall, overall.replace("_", " ").title())

    lines = [
        "<b>Race Readiness</b>",
        f"Goal: {goal}",
        f"Status: <b>{label}</b>",
        "",
        f"Weekly avg: {data.get('weekly_avg_km', 0):.1f} km"
        f" ({data.get('volume_status', '?')})",
        f"Longest run: {data.get('longest_recent_run_km', 0):.1f} km"
        f" ({data.get('long_run_status', '?')})",
        f"CTL: {data.get('ctl', 0):.1f} ({data.get('ctl_trend', '?')})",
    ]
    if data.get("vdot"):
        lines.append(f"VDOT: {data['vdot']}")

    signals = data.get("signals", {})
    pos = signals.get("positive", [])
    neg = signals.get("negative", [])
    neutral = signals.get("neutral", [])

    if pos or neg or neutral:
        lines.append("")
    for s in pos:
        lines.append(f"✓ {s}")
    for s in neg:
        lines.append(f"✗ {s}")
    for s in neutral:
        lines.append(f"· {s}")

    return "\n".join(lines)


def _format_wellness(issues: list[dict], patterns: list[dict]) -> str:
    """Format wellness issues and detected patterns as HTML."""
    lines = ["<b>Wellness</b>"]

    if not issues:
        lines.append("No active issues — all clear.")
    else:
        lines.append(f"\n<b>Active issues ({len(issues)})</b>")
        for issue in issues:
            eid = issue.get("id", "?")
            date = issue.get("date", "?")
            itype = issue.get("type", "?")
            part = issue.get("body_part", "?")
            sev = issue.get("severity", "?")
            notes = issue.get("notes", "")
            note_str = f" — {notes}" if notes else ""
            lines.append(
                f"  <code>{eid}</code> {date} · {part}: {itype} {sev}/10{note_str}"
            )
        lines.append("\nResolve with: <code>/wellness resolve &lt;id&gt;</code>")

    if patterns:
        lines.append("\n<b>Patterns</b>")
        emoji_map = {"recurring": "🔄", "escalating": "⬆", "chronic": "⚠"}
        for p in patterns:
            ptype = p.get("type", "?")
            part = p.get("body_part", "?")
            detail = p.get("detail", "")
            e = emoji_map.get(ptype, "·")
            lines.append(f"  {e} {ptype.title()} — {part}: {detail}")

    return "\n".join(lines)


def _format_pace_calc(
    distance_km: float,
    time_s: float,
    vdot: float | None,
    training_paces: dict[str, str],
) -> str:
    """Format pace calculator result as HTML."""
    pace_s = time_s / distance_km
    pace_min = int(pace_s // 60)
    pace_sec = int(pace_s % 60)

    h = int(time_s // 3600)
    m = int((time_s % 3600) // 60)
    s = int(time_s % 60)
    time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    lines = [
        "<b>Pace Calculator</b>",
        f"Distance: {distance_km:.3g} km",
        f"Time: {time_str}",
        f"Pace: <b>{pace_min}:{pace_sec:02d}/km</b>",
    ]

    # Per-km split interval: 5 km for long races, 1 km otherwise
    split_km = 5.0 if distance_km >= 10 else 1.0
    split_s = pace_s * split_km
    sm, ss = int(split_s // 60), int(split_s % 60)
    lines.append(f"{split_km:.0f} km split: {sm}:{ss:02d}")

    if vdot:
        lines.append(f"VDOT: {vdot:.1f}")
        if training_paces:
            lines.append("\n<b>Training paces (Jack Daniels)</b>")
            for zone, pace in training_paces.items():
                lines.append(f"  {zone}: {pace}")

    return "\n".join(lines)


def _format_countdown() -> str:
    """Format race countdown from today to the last date in the training plan."""
    from datetime import date as date_cls

    from coach_utils import plan as plan_mod

    p = plan_mod._load_plan()
    if not p:
        return "No training plan set."

    weeks = p.get("weeks", [])
    goal = p.get("goal", "No goal set")
    if not weeks:
        return "Training plan has no weeks."

    today = datetime.now(tz=UTC).date()
    today_str = today.isoformat()

    all_dates = [
        s.get("date", "") for w in weeks for s in w.get("sessions", []) if s.get("date")
    ]
    if not all_dates:
        return "No dated sessions in the plan."

    race_date_str = max(all_dates)
    try:
        race_date = date_cls.fromisoformat(race_date_str)
    except ValueError:
        return "Could not parse race date."

    days_to_go = (race_date - today).days

    # Current week number and phase
    current_week_num: int | None = None
    current_phase = ""
    for i, week in enumerate(weeks, 1):
        dates = [s.get("date", "") for s in week.get("sessions", []) if s.get("date")]
        if dates and min(dates) <= today_str <= max(dates):
            current_week_num = i
            current_phase = week.get("phase", "")
            break

    lines = ["<b>Race Countdown</b>", f"Goal: {goal}", ""]

    if days_to_go < 0:
        lines.append(f"Race day was {abs(days_to_go)} days ago.")
    elif days_to_go == 0:
        lines.append("Race day is <b>today</b>! Good luck!")
    else:
        weeks_left = days_to_go // 7
        days_rem = days_to_go % 7
        if weeks_left:
            countdown = f"{weeks_left}w {days_rem}d" if days_rem else f"{weeks_left}w"
        else:
            countdown = f"{days_rem}d"
        lines.append(f"<b>{countdown}</b> to go ({race_date_str})")

    if current_week_num:
        phase_str = f" — {current_phase}" if current_phase else ""
        lines.append(f"Week {current_week_num}/{len(weeks)}{phase_str}")

    return "\n".join(lines)


def _format_predict(
    vdot: float,
    predictions: dict[str, float | None],
    training_paces: dict[str, str],
) -> str:
    """Format VDOT-based race time predictions as HTML."""
    lines = [f"<b>Race Predictions (VDOT {vdot:.1f})</b>", ""]

    for label, time_s in predictions.items():
        if time_s is None:
            lines.append(f"  {label}: —")
            continue
        h = int(time_s // 3600)
        m = int((time_s % 3600) // 60)
        s = int(time_s % 60)
        time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        lines.append(f"  {label}: {time_str}")

    if training_paces:
        lines += ["", "<b>Training paces</b>"]
        for zone, pace in training_paces.items():
            lines.append(f"  {zone}: {pace}")

    return "\n".join(lines)


def _format_zone_breakdown(weeks: int = 4, sport_types: set[str] | None = None) -> str:
    """Format volume-by-zone breakdown for the last N weeks."""
    import json as _json

    import _token_utils
    from coach_utils.analyze import classify_hr_zone
    from strava_utils import strava_sync

    zones_path = _token_utils.DATA_DIR / "athlete_zones.json"
    if not zones_path.exists():
        return "No zones configured. Run: <code>just zones &lt;maxhr&gt;</code>"

    zones = _json.loads(zones_path.read_text())
    hr_zones = zones.get("hr_zones", {})
    if not hr_zones:
        return "No HR zones configured in athlete_zones.json."

    cutoff = datetime.now(tz=UTC) - timedelta(days=weeks * 7)

    zone_km: dict[str, float] = {
        "zone1": 0.0,
        "zone2": 0.0,
        "zone3": 0.0,
        "zone4": 0.0,
        "zone5": 0.0,
        "unclassified": 0.0,
    }
    zone_labels = {
        "zone1": "Z1 Recovery",
        "zone2": "Z2 Easy",
        "zone3": "Z3 Tempo",
        "zone4": "Z4 Threshold",
        "zone5": "Z5 VO2max",
        "unclassified": "No HR data",
    }

    activity_count = 0
    all_activities = strava_sync._load_cached()
    if sport_types:
        all_activities = [
            a
            for a in all_activities
            if a.get("type") in sport_types or a.get("sport_type") in sport_types
        ]
    for act in all_activities:
        date_str = act.get("date", "")
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < cutoff:
            continue
        km = act.get("distance_km", 0) or 0
        if km <= 0:
            continue
        activity_count += 1
        avg_hr = act.get("avg_hr")
        if avg_hr:
            result = classify_hr_zone(avg_hr, hr_zones)
            zone = result.get("zone", "unclassified")
            if zone == "above_zone5":
                zone = "zone5"
            elif zone == "below_zone1":
                zone = "zone1"
            zone_km[zone if zone in zone_km else "unclassified"] += km
        else:
            zone_km["unclassified"] += km

    total_km = sum(zone_km.values())
    if total_km == 0:
        return f"No activities with distance data in the last {weeks} weeks."

    _BAR = 14
    lines = [
        f"<b>Zone Breakdown — last {weeks} weeks ({total_km:.0f} km, "
        f"{activity_count} activities)</b>",
        "",
    ]
    for key in ("zone1", "zone2", "zone3", "zone4", "zone5", "unclassified"):
        km = zone_km[key]
        if km == 0:
            continue
        pct = km / total_km * 100
        filled = round(pct / 100 * _BAR)
        bar = "█" * filled + "░" * (_BAR - filled)
        label = zone_labels[key]
        lines.append(f"<code>{label:<14}  {bar}  {km:5.1f} km  {pct:4.1f}%</code>")

    # Coaching note based on distribution
    classified_km = total_km - zone_km["unclassified"]
    if classified_km > 0:
        z12_pct = (zone_km["zone1"] + zone_km["zone2"]) / total_km * 100
        z3_pct = zone_km["zone3"] / total_km * 100
        lines.append("")
        if z3_pct > 20:
            lines.append(
                f"Zone 3 is {z3_pct:.0f}% of your volume — the grey zone. "
                "Hard enough to accumulate fatigue, not hard enough to adapt. "
                "Slow your easy runs down."
            )
        elif z12_pct < 65:
            lines.append(
                f"Only {z12_pct:.0f}% easy volume. "
                "Aim for 70–80% in zones 1–2 to build base without excess fatigue."
            )
        else:
            lines.append(f"{z12_pct:.0f}% easy volume — distribution looks healthy.")

    return "\n".join(lines)


def _format_status() -> str:
    """Brief status: last sync, plan exists, today's session."""
    from strava_utils import strava_sync

    lines = ["<b>Pacr Status</b>"]

    activities = strava_sync._load_cached()
    if activities:
        last_date = activities[0].get("date", "")[:10]
        lines.append(f"Last activity: {last_date} ({len(activities)} cached)")
    else:
        lines.append("No activities synced yet")

    from coach_utils import plan as plan_mod

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
