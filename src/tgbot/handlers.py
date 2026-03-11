"""Telegram command handlers and bot state management."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tgbot.context import (
    CLAUDE_MODEL,
    HAIKU_MODEL,
    OPUS_MODEL,
    SONNET_MODEL,
    _edit_week_with_claude,
    _generate_plan_with_claude,
)
from tgbot.debrief import parse_rpe, save_debrief
from tgbot.formatters import (
    _format_countdown,
    _format_last_activity,
    _format_next_sessions,
    _format_pace_calc,
    _format_plan_overview,
    _format_plan_summary,
    _format_predict,
    _format_readiness,
    _format_results,
    _format_status,
    _format_today_session,
    _format_training_load,
    _format_week_by_number,
    _format_week_vs_plan,
    _format_weekly_summary,
    _format_wellness,
    _format_zone_breakdown,
    _format_zones,
    _today_session,
    _weekly_summary,
)
from tgbot.km_query import (
    sport_label,
    types_for_key,
)

logger = logging.getLogger("pacr")

_MAX_HISTORY = 60  # individual messages (~30 conversational turns)
_BLOCKED_FILES = {"tokens.json"}  # never exposed to the model
_MAX_CHATS = 5
_RATE_LIMIT = 5
_RATE_WINDOW = 60  # seconds


@dataclass
class BotConfig:
    """Shared bot state, injected via context.bot_data["config"]."""

    chat_id: str
    conversation_history: dict[int, list[dict]] = field(default_factory=dict)
    pending_debriefs: dict[int, dict] = field(default_factory=dict)
    pending_analysis: dict[int, dict] = field(default_factory=dict)
    rate_timestamps: dict[int, deque] = field(default_factory=dict)
    activity_type: str = "run"
    chat_model: str = CLAUDE_MODEL


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _validate_data_path(filename: str) -> Path | None:
    """Validate a filename resolves inside DATA_DIR and is not blocked.

    Returns the resolved Path if valid, or None if the path is blocked or
    escapes the data directory (path traversal).
    """
    import _token_utils

    path = (_token_utils.DATA_DIR / filename).resolve()
    if not path.is_relative_to(_token_utils.DATA_DIR.resolve()):
        return None
    if path.name in _BLOCKED_FILES:
        return None
    return path


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------


def _load_history(config: BotConfig) -> None:
    """Populate conversation_history from disk (called once at bot startup)."""
    import _token_utils

    path = _token_utils.DATA_DIR / "conversation_history.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    if len(data) > _MAX_CHATS:
        data = dict(list(data.items())[-_MAX_CHATS:])
    for k, v in data.items():
        config.conversation_history[int(k)] = v


def _save_history(config: BotConfig) -> None:
    """Persist conversation_history to disk."""
    import _token_utils

    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_utils.DATA_DIR / "conversation_history.json"
    path.write_text(
        json.dumps(
            {str(k): v for k, v in config.conversation_history.items()}, indent=2
        )
    )


def _load_settings(config: BotConfig) -> None:
    """Load persisted settings (e.g. activity_type) from disk."""
    import _token_utils

    path = _token_utils.DATA_DIR / "settings.json"
    if path.exists():
        data = json.loads(path.read_text())
        config.activity_type = data.get("activity_type", "run")
        config.chat_model = data.get("chat_model", CLAUDE_MODEL)


def _save_settings(config: BotConfig) -> None:
    """Persist bot settings to disk."""
    import _token_utils

    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_utils.DATA_DIR / "settings.json"
    path.write_text(
        json.dumps(
            {"activity_type": config.activity_type, "chat_model": config.chat_model},
            indent=2,
        )
    )


def _filter_by_sport(activities: list[dict], sport_key: str) -> list[dict]:
    """Filter activities list to those matching the given sport key."""
    types = types_for_key(sport_key)
    if types is None:
        return activities
    return [
        a for a in activities if a.get("type") in types or a.get("sport_type") in types
    ]


# ---------------------------------------------------------------------------
# Post-sync analysis helper
# ---------------------------------------------------------------------------


def _auto_analyse_new_activities(before_ids: set[int]) -> str | None:
    """Compare cached activities before and after a sync.

    Returns a coaching note if any new activities warrant feedback, else None.
    """
    from coach_utils import analyze
    from strava_utils import strava_sync

    after = strava_sync._load_cached()
    new_acts = [a for a in after if a["id"] not in before_ids]
    if not new_acts:
        return None

    _MAX_ANALYSED = 5
    omitted = max(0, len(new_acts) - _MAX_ANALYSED)
    notes: list[str] = []
    for act in new_acts[:_MAX_ANALYSED]:
        result = analyze._analyze_activity(act)
        flags = result.get("flags", [])
        dist = act.get("distance_km", 0)
        pace = act.get("pace", "N/A")
        name = act.get("name", "Run")
        header = f"{name} — {dist:.1f}km @ {pace}/km"
        if flags:
            notes.append(header + "\n" + "\n".join(f"  ⚠ {f}" for f in flags))
        else:
            notes.append(f"{header} — on target.")

    if omitted:
        notes.append(
            f"({omitted} older activit{'y' if omitted == 1 else 'ies'} not shown.)"
        )
    return "\n\n".join(notes) if notes else None


# ---------------------------------------------------------------------------
# Handlers — each reads BotConfig from context.bot_data["config"]
# ---------------------------------------------------------------------------


def _cfg(context: object) -> BotConfig:
    """Retrieve BotConfig from the telegram context."""
    return context.bot_data["config"]  # type: ignore[index]


async def _run_analysis(new_act_ids: set[int], context: object, chat_id: str) -> None:
    """Re-sync, analyse specific activities, and prompt for debrief."""
    from coach_utils import analyze
    from strava_utils import strava_sync

    config = _cfg(context)
    logger.info(
        "Analysis starting: re-syncing last 7 days for %d activity(ies)",
        len(new_act_ids),
    )
    await asyncio.to_thread(strava_sync.sync, 7)
    activities = await asyncio.to_thread(strava_sync._load_cached)
    new_acts = [a for a in activities if a["id"] in new_act_ids]
    if not new_acts:
        logger.warning(
            "Analysis: no matching activities found after re-sync (ids=%s)",
            new_act_ids,
        )
        return

    # Fetch laps/splits for activities missing them
    for act in new_acts:
        if not act.get("laps"):
            try:
                detail = await asyncio.to_thread(
                    strava_sync._fetch_detail_fields, act["id"]
                )
                if detail.get("laps"):
                    act["laps"] = detail["laps"]
                if detail.get("splits_metric"):
                    act["splits_metric"] = detail["splits_metric"]
            except Exception:
                pass

    # Rules-based flags + split analysis
    act_results: list[tuple[dict, dict, dict]] = []
    notes: list[str] = []
    for act in new_acts:
        result = await asyncio.to_thread(analyze._analyze_activity, act)
        split_data = await asyncio.to_thread(analyze.analyse_splits, act)
        act_results.append((act, result, split_data))
        flags = result.get("flags", [])
        dist = act.get("distance_km", 0)
        pace = act.get("pace", "N/A")
        name = act.get("name", "Run")
        logger.info(
            "Analysis: %s — %.1f km @ %s/km, flags=%s",
            name,
            dist,
            pace,
            flags or "none",
        )
        header = f"{name} — {dist:.1f}km @ {pace}/km"
        parts: list[str] = []
        if flags:
            parts.append(header)
            parts.extend(f"  ⚠ {f}" for f in flags)
        else:
            parts.append(f"{header} — on target.")

        # Append split flags
        split_flags = split_data.get("flags", [])
        if split_flags:
            parts.extend(f"  📊 {f}" for f in split_flags)

        # Append lap summary
        laps = act.get("laps", [])
        if len(laps) > 1:
            parts.append(f"\n<b>Laps ({len(laps)})</b>")
            for i, lap in enumerate(laps, 1):
                d = lap.get("distance_m", 0) / 1000
                p = lap.get("pace", "N/A")
                hr = lap.get("avg_hr")
                hr_s = f"  HR {hr:.0f}" if hr else ""
                parts.append(f"  {i}. {d:.2f}km  {p}/km{hr_s}")

        notes.append("\n".join(parts))
    analysis_text = "Activity analysis:\n\n" + "\n\n".join(notes)
    await context.bot.send_message(  # type: ignore[union-attr]
        chat_id=chat_id,
        text=f"<b>{analysis_text}</b>"
        if not analysis_text.startswith("<b>")
        else analysis_text,
        parse_mode="HTML",
    )

    # Inject analysis into conversation history so follow-ups have context
    cid = int(chat_id)
    history = config.conversation_history.setdefault(cid, [])
    history.append({"role": "assistant", "content": analysis_text})

    # Coaching opinion via Claude — one per activity
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        if len(act_results) > 3:
            logger.warning(
                "Fetching descriptions for %d activities (may be slow)",
                len(act_results),
            )
        for act, result, split_data in act_results:
            desc = act.get("description", "")
            if not desc:
                logger.info("Fetching description for activity %s", act["id"])
                desc = await asyncio.to_thread(
                    strava_sync._fetch_description, act["id"]
                )
                if desc:
                    logger.info("Description fetched (%d chars)", len(desc))
            lines = [
                f"Activity: {act.get('name', 'Run')}",
                f"Distance: {act.get('distance_km', 0):.1f} km",
                f"Pace: {act.get('pace', 'N/A')}/km",
            ]
            if act.get("avg_hr"):
                lines.append(f"Avg HR: {act['avg_hr']:.0f} bpm")
            if act.get("elevation_m"):
                lines.append(f"Elevation: {act['elevation_m']:.0f} m")
            if act.get("calories"):
                lines.append(f"Calories: {act['calories']}")
            if desc:
                lines.append(f"Athlete's note: {desc}")
            flags = result.get("flags", [])
            if flags:
                lines.append("Flags: " + "; ".join(flags))
            # Include split pacing info
            split_flags = split_data.get("flags", [])
            if split_flags:
                lines.append("Pacing: " + "; ".join(split_flags))
            if split_data.get("cv"):
                lines.append(f"Pace CV: {split_data['cv']:.1%}")
            # Include lap data for quality sessions (intervals, tempo, race)
            # but not for easy/long runs where it causes over-analysis
            prescribed = result.get("prescribed") or {}
            prescribed_type = prescribed.get("type", "").lower()
            if prescribed_type in ("intervals", "tempo", "race", "workout"):
                laps = act.get("laps", [])
                if len(laps) > 1:
                    lap_strs = []
                    for i, lap in enumerate(laps, 1):
                        d = lap.get("distance_m", 0) / 1000
                        p = lap.get("pace", "N/A")
                        hr = lap.get("avg_hr")
                        hr_s = f" HR {hr:.0f}" if hr else ""
                        lap_strs.append(f"{i}. {d:.2f}km {p}/km{hr_s}")
                    lines.append("Laps: " + " | ".join(lap_strs))
            # Include HR zone context
            hr_zone = result.get("hr_zone", {})
            if hr_zone:
                lines.append(
                    f"HR zone: {hr_zone.get('label', '')} ({hr_zone.get('zone', '')})"
                )
            # Use full athlete context (plan, zones, recent sessions, VDOT, etc.)
            from tgbot.context import _build_static_context

            system_prompt = _build_static_context()
            prompt = "\n".join(lines)
            logger.info("Requesting coaching opinion from Claude (%s)", CLAUDE_MODEL)
            try:
                msg = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=160
                    if prescribed_type in ("intervals", "tempo", "race", "workout")
                    else 120,
                    system=system_prompt,
                    messages=[{"role": "user", "content": prompt}],
                )
                opinion = next((b.text for b in msg.content if hasattr(b, "text")), "")
                if opinion:
                    logger.info("Coaching opinion sent (%d chars)", len(opinion))
                    await context.bot.send_message(  # type: ignore[union-attr]
                        chat_id=chat_id, text=opinion
                    )
                    history.append({"role": "assistant", "content": opinion})
            except Exception:
                logger.exception("Coaching opinion failed — continuing")

    act = new_acts[0]
    config.pending_debriefs[int(chat_id)] = {
        "activity_id": act["id"],
        "activity_name": act.get("name", "Run"),
        "activity_date": act.get("date", "")[:10],
        "asked_at": datetime.now(tz=UTC).timestamp(),
    }
    await context.bot.send_message(  # type: ignore[union-attr]
        chat_id=chat_id,
        text="How did that feel? Reply with RPE 1–10 … or <code>skip</code>.",
        parse_mode="HTML",
    )


async def _heartbeat(context: object) -> None:
    from strava_utils import strava_sync

    config = _cfg(context)
    chat_id = config.chat_id
    logger.info("Heartbeat: syncing Strava (last 3 days)…")
    before_ids = {a["id"] for a in await asyncio.to_thread(strava_sync._load_cached)}
    await asyncio.to_thread(strava_sync.sync, 3, False)
    after = await asyncio.to_thread(strava_sync._load_cached)
    new_acts = [a for a in after if a["id"] not in before_ids]
    if not new_acts:
        logger.info("Heartbeat: no new activities")
        return
    names = ", ".join(a.get("name", "activity") for a in new_acts)
    logger.info("Heartbeat: %d new activity(ies): %s", len(new_acts), names)

    from tgbot.bot import STRAVA_ANALYSIS_DELAY

    delay_min = STRAVA_ANALYSIS_DELAY // 60
    cid = int(chat_id)
    existing = config.pending_analysis.get(cid)
    merged_ids = list(
        set((existing or {}).get("new_act_ids", []) + [a["id"] for a in new_acts])
    )
    if existing:
        for job in context.job_queue.get_jobs_by_name(existing["job_name"]):  # type: ignore[union-attr]
            job.schedule_removal()
        logger.info(
            "Heartbeat: merging %d new IDs into pending analysis", len(new_acts)
        )
    job_name = f"deferred_{merged_ids[0]}"
    config.pending_analysis[cid] = {
        "job_name": job_name,
        "new_act_ids": merged_ids,
    }
    await context.bot.send_message(  # type: ignore[union-attr]
        chat_id=chat_id,
        text=f"<b>New activity detected:</b> {names}.\n"
        f"I'll analyse it in {delay_min} min — edit away in Strava first, "
        f"or reply <b>ready</b> to analyse now.",
        parse_mode="HTML",
    )
    context.job_queue.run_once(  # type: ignore[union-attr]
        _deferred_analysis,
        when=STRAVA_ANALYSIS_DELAY,
        data={"new_act_ids": merged_ids},
        name=job_name,
    )


async def _deferred_analysis(context: object) -> None:
    config = _cfg(context)
    cid = int(config.chat_id)
    config.pending_analysis.pop(cid, None)
    new_act_ids: set[int] = set(context.job.data["new_act_ids"])  # type: ignore[union-attr]
    await _run_analysis(new_act_ids, context, config.chat_id)


async def morning_checkin(context: object) -> None:
    """Send a conversational 8am check-in with today's session."""
    from tgbot.formatters import _today_session

    config = _cfg(context)
    session = _today_session()

    if session:
        stype = session.get("type", "session").title()
        desc = session.get("description", "")
        dist = session.get("distance_km")
        dist_str = f" ({dist} km)" if dist else ""
        text = (
            f"Morning! How are you feeling today?\n\n"
            f"You've got a <b>{stype}{dist_str}</b> on the plan:\n"
            f"{desc}\n\n"
            f"Let me know how it goes — I'll pick it up from Strava automatically."
        )
    else:
        text = (
            "Morning! How are you feeling today?\n\n"
            "Nothing on the plan — enjoy the rest. "
            "Drop me a message if you want a chat about training."
        )

    # Wellness reminder
    try:
        from coach_utils.wellness import get_active_issues

        active_issues = get_active_issues()
        if active_issues:
            parts = [
                f"{i['body_part']} ({i['severity']}/10)" for i in active_issues[:3]
            ]
            text += (
                f"\n\n\u26a0 Active issues: {', '.join(parts)}. "
                "Let me know how they're feeling."
            )
    except Exception:
        pass

    await context.bot.send_message(  # type: ignore[union-attr]
        chat_id=config.chat_id,
        text=text,
        parse_mode="HTML",
    )


async def weekly_debrief(context: object) -> None:
    """Sunday evening check-in — reflect on the week and flag anything forward."""
    # Only fire on Sundays (weekday 6)
    if datetime.now(tz=UTC).weekday() != 6:
        return

    config = _cfg(context)

    week_summary = _format_week_vs_plan()
    text = (
        f"<b>Weekly Check-in</b>\n\n"
        f"{week_summary}\n\n"
        "How did the week go? Anything worth flagging — sessions that felt "
        "harder or easier than expected, any niggles, or anything life threw "
        "at you? A quick note helps me keep next week honest."
    )

    # Append any active wellness issues as a reminder
    try:
        from coach_utils.wellness import get_active_issues

        active_issues = get_active_issues()
        if active_issues:
            parts = [
                f"{i['body_part']} ({i['severity']}/10)" for i in active_issues[:3]
            ]
            text += (
                f"\n\n\u26a0 Active issues: {', '.join(parts)}. "
                "How are these feeling after the week?"
            )
    except Exception:
        pass

    await context.bot.send_message(  # type: ignore[union-attr]
        chat_id=config.chat_id,
        text=text,
        parse_mode="HTML",
    )


async def cmd_start(update: object, context: object) -> None:
    logger.info("/start")
    status = await asyncio.to_thread(_format_status)
    await update.message.reply_text(  # type: ignore[union-attr]
        f"Hello! I'm Pacr, your running coach.\n\n{status}",
        parse_mode="HTML",
    )


async def cmd_sync(update: object, context: object) -> None:
    args = context.args or []  # type: ignore[union-attr]
    try:
        days = int(args[0]) if args else 365
    except (ValueError, IndexError):
        days = 365
    logger.info("/sync requested (%d days)", days)
    await update.message.reply_text(  # type: ignore[union-attr]
        f"Syncing Strava activities (last {days} days)…"
    )
    from strava_utils import strava_sync

    config = _cfg(context)
    try:
        before_ids = {a["id"] for a in strava_sync._load_cached()}
        await asyncio.to_thread(strava_sync.sync, days)
        activities = await asyncio.to_thread(strava_sync._load_cached)
        logger.info("/sync complete: %d activities cached", len(activities))
        from memory.store import (
            index_activities,
            index_debriefs,
            index_race_results,
            index_wellness,
        )
        from tgbot.debrief import load_debriefs

        new_activities = [a for a in activities if a["id"] not in before_ids]
        indexed = await asyncio.to_thread(index_activities, new_activities)
        debriefs = await asyncio.to_thread(load_debriefs)
        await asyncio.to_thread(index_debriefs, debriefs)
        # Index race results + wellness on sync
        try:
            from coach_utils.wellness import _load_log
            from strava_utils.pot10 import _load_results

            await asyncio.to_thread(index_race_results, _load_results())
            await asyncio.to_thread(index_wellness, _load_log())
        except Exception:
            logger.debug("race/wellness indexing failed", exc_info=True)
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Sync complete. {len(activities)} activities cached, "
            f"{indexed} indexed to memory.",
            parse_mode="HTML",
        )
        note = await asyncio.to_thread(_auto_analyse_new_activities, before_ids)
        if note:
            _MAX_NOTE = 4000
            header = "<b>New activity analysis (quick):</b>\n\n"
            body = note if len(note) <= _MAX_NOTE else note[:_MAX_NOTE] + "…"
            await update.message.reply_text(  # type: ignore[union-attr]
                header + body,
                parse_mode="HTML",
            )
        new_acts = [a for a in activities if a["id"] not in before_ids]
        if note and new_acts:
            act = new_acts[0]
            cid = update.effective_chat.id  # type: ignore[union-attr]
            config.pending_debriefs[cid] = {
                "activity_id": act["id"],
                "activity_name": act.get("name", "Run"),
                "activity_date": act.get("date", "")[:10],
                "asked_at": datetime.now(tz=UTC).timestamp(),
            }
            await update.message.reply_text(  # type: ignore[union-attr]
                "How did that feel? Reply with RPE 1–10 … or <code>skip</code>.",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.exception("/sync failed")
        await update.message.reply_text(f"Sync failed: {e}")  # type: ignore[union-attr]


async def cmd_plan(update: object, context: object) -> None:
    from coach_utils import plan as plan_mod

    p = await asyncio.to_thread(plan_mod._load_plan)
    if p:
        text = _format_plan_summary(p)
    else:
        text = "No training plan set. Ask your coach to create one."
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_setplan(update: object, context: object) -> None:
    if not context.args:  # type: ignore[union-attr]
        await update.message.reply_text(  # type: ignore[union-attr]
            "Usage: /setplan &lt;goal&gt; [--days=N] [--max-km=N]\n"
            "e.g. <code>/setplan half marathon on April 3 2026 in 1:21h"
            " --days=5 --max-km=70</code>",
            parse_mode="HTML",
        )
        return

    import re as _re

    raw_args = " ".join(context.args)  # type: ignore[union-attr]

    days_per_week: int | None = None
    max_km_per_week: int | None = None

    m = _re.search(r"--days[= ](\d+)", raw_args)
    if m:
        days_per_week = max(1, min(7, int(m.group(1))))
        raw_args = _re.sub(r"--days[= ]\d+", "", raw_args)

    m = _re.search(r"--max-km[= ](\d+)", raw_args)
    if m:
        max_km_per_week = int(m.group(1))
        raw_args = _re.sub(r"--max-km[= ]\d+", "", raw_args)

    goal = raw_args.strip()
    await update.message.reply_text("Generating your plan...")  # type: ignore[union-attr]

    try:
        plan_dict = await asyncio.to_thread(
            _generate_plan_with_claude, goal, days_per_week, max_km_per_week
        )
    except Exception as e:
        await update.message.reply_text(f"Failed to generate plan: {e}")  # type: ignore[union-attr]
        return

    from coach_utils import plan as plan_mod

    try:
        await asyncio.to_thread(plan_mod._save_plan, plan_dict)
    except Exception as e:
        await update.message.reply_text(f"Failed to save plan: {e}")  # type: ignore[union-attr]
        return

    summary = _format_plan_summary(plan_dict)
    await update.message.reply_text(  # type: ignore[union-attr]
        f"Plan saved!\n\n{summary}", parse_mode="HTML"
    )


async def cmd_today(update: object, context: object) -> None:
    session = await asyncio.to_thread(_today_session)
    text = _format_today_session(session)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_analyse(update: object, context: object) -> None:
    logger.info("/analyse requested")
    from strava_utils import strava_sync

    config = _cfg(context)
    activities = await asyncio.to_thread(strava_sync._load_cached)
    activities = _filter_by_sport(activities, config.activity_type)
    if not activities:
        await update.message.reply_text(  # type: ignore[union-attr]
            "No matching activities cached. Try /sync or change filter with /sport."
        )
        return
    await update.message.reply_text("Analysing your last activity…")  # type: ignore[union-attr]
    await _run_analysis({activities[0]["id"]}, context, config.chat_id)


async def cmd_results(update: object, context: object) -> None:
    from strava_utils import pot10

    results = await asyncio.to_thread(pot10._load_results)
    text = _format_results(results)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_week(update: object, context: object) -> None:
    args = context.args  # type: ignore[union-attr]
    if args and args[0].isdigit():
        text = await asyncio.to_thread(_format_week_by_number, int(args[0]))
    else:
        text = await asyncio.to_thread(_format_week_vs_plan)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_plan_overview(update: object, context: object) -> None:
    text = await asyncio.to_thread(_format_plan_overview)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_edit_week(update: object, context: object) -> None:
    args = context.args  # type: ignore[union-attr]
    if not args or not args[0].isdigit() or len(args) < 2:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Usage: /editweek &lt;N&gt; &lt;instruction&gt;\n"
            "e.g. <code>/editweek 5 add an extra tempo session on Wednesday</code>\n"
            "     <code>/editweek 4 remove the Thursday run — I'm on holiday</code>",
            parse_mode="HTML",
        )
        return

    week_num = int(args[0])
    instruction = " ".join(args[1:])
    await update.message.reply_text(  # type: ignore[union-attr]
        f"Updating week {week_num}..."
    )

    try:
        await asyncio.to_thread(_edit_week_with_claude, week_num, instruction)
    except Exception as e:
        await update.message.reply_text(f"Failed to update week: {e}")  # type: ignore[union-attr]
        return

    text = await asyncio.to_thread(_format_week_by_number, week_num)
    await update.message.reply_text(  # type: ignore[union-attr]
        f"Week {week_num} updated.\n\n{text}", parse_mode="HTML"
    )


async def cmd_next(update: object, context: object) -> None:
    text = await asyncio.to_thread(_format_next_sessions)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_last(update: object, context: object) -> None:
    from strava_utils import strava_sync

    activities = await asyncio.to_thread(strava_sync._load_cached)
    activities = _filter_by_sport(activities, _cfg(context).activity_type)
    if not activities:
        await update.message.reply_text(  # type: ignore[union-attr]
            "No matching activities cached. Try /sync or change filter with /sport."
        )
        return
    act = activities[0]
    # Fetch laps on demand if not cached
    if not act.get("laps"):
        try:
            detail = await asyncio.to_thread(
                strava_sync._fetch_detail_fields, act["id"]
            )
            if detail.get("laps"):
                act["laps"] = detail["laps"]
            if detail.get("splits_metric"):
                act["splits_metric"] = detail["splits_metric"]
        except Exception:
            pass  # show activity without laps
    await update.message.reply_text(  # type: ignore[union-attr]
        _format_last_activity(act), parse_mode="HTML"
    )


async def cmd_summary(update: object, context: object) -> None:
    sport_types = types_for_key(_cfg(context).activity_type)
    summary = await asyncio.to_thread(_weekly_summary, sport_types)
    await update.message.reply_text(  # type: ignore[union-attr]
        _format_weekly_summary(summary), parse_mode="HTML"
    )


async def cmd_zones(update: object, context: object) -> None:
    text = await asyncio.to_thread(_format_zones)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_load(update: object, context: object) -> None:
    from coach_utils.training_load import (
        calculate_load_metrics,
        volume_spike_check,
        weekly_km_trend,
    )
    from strava_utils import strava_sync

    activities = await asyncio.to_thread(strava_sync._load_cached)
    activities = _filter_by_sport(activities, _cfg(context).activity_type)
    metrics = await asyncio.to_thread(calculate_load_metrics, activities)
    trend = await asyncio.to_thread(weekly_km_trend, activities)
    spike = await asyncio.to_thread(volume_spike_check, activities)
    text = _format_training_load(metrics, trend)
    if spike:
        text += f"\n\n⚠ {spike}"
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_clear(update: object, context: object) -> None:
    config = _cfg(context)
    cid = update.effective_chat.id  # type: ignore[union-attr]
    config.conversation_history.pop(cid, None)
    await asyncio.to_thread(_save_history, config)
    await update.message.reply_text("Conversation history cleared.")  # type: ignore[union-attr]


_FALLBACK_QUOTES = [
    '"Iron sharpens iron. Biscuits sharpen nothing." — Coach T. Rigby, 1994',
    '"Somewhere a Kenyan is warming up. You are already behind." — Anonymous',
    (
        '"The only bad run is the one where you checked your watch'
        ' and then sat down." — D. Hutchins'
    ),
    (
        '"Two types of pain: the pain of discipline, and the pain'
        ' of explaining your DNF." — R. Oswald'
    ),
    (
        '"Your legs are not giving out. Your brain is giving up.'
        ' Evict it." — Coach O. Leary'
    ),
    '"Sweat is just weakness evaporating and leaving a damp patch." — G. Mercer',
    (
        '"The finish line is just the start line of your excuses."'
        " — P. Dunne, Athletics Monthly"
    ),
    '"You can rest when you\'re DNS." — M. Wills, Track & Field Quarterly',
    '"A 10k does not care how busy you were last week." — Coach B. Stanton',
    '"The treadmill is not running. You are just failing to escape." — J. Carmichael',
    (
        '"Champions are made in the moments when they want to stop'
        " but don't have a good enough excuse.\" — F. Kimura"
    ),
    '"Pain is temporary. Your Strava is forever." — Dr. A. Hollis',
    (
        '"The body achieves what the mind believes, unless the mind'
        ' has been watching too much television." — Coach S. Nkosi'
    ),
    (
        '"No one ever looked back on a race and wished they had'
        ' started slower." — E. Okafor, 2003 (disputed)'
    ),
    '"Fatigue is just fitness knocking loudly." — T. Lindqvist',
    (
        '"Easy days are the hardest days because they require'
        ' humility, and you have very little." — Coach P. Reyes'
    ),
    (
        '"The marathon doesn\'t care about your personality."'
        " — R. Abara, Dublin Track Club"
    ),
    '"Consistency is the enemy of excuses." — Coach H. Bergström',
    (
        '"Run easy until it feels easy, then run slightly less easy."'
        " — J. Osei, VDOT Research Unit"
    ),
    '"The plan is not optional. The suffering is." — M. Farrant',
]


def _motivation_quote(api_key: str) -> str:
    """Return a funny made-up motivational running quote.

    Uses Claude Haiku if an API key is available; falls back to the hardcoded
    list otherwise.
    """
    import random

    if not api_key:
        return random.choice(_FALLBACK_QUOTES)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=80,
            system=(
                "You produce one short, made-up motivational running quote. "
                "Style: dry, faintly absurd, like 'iron sharpens iron' but original. "
                "British English. One sentence, then a short attribution to a "
                "plausible-sounding fake person or publication. "
                "No exclamation marks. No emojis. Output only the quote."
            ),
            messages=[{"role": "user", "content": "Give me a motivational quote."}],
        )
        text = next((b.text for b in msg.content if hasattr(b, "text")), "").strip()
        return text if text else random.choice(_FALLBACK_QUOTES)
    except Exception:
        logger.debug("Motivation quote API call failed — using fallback", exc_info=True)
        return random.choice(_FALLBACK_QUOTES)


async def cmd_motivation(update: object, context: object) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    quote = await asyncio.to_thread(_motivation_quote, api_key)
    await update.message.reply_text(f"<i>{quote}</i>", parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_readiness(update: object, context: object) -> None:
    from coach_utils.readiness import assess_readiness

    data = await asyncio.to_thread(assess_readiness)
    text = _format_readiness(data)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_wellness(update: object, context: object) -> None:
    from coach_utils.wellness import (
        detect_patterns,
        get_active_issues,
        log_entry,
        resolve_entry,
    )

    args = context.args or []  # type: ignore[union-attr]

    # /wellness resolve <id>
    if args and args[0].lower() == "resolve":
        if len(args) < 2:
            await update.message.reply_text(  # type: ignore[union-attr]
                "Usage: <code>/wellness resolve &lt;id&gt;</code>",
                parse_mode="HTML",
            )
            return
        entry_id = args[1]
        ok = await asyncio.to_thread(resolve_entry, entry_id)
        if ok:
            await update.message.reply_text(  # type: ignore[union-attr]
                f"Issue <code>{entry_id}</code> resolved.", parse_mode="HTML"
            )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]
                f"No active issue found with id <code>{entry_id}</code>.",
                parse_mode="HTML",
            )
        return

    # /wellness <body_part...> <severity> [notes...]
    # Find first integer arg — it becomes severity; everything before = body_part,
    # everything after = notes.
    if args:
        sev_idx: int | None = None
        for i, a in enumerate(args):
            if a.isdigit():
                sev_idx = i
                break

        if sev_idx is not None:
            body_part = " ".join(args[:sev_idx])
            severity = int(args[sev_idx])
            notes = " ".join(args[sev_idx + 1 :])
            if not body_part:
                await update.message.reply_text(  # type: ignore[union-attr]
                    "Please specify a body part, "
                    "e.g. <code>/wellness left knee 6</code>",
                    parse_mode="HTML",
                )
                return
            entry = await asyncio.to_thread(
                log_entry, "soreness", body_part, severity, notes
            )
            eid = entry.get("id", "?")
            await update.message.reply_text(  # type: ignore[union-attr]
                f"Logged: {body_part} soreness {severity}/10"
                + (f" — {notes}" if notes else "")
                + f"\nID: <code>{eid}</code>",
                parse_mode="HTML",
            )
            return

        # No digit found — fall through to show
        await update.message.reply_text(  # type: ignore[union-attr]
            "Usage:\n"
            "  /wellness — show active issues\n"
            "  /wellness &lt;body_part&gt; &lt;severity&gt; [notes] — log issue\n"
            "  /wellness resolve &lt;id&gt; — resolve issue",
            parse_mode="HTML",
        )
        return

    # No args — show current issues
    issues = await asyncio.to_thread(get_active_issues)
    patterns = await asyncio.to_thread(detect_patterns)
    text = _format_wellness(issues, patterns)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


def _parse_time_str(s: str) -> float | None:
    """Parse a time string to total seconds.

    Accepts: H:MM:SS, H:MMh, MM:SS, or plain minutes (e.g. 81).
    """
    import re

    s = s.strip()
    # H:MM:SS
    m = re.match(r"^(\d+):(\d+):(\d+)$", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    # H:MMh
    m = re.match(r"^(\d+):(\d+)h$", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    # MM:SS
    m = re.match(r"^(\d+):(\d+)$", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


async def cmd_pace(update: object, context: object) -> None:
    """Calculate pace, VDOT and training zones from distance + finish time."""
    from tgbot.context import _calculate_vdot, _vdot_paces

    args = context.args or []  # type: ignore[union-attr]
    if len(args) < 2:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Usage: /pace &lt;distance&gt; &lt;time&gt;\n"
            "Examples:\n"
            "  <code>/pace 5k 21:30</code>\n"
            "  <code>/pace half 1:21:00</code>\n"
            "  <code>/pace marathon 3:30:00</code>\n"
            "  <code>/pace 10 45:00</code>",
            parse_mode="HTML",
        )
        return

    time_str = args[-1]
    dist_raw = " ".join(args[:-1]).lower().strip()

    _DIST_MAP: dict[str, float] = {
        "marathon": 42.195,
        "half marathon": 21.0975,
        "half": 21.0975,
        "hm": 21.0975,
        "10k": 10.0,
        "10km": 10.0,
        "5k": 5.0,
        "5km": 5.0,
        "mile": 1.60934,
        "1 mile": 1.60934,
    }
    distance_km = _DIST_MAP.get(dist_raw)
    if distance_km is None:
        import re as _re

        m = _re.match(r"(\d+(?:\.\d+)?)", dist_raw)
        if m:
            distance_km = float(m.group(1))
    if distance_km is None:
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Unknown distance: <code>{dist_raw}</code>. "
            "Use 5k, 10k, half, marathon, or a number in km.",
            parse_mode="HTML",
        )
        return

    time_s = _parse_time_str(time_str)
    if time_s is None:
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Could not parse time: <code>{time_str}</code>. "
            "Use MM:SS, H:MM:SS, or H:MMh.",
            parse_mode="HTML",
        )
        return

    # Sanity-check: if pace would be faster than 2:00/km treat MM:SS as H:MMm
    if time_s / distance_km < 120 and ":" in time_str and time_str.count(":") == 1:
        parts = time_str.split(":")
        time_s = int(parts[0]) * 3600 + int(parts[1]) * 60

    vdot = await asyncio.to_thread(_calculate_vdot, distance_km, time_s)
    paces = await asyncio.to_thread(_vdot_paces, vdot) if vdot else {}
    text = _format_pace_calc(distance_km, time_s, vdot, paces)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_countdown(update: object, context: object) -> None:
    text = await asyncio.to_thread(_format_countdown)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_predict(update: object, context: object) -> None:
    """Predict race times at standard distances from VDOT."""
    from tgbot.context import _best_vdot_from_results, _predict_time, _vdot_paces

    args = context.args or []  # type: ignore[union-attr]

    vdot: float | None = None
    if args:
        try:
            vdot = float(args[0])
        except ValueError:
            await update.message.reply_text(  # type: ignore[union-attr]
                f"Invalid VDOT: <code>{args[0]}</code>. "
                "Provide a number or omit to use your race results.",
                parse_mode="HTML",
            )
            return
    else:
        vdot = await asyncio.to_thread(_best_vdot_from_results)

    if vdot is None:
        await update.message.reply_text(  # type: ignore[union-attr]
            "No VDOT available — add race results first with /results, "
            "or pass a VDOT directly: <code>/predict 52.5</code>",
            parse_mode="HTML",
        )
        return

    _PRED_DISTANCES: dict[str, float] = {
        "1 mile": 1.60934,
        "5k": 5.0,
        "10k": 10.0,
        "Half Marathon": 21.0975,
        "Marathon": 42.195,
    }
    predictions: dict[str, float | None] = {}
    for label, km in _PRED_DISTANCES.items():
        predictions[label] = await asyncio.to_thread(_predict_time, vdot, km)

    paces = await asyncio.to_thread(_vdot_paces, vdot)
    text = _format_predict(vdot, predictions, paces)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_memory(update: object, context: object) -> None:
    from memory.store import memory_stats

    stats = await asyncio.to_thread(memory_stats)
    if not stats["available"]:
        await update.message.reply_text(  # type: ignore[union-attr]
            "ChromaDB unavailable."
        )
        return
    lines = [
        "<b>Coaching Memory</b>",
        f"Documents: {stats['total']}",
        f"Disk: {stats['disk_mb']} MB",
        "",
    ]
    cats = stats.get("categories", {})
    if cats:
        for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
            label = cat.replace("_", " ").title()
            lines.append(f"  {label}: {n}")
    await update.message.reply_text(  # type: ignore[union-attr]
        "\n".join(lines), parse_mode="HTML"
    )


async def cmd_help(update: object, context: object) -> None:
    text = (
        "<b>Available Commands</b>\n\n"
        "/start — Greeting and status\n"
        "/sync — Sync Strava activities\n"
        "/week [N] — This week vs plan, or a specific week: /week 3\n"
        "/next — Next 5 upcoming sessions\n"
        "/today — Today's prescribed session\n"
        "/countdown — Days to race day + current training phase\n"
        "/last — Full detail on the last activity\n"
        "/summary — Last 7 days: distance, time, pace\n"
        "/plan — Current week of training plan\n"
        "/planview — Week-by-week plan overview with km totals\n"
        "/editweek &lt;N&gt; &lt;instruction&gt; — Edit a specific week\n"
        "/setplan &lt;goal&gt; [--days=N] [--max-km=N] — Generate a new plan\n"
        "    e.g. <code>/setplan half marathon April 3 2026 in 1:21h"
        " --days=5 --max-km=70</code>\n"
        "/analyse — Analyse last activity: flags, coaching opinion &amp; debrief\n"
        "/reanalyse — Re-analyse last activity on demand\n"
        "/load — Training load: CTL/ATL/TSB + weekly km sparkline\n"
        "/readiness — Race readiness assessment\n"
        "/results — Race results\n"
        "/predict [vdot] — Predict race times from VDOT\n"
        "/pace &lt;distance&gt; &lt;time&gt; — Pace calculator + VDOT training paces\n"
        "/zones — HR and pace training zones\n"
        "/adherence [weeks] — Plan adherence score (default 4 weeks)\n"
        "/breakdown [weeks] — Volume by HR zone (default 4 weeks)\n"
        "/motivation — Get a motivational quote\n"
        "/wellness — Show injury/wellness log\n"
        "/wellness &lt;body_part&gt; &lt;1-10&gt; [notes] — Log an issue\n"
        "/wellness resolve &lt;id&gt; — Mark an issue resolved\n"
        "/sport [type] — Set activity type filter (run/ride/hike/swim/walk/all)\n"
        "/model [haiku|sonnet|opus] — Switch AI model for chat\n"
        "/memory — Show coaching memory stats\n"
        "/clear — Clear conversation history\n"
        "/help — Show this help message"
    )
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_adherence(update: object, context: object) -> None:
    args = context.args or []  # type: ignore[union-attr]
    try:
        weeks = int(args[0]) if args else 4
    except (ValueError, IndexError):
        weeks = 4
    from coach_utils.adherence import calculate_adherence

    data = await asyncio.to_thread(calculate_adherence, weeks)
    pct = data["adherence_pct"]
    honoured = data["rest_days_honoured"]
    rest_total = data["rest_days_total"]
    text = (
        f"<b>Plan Adherence ({weeks} weeks)</b>\n\n"
        f"Score: <b>{pct:.0f}%</b>\n"
        f"\u2705 Completed: {data['completed']}\n"
        f"\U0001f536 Partial: {data['partial']}\n"
        f"\u274c Missed: {data['missed']}\n"
        f"\U0001f634 Rest days honoured: "
        f"{honoured}/{rest_total}"
    )
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_breakdown(update: object, context: object) -> None:
    """Show volume-by-HR-zone breakdown for the last N weeks (default 4)."""
    args = context.args or []  # type: ignore[union-attr]
    try:
        weeks = max(1, min(int(args[0]), 52)) if args else 4
    except (ValueError, IndexError):
        weeks = 4
    sport_types = types_for_key(_cfg(context).activity_type)
    text = await asyncio.to_thread(_format_zone_breakdown, weeks, sport_types)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


async def cmd_sport(update: object, context: object) -> None:
    from tgbot.km_query import VALID_SPORT_KEYS

    config = _cfg(context)
    args = context.args  # type: ignore[union-attr]
    if not args:
        types = types_for_key(config.activity_type)
        label = sport_label(types) if types else "all activities"
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Current filter: <b>{config.activity_type}</b> ({label})\n"
            f"Change with: /sport run | ride | hike | swim | walk | all",
            parse_mode="HTML",
        )
        return
    key = args[0].lower()
    if key not in VALID_SPORT_KEYS:
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Unknown type <code>{key}</code>. Valid options: "
            + ", ".join(sorted(VALID_SPORT_KEYS)),
            parse_mode="HTML",
        )
        return
    config.activity_type = key
    await asyncio.to_thread(_save_settings, config)
    types = types_for_key(key)
    label = sport_label(types) if types else "all activities"
    await update.message.reply_text(  # type: ignore[union-attr]
        f"Activity filter set to <b>{key}</b> ({label}).\n"
        "/last, /summary, /load and /analyse now show only these activities.",
        parse_mode="HTML",
    )


_MODEL_ALIASES: dict[str, str] = {
    "haiku": HAIKU_MODEL,
    "sonnet": SONNET_MODEL,
    "opus": OPUS_MODEL,
}


async def cmd_model(update: object, context: object) -> None:
    config = _cfg(context)
    args = context.args  # type: ignore[union-attr]
    if not args:
        current = config.chat_model
        label = next((k for k, v in _MODEL_ALIASES.items() if v == current), current)
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Current model: <b>{label}</b> (<code>{current}</code>)\n"
            "Change with: /model haiku | sonnet | opus",
            parse_mode="HTML",
        )
        return
    key = args[0].lower()
    if key not in _MODEL_ALIASES:
        await update.message.reply_text(  # type: ignore[union-attr]
            f"Unknown model <code>{key}</code>. Valid options: haiku, sonnet, opus",
            parse_mode="HTML",
        )
        return
    config.chat_model = _MODEL_ALIASES[key]
    await asyncio.to_thread(_save_settings, config)
    await update.message.reply_text(  # type: ignore[union-attr]
        f"Model switched to <b>{key}</b> (<code>{config.chat_model}</code>).\n"
        "Note: plan generation always uses Sonnet regardless of this setting.",
        parse_mode="HTML",
    )


async def cmd_message(update: object, context: object) -> None:
    from tgbot.claude_chat import call_claude

    config = _cfg(context)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        await update.message.reply_text(  # type: ignore[union-attr]
            "ANTHROPIC_API_KEY not set — I can't respond to free-text messages."
        )
        return

    cid = update.effective_chat.id  # type: ignore[union-attr]
    user_text = update.message.text or ""  # type: ignore[union-attr]

    pending = config.pending_debriefs.get(cid)
    if pending:
        # Auto-expire debrief prompt after 5 minutes
        from datetime import datetime as _dt

        asked_at = pending.get("asked_at", 0)
        if _dt.now(tz=UTC).timestamp() - asked_at > 300:
            config.pending_debriefs.pop(cid, None)
            pending = None
    if pending:
        if user_text.strip().lower() == "skip":
            config.pending_debriefs.pop(cid, None)
            await update.message.reply_text("Skipped.")  # type: ignore[union-attr]
            return
        rpe, notes = parse_rpe(user_text)
        if rpe:
            await asyncio.to_thread(
                save_debrief,
                pending["activity_id"],
                pending["activity_name"],
                pending["activity_date"],
                rpe,
                notes,
            )
            from memory.store import index_debriefs
            from tgbot.debrief import load_debriefs

            await asyncio.to_thread(
                index_debriefs, await asyncio.to_thread(load_debriefs)
            )
            config.pending_debriefs.pop(cid, None)
            await update.message.reply_text(f"RPE {rpe}/10 logged.")  # type: ignore[union-attr]
            return
        # Not a valid RPE — clear the prompt and fall through to normal chat
        config.pending_debriefs.pop(cid, None)

    pending_analysis = config.pending_analysis.get(cid)
    if pending_analysis and "ready" in user_text.lower():
        logger.info("User triggered immediate analysis")
        for job in context.job_queue.get_jobs_by_name(  # type: ignore[union-attr]
            pending_analysis["job_name"]
        ):
            job.schedule_removal()
        config.pending_analysis.pop(cid, None)
        await update.message.reply_text("On it — analysing now…")  # type: ignore[union-attr]
        await _run_analysis(
            set(pending_analysis["new_act_ids"]), context, config.chat_id
        )
        return

    # Rate limit before forwarding to Claude
    now_ts = datetime.now(tz=UTC).timestamp()
    ts_deque = config.rate_timestamps.setdefault(cid, deque())
    while ts_deque and ts_deque[0] < now_ts - _RATE_WINDOW:
        ts_deque.popleft()
    if len(ts_deque) >= _RATE_LIMIT:
        await update.message.reply_text(  # type: ignore[union-attr]
            "Steady on — I can only handle a few messages per minute. "
            "Try again shortly."
        )
        return
    ts_deque.append(now_ts)

    logger.info("Forwarding message to Claude: %r", user_text[:80])

    history = config.conversation_history.setdefault(cid, [])
    history.append({"role": "user", "content": user_text})
    if len(history) > _MAX_HISTORY:
        history[:] = history[-_MAX_HISTORY:]

    try:
        reply = await asyncio.to_thread(
            call_claude, api_key, history, config.activity_type, config.chat_model
        )
    except Exception as e:
        logger.exception("Claude call failed")
        try:
            import anthropic as _anthropic

            if isinstance(e, _anthropic.RateLimitError):
                msg = (
                    "The AI service is rate-limited right now. "
                    "Please wait a minute and try again."
                )
            elif isinstance(e, _anthropic.InternalServerError):
                msg = (
                    "The AI service is temporarily overloaded. "
                    "Please try again in a moment."
                )
            elif isinstance(e, _anthropic.APIConnectionError):
                msg = (
                    "Couldn't reach the AI service — "
                    "check your internet connection and try again."
                )
            elif isinstance(e, _anthropic.APITimeoutError):
                msg = "The AI service timed out. Please try again."
            elif isinstance(e, _anthropic.AuthenticationError):
                msg = "AI service authentication failed — check your API key."
            elif isinstance(e, _anthropic.AnthropicError):
                msg = "The AI service returned an error. Please try again shortly."
            else:
                msg = "Sorry, something went wrong. Please try again."
        except ImportError:
            msg = "Sorry, something went wrong. Please try again."
        await update.message.reply_text(msg)  # type: ignore[union-attr]
        return

    if not reply:
        await update.message.reply_text(  # type: ignore[union-attr]
            "The AI returned an empty response — please try again."
        )
        return

    history.append({"role": "assistant", "content": reply})
    await asyncio.to_thread(_save_history, config)
    await update.message.reply_text(reply)  # type: ignore[union-attr]
