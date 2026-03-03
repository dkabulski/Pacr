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
    _generate_plan_with_claude,
)
from tgbot.debrief import parse_rpe, save_debrief
from tgbot.formatters import (
    _format_last_activity,
    _format_next_sessions,
    _format_plan_summary,
    _format_results,
    _format_status,
    _format_today_session,
    _format_training_load,
    _format_week_vs_plan,
    _format_weekly_summary,
    _format_zones,
    _today_session,
    _weekly_summary,
)
from tgbot.km_query import (
    sport_label,
    types_for_key,
)

logger = logging.getLogger("pacr")

_MAX_HISTORY = 20  # individual messages (~10 conversational turns)
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


def _save_settings(config: BotConfig) -> None:
    """Persist bot settings to disk."""
    import _token_utils

    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_utils.DATA_DIR / "settings.json"
    path.write_text(json.dumps({"activity_type": config.activity_type}, indent=2))


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

    # Rules-based flags analysis
    act_results: list[tuple[dict, dict]] = []
    notes: list[str] = []
    for act in new_acts:
        result = await asyncio.to_thread(analyze._analyze_activity, act)
        act_results.append((act, result))
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
        if flags:
            notes.append(header + "\n" + "\n".join(f"  ⚠ {f}" for f in flags))
        else:
            notes.append(f"{header} — on target.")
    await context.bot.send_message(  # type: ignore[union-attr]
        chat_id=chat_id,
        text="<b>Activity analysis:</b>\n\n" + "\n\n".join(notes),
        parse_mode="HTML",
    )

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
        for act, result in act_results:
            # Fetch description (not available from list API)
            logger.info("Fetching description for activity %s", act["id"])
            desc = await asyncio.to_thread(strava_sync._fetch_description, act["id"])
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
            prompt = "\n".join(lines)
            logger.info("Requesting coaching opinion from Claude (%s)", CLAUDE_MODEL)
            try:
                msg = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=180,
                    system=(
                        "You are a direct, experienced running coach. "
                        "Give a 2–3 sentence coaching reaction to this activity. "
                        "Be specific to the numbers, honest, and concise. "
                        "Don't shy away from humour"
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )
                opinion = next((b.text for b in msg.content if hasattr(b, "text")), "")
                if opinion:
                    logger.info("Coaching opinion sent (%d chars)", len(opinion))
                    await context.bot.send_message(  # type: ignore[union-attr]
                        chat_id=chat_id, text=opinion
                    )
            except Exception:
                logger.exception("Coaching opinion failed — continuing")

    act = new_acts[0]
    config.pending_debriefs[int(chat_id)] = {
        "activity_id": act["id"],
        "activity_name": act.get("name", "Run"),
        "activity_date": act.get("date", "")[:10],
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
        from memory.store import index_activities, index_debriefs
        from tgbot.debrief import load_debriefs

        indexed = await asyncio.to_thread(index_activities, activities)
        debriefs = await asyncio.to_thread(load_debriefs)
        await asyncio.to_thread(index_debriefs, debriefs)
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
            "Usage: /setplan &lt;goal&gt;\n"
            "e.g. <code>/setplan half marathon on April 3 2026 in 1:21h</code>",
            parse_mode="HTML",
        )
        return

    goal = " ".join(context.args)  # type: ignore[union-attr]
    await update.message.reply_text("Generating your plan...")  # type: ignore[union-attr]

    try:
        plan_dict = await asyncio.to_thread(_generate_plan_with_claude, goal)
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
    text = await asyncio.to_thread(_format_week_vs_plan)
    await update.message.reply_text(text, parse_mode="HTML")  # type: ignore[union-attr]


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
    await update.message.reply_text(  # type: ignore[union-attr]
        _format_last_activity(activities[0]), parse_mode="HTML"
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


async def cmd_help(update: object, context: object) -> None:
    text = (
        "<b>Available Commands</b>\n\n"
        "/start — Greeting and status\n"
        "/sync — Sync Strava activities\n"
        "/week — This week's plan vs completed sessions\n"
        "/next — Next 5 upcoming sessions\n"
        "/today — Today's prescribed session\n"
        "/last — Full detail on the last activity\n"
        "/summary — Last 7 days: distance, time, pace\n"
        "/plan — Training plan overview\n"
        "/setplan &lt;goal&gt; — Generate a new training plan with AI\n"
        "    e.g. <code>/setplan half marathon on April 3 2026 in 1:21h</code>\n"
        "/analyse — Analyse last activity: flags, coaching opinion &amp; debrief\n"
        "/reanalyse — Re-analyse last activity on demand\n"
        "/load — Training load: CTL/ATL/TSB + weekly km\n"
        "/results — Race results\n"
        "/zones — HR and pace training zones\n"
        "/adherence [weeks] — Plan adherence score (default 4 weeks)\n"
        "/sport [type] — Set activity type filter (run/ride/hike/swim/walk/all)\n"
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
        await update.message.reply_text(  # type: ignore[union-attr]
            "Please reply with 1–10 or <code>skip</code>.", parse_mode="HTML"
        )
        return

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
            "Steady on \u2014 I can only handle a few messages per minute. "
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
            call_claude, api_key, history, config.activity_type
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
