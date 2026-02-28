"""Athlete context building, VDOT helpers, and Claude plan generation."""

from __future__ import annotations

import json
import math
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .formatters import _today_session

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

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
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v ** 2
    pct_max = (
        0.8
        + 0.1894393 * math.exp(-0.012778 * t)
        + 0.2989558 * math.exp(-0.1932605 * t)
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
        disc = b ** 2 - 4 * a * c
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
        "10k": 10.0, "10km": 10.0,
        "5k": 5.0, "5km": 5.0,
        "15k": 15.0, "15km": 15.0,
        "20k": 20.0, "20km": 20.0,
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


def _build_athlete_context() -> str:
    """Build a system prompt from SOUL.md and live athlete data.

    Injects coaching personality, current plan, recent activities, zones,
    and today's session so Claude has full context for conversation.
    Results are cached for 60 seconds to avoid repeated disk reads.
    """
    now = datetime.now(tz=UTC).timestamp()
    cached = _context_cache.get("text")
    cached_ts = _context_cache.get("ts", 0.0)
    if cached and now - float(cached_ts) < 60:
        return str(cached)

    import _token_utils
    import plan as plan_mod
    import pot10
    import strava_sync

    activities = strava_sync._load_cached()
    lines: list[str] = []

    # Coaching personality
    soul_path = Path(__file__).parent.parent.parent / "config" / "SOUL.md"
    if soul_path.exists():
        lines.append(soul_path.read_text().strip())

    lines.append(f"\nToday's date is {datetime.now(tz=UTC).strftime('%Y-%m-%d')}.")

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
                lines.append(
                    f"  {s.get('date','')} — {s.get('type','')}: {s.get('description','')}"
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
    else:
        lines.append("\nNo training zones configured (run: just zones <maxhr>).")

    # Training load (PMC)
    from training_load import calculate_load_metrics, volume_spike_check

    load_metrics = calculate_load_metrics(activities)
    lines.append(
        f"\nTraining load (PMC): CTL {load_metrics['ctl']:.1f}, "
        f"ATL {load_metrics['atl']:.1f}, TSB {load_metrics['tsb']:+.1f}."
    )
    spike = volume_spike_check(activities)
    if spike:
        lines.append(f"Volume warning: {spike}")

    # Today's session
    session = _today_session()
    if session:
        stype = session.get("type", "")
        desc = session.get("description", "")
        lines.append(f"\nToday's prescribed session: {stype} — {desc}.")

    # Activities: individual detail for last 4 weeks, weekly summaries beyond that
    if activities:
        cutoff_recent = datetime.now(tz=UTC) - timedelta(days=28)
        cutoff_history = datetime.now(tz=UTC) - timedelta(days=365)

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
            elif dt >= cutoff_history:
                older_acts.append((dt, act))

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
                lines.append(f"  {date} — {name}: {dist:.1f} km @ {pace}/km{hr_str}{debrief_suffix}.")

        if older_acts:
            weeks_by_iso: dict[tuple[int, int], list[tuple]] = {}
            for dt, act in older_acts:
                iso = dt.isocalendar()
                weeks_by_iso.setdefault((iso.year, iso.week), []).append((dt, act))

            lines.append("\nWeekly summaries (older history):")
            for (year, week) in sorted(weeks_by_iso.keys(), reverse=True):
                week_acts = [a for _, a in weeks_by_iso[(year, week)]]
                total_km = sum(a.get("distance_km", 0) for a in week_acts)
                total_s = sum(a.get("moving_time_s", 0) for a in week_acts)
                hrs = [a["avg_hr"] for a in week_acts if a.get("avg_hr")]
                pace_str = strava_sync.format_pace(total_km * 1000, total_s) if total_km > 0 else "N/A"
                hr_str = f", avg HR {sum(hrs) / len(hrs):.0f} bpm" if hrs else ""
                week_start = datetime.fromisocalendar(year, week, 1).strftime("%b %d")
                week_end = datetime.fromisocalendar(year, week, 7).strftime("%b %d")
                lines.append(
                    f"  {year}-W{week:02d} ({week_start}–{week_end}): {len(week_acts)} runs, "
                    f"{total_km:.1f} km, avg pace {pace_str}/km{hr_str}."
                )
    else:
        lines.append("\nNo recent activities cached.")

    # Race results (last 5) + VDOT
    results = pot10._load_results()
    if results:
        lines.append("\nRecent race results:")
        best_vdot: float | None = None
        best_vdot_result: dict | None = None
        dist_map = {
            "5k": 5.0, "5km": 5.0,
            "10k": 10.0, "10km": 10.0,
            "hm": 21.0975, "half marathon": 21.0975,
            "marathon": 42.195,
        }
        for r in results[:5]:
            event = r.get("event", "").lower()
            time_str = r.get("time", "")
            lines.append(f"  {r.get('date','?')} {r.get('event','?')} — {time_str}.")
            dist_km = next((v for k, v in dist_map.items() if k in event), None)
            if dist_km and time_str:
                parts = time_str.split(":")
                try:
                    if len(parts) == 2:
                        time_s = int(parts[0]) * 60 + float(parts[1])
                    elif len(parts) == 3:
                        time_s = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
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

    result = "\n".join(lines)
    _context_cache["text"] = result
    _context_cache["ts"] = datetime.now(tz=UTC).timestamp()
    return result


# ---------------------------------------------------------------------------
# Claude plan generation
# ---------------------------------------------------------------------------


def _generate_plan_with_claude(goal: str) -> dict:
    """Call Claude to generate a training plan JSON from a natural-language goal.

    Args:
        goal: Natural-language goal, e.g. "half marathon on April 3 2026 in 1:21h".

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

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    # Pre-calculate target pace so the model never has to do the arithmetic
    pace_hint = _compute_goal_pace(goal)
    pace_line = f"- Target race pace (pre-calculated, use exactly): {pace_hint}" if pace_hint else ""

    # Inject recent fitness data so the plan is anchored to actual form
    fitness_lines: list[str] = []
    try:
        import pot10 as _pot10
        import strava_sync as _ss

        acts = _ss._load_cached()
        cutoff = (datetime.now(tz=UTC) - timedelta(days=56)).strftime("%Y-%m-%d")
        recent = [a for a in acts if a.get("date", "")[:10] >= cutoff]
        if recent:
            total_km = sum(a.get("distance_km", 0) for a in recent)
            fitness_lines.append(f"Last 8 weeks: {len(recent)} runs, {total_km:.0f} km total.")
            fitness_lines.append("Recent sessions (newest first):")
            for a in recent[:10]:
                hr = a.get("avg_hr")
                hr_str = f", HR {hr:.0f}" if hr else ""
                fitness_lines.append(
                    f"  {a.get('date','')[:10]} — {a.get('distance_km',0):.1f}km @ {a.get('pace','N/A')}/km{hr_str}"
                )
        results = _pot10._load_results()
        if results:
            fitness_lines.append("Race results:")
            for r in results[:5]:
                fitness_lines.append(f"  {r.get('date','?')} {r.get('event','?')} — {r.get('time','?')}")
    except Exception:
        pass
    fitness_context = "\n".join(fitness_lines) if fitness_lines else "No recent activity data available."

    system_prompt = f"""You are an expert running coach who generates training plans.
Today's date is {today}.

Athlete's recent fitness data:
{fitness_context}

Generate a structured training plan as raw JSON only — no prose, no markdown fences, no explanation.

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
          "description": "<one-line British English description>"
        }}
      ]
    }}
  ]
}}

Rules:
- Use Jack Daniels mesocycle structure: base → build → sharpen → taper
- Calculate all session dates from today ({today}) working backwards from the race date
- Include 5–6 sessions per week (rest days count as sessions with type "rest")
- Use British English in descriptions
{pace_line}
- Output raw JSON only — absolutely no markdown fences or extra text"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": f"Create a training plan for: {goal}"}],
            system=system_prompt,
        )
        raw = message.content[0].text.strip()
    except Exception as e:
        raise RuntimeError(f"Claude API error: {e}") from e

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw_lines = raw.splitlines()
        raw = "\n".join(raw_lines[1:-1] if raw_lines[-1].strip() == "```" else raw_lines[1:])

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude returned invalid JSON: {e}") from e

    if not isinstance(plan, dict):
        raise RuntimeError("Claude returned a non-object JSON value.")
    if "weeks" not in plan or not isinstance(plan["weeks"], list):
        raise RuntimeError("Claude plan is missing a valid 'weeks' array.")

    return plan
