"""Athlete context building, VDOT helpers, and Claude plan generation."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .formatters import _today_session

logger = logging.getLogger("pacr")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL = "claude-opus-4-6"

# Default conversational model — overridden per-user via /model command.
# Heavy tasks (plan generation, week editing) always use SONNET_MODEL.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", SONNET_MODEL)

_context_cache: dict[str, object] = {}


# ---------------------------------------------------------------------------
# VDOT and pace helpers (Jack Daniels Running Formula)
# ---------------------------------------------------------------------------


def _calculate_vdot(distance_km: float, time_s: float) -> float | None:
    """Compute VDOT from a race result using the Jack Daniels formula."""
    if distance_km <= 0 or time_s <= 0:
        return None
    t = time_s / 60  # minutes
    v = (distance_km * 1000) / t  # metres per minute
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v**2
    pct_max = (
        0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)
    )
    if pct_max <= 0:
        return None
    return round(vo2 / pct_max, 1)


def _vdot_paces(vdot: float) -> dict[str, str]:
    """Return Jack Daniels training paces (mm:ss/km) for a given VDOT."""

    def _velocity_for_pct(pct: float) -> float:
        """Solve for velocity (m/min) at a given % of VDOT using quadratic formula."""
        target_vo2 = vdot * pct
        # -4.60 + 0.182258*v + 0.000104*v^2 = target_vo2
        a, b, c = 0.000104, 0.182258, -4.60 - target_vo2
        disc = b**2 - 4 * a * c
        if disc < 0:
            return 0.0
        return (-b + math.sqrt(disc)) / (2 * a)

    def _pace_str(pct: float) -> str:
        v = _velocity_for_pct(pct)
        if v <= 0:
            return "N/A"
        pace_s = 1000 / v * 60  # seconds per km
        return f"{int(pace_s // 60)}:{int(pace_s % 60):02d}"

    return {
        "E (Easy)": f"{_pace_str(0.65)}–{_pace_str(0.74)}/km",
        "M (Marathon)": f"{_pace_str(0.75)}–{_pace_str(0.84)}/km",
        "T (Threshold)": f"{_pace_str(0.83)}–{_pace_str(0.88)}/km",
        "I (Interval)": f"{_pace_str(0.95)}–{_pace_str(1.00)}/km",
        "R (Repetition)": f"{_pace_str(1.05)}/km",
    }


def _predict_time(vdot: float, distance_km: float) -> float | None:
    """Binary-search for the race time (seconds) that yields a given VDOT.

    Higher VDOT = faster runner, so shorter time.  Returns None if no
    reasonable solution is found within 60 s - 24 h.
    """
    lo, hi = 60.0, 86_400.0
    for _ in range(60):
        mid = (lo + hi) / 2
        v = _calculate_vdot(distance_km, mid)
        if v is None:
            return None
        if v > vdot:
            lo = mid  # too fast (VDOT too high) → need more time
        else:
            hi = mid
    return (lo + hi) / 2


def _best_vdot_from_results() -> float | None:
    """Return the best VDOT calculated from cached race results, or None."""
    from strava_utils import pot10

    dist_map = {
        "5k": 5.0,
        "5km": 5.0,
        "10k": 10.0,
        "10km": 10.0,
        "hm": 21.0975,
        "half marathon": 21.0975,
        "marathon": 42.195,
    }
    best: float | None = None
    for r in pot10._load_results() or []:
        time_str = r.get("time", "")
        _dm = re.match(r"(\d+(?:\.\d+)?)\s*km", r.get("distance", "").lower())
        if _dm:
            dist_km: float | None = float(_dm.group(1))
        else:
            dist_km = next(
                (v for k, v in dist_map.items() if k in r.get("event", "").lower()),
                None,
            )
        if not dist_km or not time_str:
            continue
        parts = time_str.split(":")
        try:
            if len(parts) == 2:
                time_s = int(parts[0]) * 60 + float(parts[1])
                # Reinterpret as H:MM if pace is impossibly fast (< 90 s/km)
                if time_s / dist_km < 90:
                    time_s = int(parts[0]) * 3600 + int(parts[1]) * 60
            elif len(parts) == 3:
                time_s = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                continue
            v = _calculate_vdot(dist_km, time_s)
            if v and (best is None or v > best):
                best = v
        except (ValueError, IndexError):
            pass
    return best


def _compute_goal_pace(goal: str) -> str | None:
    """Parse a natural-language goal and return target pace as mm:ss/km.

    Handles patterns like "half marathon in 1:21h", "10k in 45:00", "marathon in 3:30".
    Returns None if the goal cannot be parsed.
    """
    goal_lower = goal.lower()

    distances: dict[str, float] = {
        "marathon": 42.195,
        "half marathon": 21.0975,
        "half-marathon": 21.0975,
        "10k": 10.0,
        "10km": 10.0,
        "5k": 5.0,
        "5km": 5.0,
        "15k": 15.0,
        "15km": 15.0,
        "20k": 20.0,
        "20km": 20.0,
    }
    distance_km: float | None = None
    for name, km in sorted(distances.items(), key=lambda x: -len(x[0])):
        if name in goal_lower:
            distance_km = km
            break
    if distance_km is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*km", goal_lower)
        if m:
            distance_km = float(m.group(1))

    # Parse time: "1:21h", "1:21:30", "81min", "81 minutes", "3:30"
    total_minutes: float | None = None
    m = re.search(r"(\d+):(\d+):(\d+)", goal)
    if m:
        total_minutes = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 60
    else:
        m = re.search(r"(\d+):(\d+)\s*h", goal)
        if m:
            total_minutes = int(m.group(1)) * 60 + int(m.group(2))
        else:
            m = re.search(r"(\d+):(\d+)", goal)
            if m:
                total_minutes = int(m.group(1)) * 60 + int(m.group(2))
            else:
                m = re.search(r"(\d+(?:\.\d+)?)\s*min", goal_lower)
                if m:
                    total_minutes = float(m.group(1))

    if distance_km is None or total_minutes is None or distance_km <= 0:
        return None

    pace_s_per_km = (total_minutes * 60) / distance_km
    mins = int(pace_s_per_km // 60)
    secs = int(pace_s_per_km % 60)
    return f"{mins}:{secs:02d}/km"


# ---------------------------------------------------------------------------
# Athlete context (system prompt for conversational AI)
# ---------------------------------------------------------------------------


def _build_static_context(sport_key: str = "run") -> str:
    """Build a system prompt from SOUL.md and live athlete data.

    Injects coaching personality, current plan, recent activities, zones,
    and today's session so Claude has full context for conversation.
    Results are cached per sport_key for 60 seconds to avoid repeated disk reads.
    """
    now = datetime.now(tz=UTC).timestamp()
    cache_key = f"text_{sport_key}"
    ts_key = f"ts_{sport_key}"
    cached = _context_cache.get(cache_key)
    cached_ts = _context_cache.get(ts_key, 0.0)
    if cached and now - float(cached_ts) < 60:
        return str(cached)

    import _token_utils
    from coach_utils import plan as plan_mod
    from strava_utils import pot10, strava_sync

    activities = strava_sync._load_cached()
    lines: list[str] = []

    # Coaching personality
    soul_path = Path(__file__).parent.parent.parent / "config" / "SOUL.md"
    if soul_path.exists():
        lines.append(soul_path.read_text().strip())

    lines.append(f"\nToday is {datetime.now(tz=UTC).strftime('%A %Y-%m-%d')}.")
    focus = "all sports" if sport_key == "all" else sport_key
    lines.append(f"The athlete's current activity focus is: {focus}.")

    # Training plan — full schedule so the model can read and modify it
    p = plan_mod._load_plan()
    if p:
        goal = p.get("goal", "")
        plan_weeks = p.get("weeks", [])
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        lines.append(f"\nTraining plan: {goal} ({len(plan_weeks)} weeks).")
        for i, week in enumerate(plan_weeks):
            phase = week.get("phase", "")
            sessions = week.get("sessions", [])
            is_current = any(s.get("date", "") >= today for s in sessions)
            marker = " ← current week" if is_current else ""
            lines.append(f"\nWeek {i + 1} ({phase}){marker}:")
            for s in sessions:
                sdate = s.get("date", "")
                try:
                    day_name = datetime.fromisoformat(sdate).strftime("%a")
                except (ValueError, TypeError):
                    day_name = ""
                lines.append(
                    f"  {sdate} ({day_name}) — {s.get('type', '')}: "
                    f"{s.get('description', '')}"
                )
    else:
        lines.append("\nNo training plan set.")

    # Training zones
    zones_path = _token_utils.DATA_DIR / "athlete_zones.json"
    if zones_path.exists():
        zones = json.loads(zones_path.read_text())
        hr_labels = {
            "zone1": "Recovery",
            "zone2": "Easy Aerobic",
            "zone3": "Tempo",
            "zone4": "Threshold",
            "zone5": "VO2max",
        }
        hr_zones = zones.get("hr_zones", {})
        if hr_zones:
            lines.append("\nHR training zones:")
            for key, label in hr_labels.items():
                if key in hr_zones:
                    lo, hi = hr_zones[key]
                    lines.append(f"  {label} ({key}): {lo}–{hi} bpm.")
        pace_zones = zones.get("pace_zones", {})
        if pace_zones:
            lines.append("Pace zones (seconds/km → min:sec/km):")
            for name, (lo, hi) in pace_zones.items():
                lo_str = f"{lo // 60}:{lo % 60:02d}"
                hi_str = f"{hi // 60}:{hi % 60:02d}"
                lines.append(f"  {name}: {lo_str}–{hi_str}/km.")

        # Cycling power zones
        cycling = zones.get("cycling", {})
        cycling_power = cycling.get("power_zones", {})
        if cycling_power:
            ftp = cycling.get("ftp", "?")
            lines.append(f"\nCycling power zones (FTP: {ftp}W):")
            for name, (lo, hi) in cycling_power.items():
                lines.append(f"  {name}: {lo}–{hi}W.")

        # Swimming pace zones
        swimming = zones.get("swimming", {})
        swim_paces = swimming.get("pace_zones", {})
        if swim_paces:
            css = swimming.get("css_per_100m", "?")
            lines.append(f"\nSwimming pace zones (CSS: {css}s/100m):")
            for name, (lo, hi) in swim_paces.items():
                lo_str = f"{lo // 60}:{lo % 60:02d}"
                hi_str = f"{hi // 60}:{hi % 60:02d}"
                lines.append(f"  {name}: {lo_str}–{hi_str}/100m.")
    else:
        lines.append("\nNo training zones configured (run: just zones <maxhr>).")

    # Training load (PMC)
    from coach_utils.training_load import calculate_load_metrics, volume_spike_check

    load_metrics = calculate_load_metrics(activities)
    lines.append(
        f"\nTraining load (PMC): CTL {load_metrics['ctl']:.1f}, "
        f"ATL {load_metrics['atl']:.1f}, TSB {load_metrics['tsb']:+.1f}."
    )
    spike = volume_spike_check(activities)
    if spike:
        lines.append(f"Volume warning: {spike}")

    # Plan adherence
    try:
        from coach_utils.adherence import calculate_adherence

        adherence = calculate_adherence(4)
        lines.append(
            f"\nPlan adherence (4 weeks): {adherence['adherence_pct']:.0f}% "
            f"({adherence['completed']} completed, {adherence['partial']} partial, "
            f"{adherence['missed']} missed)."
        )
    except Exception:
        pass

    # Personal records
    try:
        from coach_utils.records import load_records

        recs = load_records()
        if recs:
            rec_parts = []
            for key, val in recs.items():
                label = key.replace("_", " ").title()
                if "time_str" in val:
                    rec_parts.append(f"{label}: {val['time_str']}")
                elif "distance_km" in val:
                    rec_parts.append(f"{label}: {val['distance_km']:.1f} km")
                elif "days" in val:
                    rec_parts.append(f"{label}: {val['days']} days")
            lines.append(f"\nPersonal records: {'; '.join(rec_parts)}.")
    except Exception:
        pass

    # Race readiness
    try:
        from coach_utils.readiness import assess_readiness

        readiness = assess_readiness()
        if readiness["overall"] != "insufficient_data":
            lines.append(
                f"\nRace readiness: {readiness['overall'].replace('_', ' ')} "
                f"(weekly avg {readiness['weekly_avg_km']:.0f} km, "
                f"CTL {readiness['ctl']:.0f}, "
                f"trend: {readiness['ctl_trend']})."
            )
    except Exception:
        pass

    # Today's and tomorrow's sessions — explicit to avoid date confusion
    session = _today_session()
    if session:
        stype = session.get("type", "")
        desc = session.get("description", "")
        lines.append(f"\nToday's prescribed session: {stype} — {desc}.")

    tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_day = (datetime.now(tz=UTC) + timedelta(days=1)).strftime("%A")
    if p:
        for week in p.get("weeks", []):
            for s in week.get("sessions", []):
                if s.get("date") == tomorrow:
                    lines.append(
                        f"Tomorrow ({tomorrow_day} {tomorrow}) prescribed session: "
                        f"{s.get('type', '')} — {s.get('description', '')}."
                    )
                    break

    # Activities: individual detail for last 4 weeks,
    # weekly summaries for all older history
    if activities:
        cutoff_recent = datetime.now(tz=UTC) - timedelta(days=28)

        recent_acts: list[tuple] = []
        older_acts: list[tuple] = []
        for act in activities:
            date_str = act.get("date", "")
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if dt >= cutoff_recent:
                recent_acts.append((dt, act))
            else:
                older_acts.append((dt, act))

        # Tell Claude the full extent of available data
        all_dates = [dt for dt, _ in recent_acts + older_acts]
        if all_dates:
            earliest = min(all_dates).strftime("%Y-%m-%d")
            lines.append(
                f"\nActivity history spans {earliest} to today "
                f"({len(activities)} total). "
                "Use the lookup_activities tool for individual session detail "
                "beyond the last 4 weeks."
            )

        if recent_acts:
            from .debrief import load_debriefs

            debriefs = load_debriefs()
            lines.append("\nRecent sessions (last 4 weeks):")
            for _, act in recent_acts:
                date = act.get("date", "")[:10]
                name = act.get("name", "Run")
                dist = act.get("distance_km", 0)
                pace = act.get("pace", "N/A")
                hr = act.get("avg_hr")
                hr_str = f", HR {hr:.0f} bpm" if hr else ""
                debrief = debriefs.get(str(act.get("id", "")))
                debrief_suffix = (
                    f" (RPE {debrief['rpe']}/10 — {debrief['notes']})"
                    if debrief
                    else ""
                )
                lines.append(
                    f"  {date} — {name}: {dist:.1f} km"
                    f" @ {pace}/km{hr_str}{debrief_suffix}."
                )

        if older_acts:
            weeks_by_iso: dict[tuple[int, int], list[tuple]] = {}
            for dt, act in older_acts:
                iso = dt.isocalendar()
                weeks_by_iso.setdefault((iso.year, iso.week), []).append((dt, act))

            _MAX_SUMMARY_WEEKS = 13  # 3 months — keeps system prompt token count low
            all_weeks = sorted(weeks_by_iso.keys(), reverse=True)
            display_weeks = all_weeks[:_MAX_SUMMARY_WEEKS]
            omitted = len(all_weeks) - len(display_weeks)

            lines.append("\nWeekly summaries (last 3 months):")
            for year, week in display_weeks:
                week_acts = [a for _, a in weeks_by_iso[(year, week)]]
                total_km = sum(a.get("distance_km", 0) for a in week_acts)
                total_s = sum(a.get("moving_time_s", 0) for a in week_acts)
                hrs = [a["avg_hr"] for a in week_acts if a.get("avg_hr")]
                pace_str = (
                    strava_sync.format_pace(total_km * 1000, total_s)
                    if total_km > 0
                    else "N/A"
                )
                hr_str = f", avg HR {sum(hrs) / len(hrs):.0f} bpm" if hrs else ""
                week_start = datetime.fromisocalendar(year, week, 1).strftime("%b %d")
                week_end = datetime.fromisocalendar(year, week, 7).strftime("%b %d")
                lines.append(
                    f"  {year}-W{week:02d} ({week_start}–{week_end}):"
                    f" {len(week_acts)} runs, {total_km:.1f} km,"
                    f" avg pace {pace_str}/km{hr_str}."
                )
            if omitted:
                lines.append(
                    f"  ({omitted} older weeks not shown — use lookup_activities to search further back)"  # noqa: E501
                )
    else:
        lines.append("\nNo recent activities cached.")

    # Race results (last 10 by date) + VDOT
    results = pot10._load_results()
    if results:
        sorted_results = sorted(results, key=lambda r: r.get("date", ""), reverse=True)
        lines.append(f"\nRace history: {len(results)} races on file. Most recent 10:")
        best_vdot: float | None = None
        best_vdot_result: dict | None = None
        dist_map = {
            "5k": 5.0,
            "5km": 5.0,
            "10k": 10.0,
            "10km": 10.0,
            "hm": 21.0975,
            "half marathon": 21.0975,
            "marathon": 42.195,
        }
        for r in sorted_results[:10]:
            time_str = r.get("time", "")
            dist_str = r.get("distance", "")
            lines.append(
                f"  {r.get('date', '?')} {r.get('event', '?')} {dist_str} — {time_str}."
            )
        # Compute best VDOT from ALL results
        for r in results:
            time_str = r.get("time", "")
            _dm = re.match(r"(\d+(?:\.\d+)?)\s*km", r.get("distance", "").lower())
            if _dm:
                dist_km = float(_dm.group(1))
            else:
                dist_km = next(
                    (v for k, v in dist_map.items() if k in r.get("event", "").lower()),
                    None,
                )
            if dist_km and time_str:
                parts = time_str.split(":")
                try:
                    if len(parts) == 2:
                        time_s = int(parts[0]) * 60 + float(parts[1])
                        if time_s / dist_km < 90:
                            time_s = int(parts[0]) * 3600 + int(parts[1]) * 60
                    elif len(parts) == 3:
                        time_s = (
                            int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                        )
                    else:
                        continue
                    v = _calculate_vdot(dist_km, time_s)
                    if v and (best_vdot is None or v > best_vdot):
                        best_vdot, best_vdot_result = v, r
                except (ValueError, IndexError):
                    pass
        if best_vdot:
            paces = _vdot_paces(best_vdot)
            event_label = best_vdot_result.get("event", "") if best_vdot_result else ""
            lines.append(f"\nVDOT (from {event_label}): {best_vdot}")
            lines.append("Jack Daniels training paces:")
            for zone, pace in paces.items():
                lines.append(f"  {zone}: {pace}")

    _MAX_CONTEXT_CHARS = 400_000  # ~100K tokens, leaves room for conversation

    result = "\n".join(lines)
    if len(result) > _MAX_CONTEXT_CHARS:
        logger.warning("System prompt too large (%d chars), trimming", len(result))
        result = (
            result[:_MAX_CONTEXT_CHARS] + "\n(… context truncated to fit token limit)"
        )
    _context_cache[cache_key] = result
    _context_cache[ts_key] = datetime.now(tz=UTC).timestamp()
    return result


def _build_athlete_context(sport_key: str = "run", query: str = "") -> str:
    """Return the full system prompt, optionally augmented with relevant memories.

    Calls _build_static_context (cached) then appends vector-retrieved coaching
    notes when *query* is non-empty.  All existing callers are unaffected because
    the *query* parameter defaults to "".
    """
    context = _build_static_context(sport_key)
    if not query:
        return context

    try:
        from memory.store import query_memories

        memories = query_memories(query, n_results=5)
    except Exception:
        logger.warning("Failed to retrieve memories", exc_info=True)
        memories = []

    if not memories:
        return context

    lines = ["\nRelevant coaching notes from previous sessions:"]
    for m in memories:
        lines.append(f"  - {m['text']}")
    return context + "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude plan generation
# ---------------------------------------------------------------------------


def _generate_plan_with_claude(
    goal: str,
    days_per_week: int | None = None,
    max_km_per_week: int | None = None,
) -> dict:
    """Call Claude to generate a training plan JSON from a natural-language goal.

    Args:
        goal: Natural-language goal, e.g. "half marathon on April 3 2026 in 1:21h".
        days_per_week: Optional cap on training days per week (rest days fill the rest).
        max_km_per_week: Optional cap on weekly volume in km.

    Returns:
        Parsed plan dict with at least a 'weeks' array.

    Raises:
        RuntimeError: If the API key is missing, the API call fails, or the
            response cannot be parsed as a valid plan.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to .env and restart the bot."
        )

    today_dt = datetime.now(tz=UTC).date()
    today = today_dt.isoformat()
    # Snap plan start to Monday of the current ISO week so every week is Mon-Sun
    plan_start = (today_dt - timedelta(days=today_dt.weekday())).isoformat()

    # Pre-calculate target pace so the model never has to do the arithmetic
    pace_hint = _compute_goal_pace(goal)
    pace_line = (
        f"- Target race pace (pre-calculated, use exactly): {pace_hint}"
        if pace_hint
        else ""
    )

    # Inject recent fitness data so the plan is anchored to actual form
    fitness_lines: list[str] = []
    try:
        from strava_utils import pot10 as _pot10
        from strava_utils import strava_sync as _ss

        acts = _ss._load_cached()
        cutoff = (datetime.now(tz=UTC) - timedelta(days=56)).strftime("%Y-%m-%d")
        recent = [a for a in acts if a.get("date", "")[:10] >= cutoff]
        if recent:
            total_km = sum(a.get("distance_km", 0) for a in recent)
            fitness_lines.append(
                f"Last 8 weeks: {len(recent)} runs, {total_km:.0f} km total."
            )
            fitness_lines.append("Recent sessions (newest first):")
            for a in recent[:10]:
                hr = a.get("avg_hr")
                hr_str = f", HR {hr:.0f}" if hr else ""
                fitness_lines.append(
                    f"  {a.get('date', '')[:10]} — "
                    f"{a.get('distance_km', 0):.1f}km"
                    f" @ {a.get('pace', 'N/A')}/km{hr_str}"
                )
        results = _pot10._load_results()
        if results:
            fitness_lines.append("Race results:")
            for r in results[:5]:
                fitness_lines.append(
                    f"  {r.get('date', '?')} {r.get('event', '?')}"
                    f" — {r.get('time', '?')}"
                )
    except Exception:
        pass
    fitness_context = (
        "\n".join(fitness_lines)
        if fitness_lines
        else "No recent activity data available."
    )

    days_line = (
        f"- Training days per week: exactly {days_per_week} "
        f'(the remaining {7 - days_per_week} days must be type "rest")'
        if days_per_week
        else "- Include 5–6 training days per week (rest days fill the remainder)"
    )
    km_line = (
        f"- Maximum weekly volume: {max_km_per_week} km"
        " — do not exceed this in any week"
        if max_km_per_week
        else ""
    )

    system_prompt = f"""You are an expert running coach who generates training plans.
Today is {today_dt.strftime("%A")} {today}.

Athlete's recent fitness data:
{fitness_context}

Generate a structured training plan as raw JSON only.
No prose, no markdown fences, no explanation.

The JSON must follow this exact schema:
{{
  "goal": "<string describing the race goal>",
  "weeks": [
    {{
      "phase": "<base|build|sharpen|taper>",
      "sessions": [
        {{
          "date": "<YYYY-MM-DD>",
          "type": "<easy|tempo|intervals|long|rest|race>",
          "description": "<one-line British English description>",
          "distance_km": <total session distance as a number, omit for rest>
        }}
      ]
    }}
  ]
}}

Rules:
- Use Jack Daniels mesocycle structure: base → build → sharpen → taper
- Week 1 starts on {plan_start} (Monday). Every week runs Monday–Sunday.
  Calculate all session dates forward from {plan_start} to the race date.
  Today is {today} — sessions before today may already be in the past; keep them.
- Include exactly 7 sessions per week (training days + rest days must sum to 7)
- Use British English in descriptions
- Set distance_km to the total session distance in km
  (warm-up + main set + cool-down for tempo/intervals); omit for rest days
{pace_line}
{days_line}
{km_line}
- Output raw JSON only — absolutely no markdown fences or extra text"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=16384,
            messages=[
                {"role": "user", "content": f"Create a training plan for: {goal}"}
            ],
            system=system_prompt,
        )
        if message.stop_reason == "max_tokens":
            raise RuntimeError(
                "Claude hit the token limit mid-response — plan too long. "
                "Try a shorter goal period or contact the developer."
            )
        raw = message.content[0].text.strip()
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Claude API error: {e}") from e

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw_lines = raw.splitlines()
        raw = "\n".join(
            raw_lines[1:-1] if raw_lines[-1].strip() == "```" else raw_lines[1:]
        )

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude returned invalid JSON: {e}") from e

    if not isinstance(plan, dict):
        raise RuntimeError("Claude returned a non-object JSON value.")
    if "weeks" not in plan or not isinstance(plan["weeks"], list):
        raise RuntimeError("Claude plan is missing a valid 'weeks' array.")

    return plan


def _edit_week_with_claude(week_num: int, instruction: str) -> dict:
    """Modify a specific plan week in-place using a natural-language instruction.

    Args:
        week_num: 1-based week index.
        instruction: Natural-language edit, e.g. "add a tempo on Wednesday".

    Returns:
        The updated week dict (already saved to disk).

    Raises:
        RuntimeError: If the plan is missing, the week is out of range, the
            API call fails, or the response cannot be parsed.
    """
    import anthropic

    from coach_utils import plan as plan_mod

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set.")

    p = plan_mod._load_plan()
    if not p:
        raise RuntimeError("No training plan set.")

    weeks = p.get("weeks", [])
    if week_num < 1 or week_num > len(weeks):
        raise RuntimeError(f"Week {week_num} not found — plan has {len(weeks)} weeks.")

    week = weeks[week_num - 1]

    system_prompt = """\
You are an expert running coach modifying one week of a training plan.
Return only the modified sessions array as raw JSON — no prose, no markdown fences.
Rules:
- Preserve all unaffected sessions exactly as given
  (date, type, description, distance_km)
- Keep exactly 7 entries per week (training days + rest days must sum to 7)
- Set distance_km on every non-rest session
- Use British English in descriptions"""

    user_msg = (
        f"Week {week_num} sessions (current):\n"
        f"{json.dumps(week['sessions'], indent=2)}\n\n"
        f"Instruction: {instruction}\n\n"
        "Return the modified sessions array as raw JSON only."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        if message.stop_reason == "max_tokens":
            raise RuntimeError(
                "Claude hit the token limit — try a simpler instruction."
            )
        raw = message.content[0].text.strip()
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Claude API error: {e}") from e

    if raw.startswith("```"):
        raw_lines = raw.splitlines()
        raw = "\n".join(
            raw_lines[1:-1] if raw_lines[-1].strip() == "```" else raw_lines[1:]
        )

    try:
        sessions = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude returned invalid JSON: {e}") from e

    if not isinstance(sessions, list):
        raise RuntimeError("Expected a sessions array from Claude.")

    p["weeks"][week_num - 1]["sessions"] = sessions
    plan_mod._save_plan(p)
    return p["weeks"][week_num - 1]
