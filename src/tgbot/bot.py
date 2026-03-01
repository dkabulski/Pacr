# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "python-telegram-bot[job-queue]>=21.0",
#     "fire>=0.7",
#     "python-dotenv>=1.0",
#     "requests>=2.32",
#     "beautifulsoup4>=4.12",
#     "anthropic>=0.30",
# ]
# ///
"""Telegram integration — send notifications and interactive bot."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

# When run as a uv script, add src/ to sys.path so sibling modules resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fire
from dotenv import load_dotenv

load_dotenv()

STRAVA_POLL_INTERVAL = int(os.environ.get("STRAVA_POLL_INTERVAL", "180"))
STRAVA_ANALYSIS_DELAY = int(os.environ.get("STRAVA_ANALYSIS_DELAY", "600"))  # 10 min

# Re-export from submodules so tests can import via `tgbot.bot.*`
from tgbot.context import (  # noqa: E402
    CLAUDE_MODEL,
    _build_athlete_context,
    _calculate_vdot,
    _compute_goal_pace,
    _generate_plan_with_claude,
    _vdot_paces,
)
from tgbot.debrief import parse_rpe, save_debrief  # noqa: E402
from tgbot.km_query import (  # noqa: E402
    compute_km,
    describe_period,
    is_km_query,
    parse_period,
    parse_sport,
    sport_label,
)
from tgbot.formatters import (  # noqa: E402
    _format_activity_summary,
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

TELEGRAM_MAX_LENGTH = 4096

_conversation_history: dict[int, list[dict]] = {}
_MAX_HISTORY = 20  # individual messages (~10 conversational turns)
_BLOCKED_FILES = {"tokens.json"}  # never exposed to the model
_pending_debriefs: dict[int, dict] = {}
# {chat_id: {"activity_id": int, "activity_name": str, "activity_date": str}}


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------


def _load_history() -> None:
    """Populate _conversation_history from disk (called once at bot startup)."""
    import _token_utils
    path = _token_utils.DATA_DIR / "conversation_history.json"
    if not path.exists():
        return
    data = json.loads(path.read_text())
    for k, v in data.items():
        _conversation_history[int(k)] = v


def _save_history() -> None:
    """Persist _conversation_history to disk."""
    import _token_utils
    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_utils.DATA_DIR / "conversation_history.json"
    path.write_text(
        json.dumps({str(k): v for k, v in _conversation_history.items()}, indent=2)
    )


# ---------------------------------------------------------------------------
# Config and transport
# ---------------------------------------------------------------------------


def _get_bot_config() -> tuple[str, str]:
    """Read TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.

    Returns:
        (bot_token, chat_id) tuple.

    Raises:
        RuntimeError: If either variable is missing.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN not set. "
            "Create a bot via @BotFather and add the token to .env"
        )
    if not chat_id:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID not set. "
            "Message your bot, then fetch your chat ID from "
            "https://api.telegram.org/bot<TOKEN>/getUpdates"
        )
    return token, chat_id


def _send_telegram_message(text: str, parse_mode: str = "HTML") -> dict:
    """POST a message to Telegram via urllib (no library needed).

    Truncates at 4096 chars (Telegram limit).
    Returns the API response as a dict.
    """
    token, chat_id = _get_bot_config()
    if len(text) > TELEGRAM_MAX_LENGTH:
        text = text[: TELEGRAM_MAX_LENGTH - 3] + "..."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Post-sync analysis
# ---------------------------------------------------------------------------


def _auto_analyze_new_activities(before_ids: set[int]) -> str | None:
    """Compare cached activities before and after a sync.

    Returns a coaching note if any new activities warrant feedback, else None.
    """
    import analyze
    import strava_sync

    after = strava_sync._load_cached()
    new_acts = [a for a in after if a["id"] not in before_ids]
    if not new_acts:
        return None

    notes: list[str] = []
    for act in new_acts:
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

    return "\n\n".join(notes) if notes else None


# ---------------------------------------------------------------------------
# Fire commands
# ---------------------------------------------------------------------------


def send(text: str) -> None:
    """Send a message to Telegram."""
    result = _send_telegram_message(text)
    if result.get("ok"):
        print("Message sent.")
    else:
        print(f"Error: {result}")


def send_summary(period: str = "daily") -> None:
    """Send a summary to Telegram.

    Args:
        period: "daily" for latest activity, "weekly" for 7-day stats.
    """
    if period == "weekly":
        summary = _weekly_summary()
        text = _format_weekly_summary(summary)
    else:
        import strava_sync

        activities = strava_sync._load_cached()
        if not activities:
            text = "No activities cached. Run a sync first."
        else:
            text = _format_activity_summary(activities[0])

    _send_telegram_message(text)
    print(f"Sent {period} summary.")


def bot() -> None:
    """Start the interactive Telegram bot (long-polling)."""
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    token, chat_id = _get_bot_config()
    chat_filter = filters.Chat(chat_id=int(chat_id))

    async def _heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
        import strava_sync
        before_ids = {a["id"] for a in await asyncio.to_thread(strava_sync._load_cached)}
        # Only fetch recent days — no need to pull full history on every tick
        await asyncio.to_thread(strava_sync.sync, 3)
        after = await asyncio.to_thread(strava_sync._load_cached)
        new_acts = [a for a in after if a["id"] not in before_ids]
        if new_acts:
            # Acknowledge immediately but defer analysis so the user has time to
            # edit the title, description, gear etc. in Strava before we analyse.
            names = ", ".join(a.get("name", "activity") for a in new_acts)
            delay_min = STRAVA_ANALYSIS_DELAY // 60
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"<b>New activity detected:</b> {names}.\n"
                     f"I'll analyse it in {delay_min} min — edit away in Strava first.",
                parse_mode="HTML",
            )
            context.job_queue.run_once(
                _deferred_analysis,
                when=STRAVA_ANALYSIS_DELAY,
                data={"new_act_ids": [a["id"] for a in new_acts]},
                name=f"deferred_{new_acts[0]['id']}",
            )

    async def _deferred_analysis(context: ContextTypes.DEFAULT_TYPE) -> None:
        import analyze
        import strava_sync
        new_act_ids: set[int] = set(context.job.data["new_act_ids"])  # type: ignore[union-attr]
        # Re-sync to pick up any title / description edits made since detection
        await asyncio.to_thread(strava_sync.sync, 7)
        activities = await asyncio.to_thread(strava_sync._load_cached)
        new_acts = [a for a in activities if a["id"] in new_act_ids]
        if not new_acts:
            return
        notes: list[str] = []
        for act in new_acts:
            result = await asyncio.to_thread(analyze._analyze_activity, act)
            flags = result.get("flags", [])
            dist = act.get("distance_km", 0)
            pace = act.get("pace", "N/A")
            name = act.get("name", "Run")
            header = f"{name} — {dist:.1f}km @ {pace}/km"
            if flags:
                notes.append(header + "\n" + "\n".join(f"  ⚠ {f}" for f in flags))
            else:
                notes.append(f"{header} — on target.")
        await context.bot.send_message(
            chat_id=chat_id,
            text="<b>Activity analysis:</b>\n\n" + "\n\n".join(notes),
            parse_mode="HTML",
        )
        act = new_acts[0]
        _pending_debriefs[int(chat_id)] = {
            "activity_id": act["id"],
            "activity_name": act.get("name", "Run"),
            "activity_date": act.get("date", "")[:10],
        }
        await context.bot.send_message(
            chat_id=chat_id,
            text="How did that feel? Reply with RPE 1–10 … or <code>skip</code>.",
            parse_mode="HTML",
        )

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        status = await asyncio.to_thread(_format_status)
        await update.message.reply_text(
            f"Hello! I'm RunWhisperer, your running coach.\n\n{status}",
            parse_mode="HTML",
        )

    async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Syncing Strava activities...")
        import strava_sync

        try:
            before_ids = {a["id"] for a in strava_sync._load_cached()}
            await asyncio.to_thread(strava_sync.sync, 365)
            activities = await asyncio.to_thread(strava_sync._load_cached)
            await update.message.reply_text(
                f"Sync complete. {len(activities)} activities cached.",
                parse_mode="HTML",
            )
            note = await asyncio.to_thread(_auto_analyze_new_activities, before_ids)
            if note:
                await update.message.reply_text(f"<b>New activity analysis:</b>\n\n{note}", parse_mode="HTML")
            new_acts = [a for a in activities if a["id"] not in before_ids]
            if note and new_acts:
                act = new_acts[0]
                cid = update.effective_chat.id
                _pending_debriefs[cid] = {
                    "activity_id": act["id"],
                    "activity_name": act.get("name", "Run"),
                    "activity_date": act.get("date", "")[:10],
                }
                await update.message.reply_text(
                    "How did that feel? Reply with RPE 1–10 … or <code>skip</code>.",
                    parse_mode="HTML",
                )
        except Exception as e:
            await update.message.reply_text(f"Sync failed: {e}")

    async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import plan as plan_mod

        p = await asyncio.to_thread(plan_mod._load_plan)
        if p:
            text = _format_plan_summary(p)
        else:
            text = "No training plan set. Ask your coach to create one."
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_setplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text(
                "Usage: /setplan &lt;goal&gt;\n"
                "e.g. <code>/setplan half marathon on April 3 2026 in 1:21h</code>",
                parse_mode="HTML",
            )
            return

        goal = " ".join(context.args)
        await update.message.reply_text("Generating your plan...")

        try:
            plan_dict = await asyncio.to_thread(_generate_plan_with_claude, goal)
        except Exception as e:
            await update.message.reply_text(f"Failed to generate plan: {e}")
            return

        import plan as plan_mod

        try:
            await asyncio.to_thread(plan_mod._save_plan, plan_dict)
        except Exception as e:
            await update.message.reply_text(f"Failed to save plan: {e}")
            return

        summary = _format_plan_summary(plan_dict)
        await update.message.reply_text(f"Plan saved!\n\n{summary}", parse_mode="HTML")

    async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        session = await asyncio.to_thread(_today_session)
        text = _format_today_session(session)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import analyze
        import strava_sync

        activities = await asyncio.to_thread(strava_sync._load_cached)
        if not activities:
            await update.message.reply_text("No activities cached. Run /sync first.")
            return

        result = await asyncio.to_thread(analyze._analyze_activity, activities[0])
        text = _format_activity_summary(activities[0])
        flags = result.get("flags", [])
        recs = result.get("recommendations", [])
        if flags:
            text += "\n\n<b>Flags:</b>\n" + "\n".join(f"- {f}" for f in flags)
        if recs:
            text += "\n\n<b>Recommendations:</b>\n" + "\n".join(f"- {r}" for r in recs)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import pot10

        results = await asyncio.to_thread(pot10._load_results)
        text = _format_results(results)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = await asyncio.to_thread(_format_week_vs_plan)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = await asyncio.to_thread(_format_next_sessions)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import strava_sync

        activities = await asyncio.to_thread(strava_sync._load_cached)
        if not activities:
            await update.message.reply_text("No activities cached. Run /sync first.")
            return
        await update.message.reply_text(
            _format_last_activity(activities[0]), parse_mode="HTML"
        )

    async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        summary = await asyncio.to_thread(_weekly_summary)
        await update.message.reply_text(
            _format_weekly_summary(summary), parse_mode="HTML"
        )

    async def cmd_zones(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = await asyncio.to_thread(_format_zones)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_load(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import strava_sync
        from training_load import calculate_load_metrics, volume_spike_check, weekly_km_trend

        activities = await asyncio.to_thread(strava_sync._load_cached)
        metrics = await asyncio.to_thread(calculate_load_metrics, activities)
        trend = await asyncio.to_thread(weekly_km_trend, activities)
        spike = await asyncio.to_thread(volume_spike_check, activities)
        text = _format_training_load(metrics, trend)
        if spike:
            text += f"\n\n⚠ {spike}"
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cid = update.effective_chat.id
        _conversation_history.pop(cid, None)
        await asyncio.to_thread(_save_history)
        await update.message.reply_text("Conversation history cleared.")

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            "/analyze — Analyse latest activity against plan &amp; zones\n"
            "/load — Training load: CTL/ATL/TSB + weekly km\n"
            "/results — Race results\n"
            "/zones — HR and pace training zones\n"
            "/clear — Clear conversation history\n"
            "/help — Show this help message"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            await update.message.reply_text(
                "ANTHROPIC_API_KEY not set — I can't respond to free-text messages."
            )
            return

        cid = update.effective_chat.id
        user_text = update.message.text or ""

        pending = _pending_debriefs.get(cid)
        if pending:
            if user_text.strip().lower() == "skip":
                _pending_debriefs.pop(cid, None)
                await update.message.reply_text("Skipped.")
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
                _pending_debriefs.pop(cid, None)
                await update.message.reply_text(f"RPE {rpe}/10 logged.")
                return
            await update.message.reply_text(
                "Please reply with 1–10 or <code>skip</code>.", parse_mode="HTML"
            )
            return

        if is_km_query(user_text):
            period = parse_period(user_text)
            if period:
                import strava_sync

                start, end = period
                types = parse_sport(user_text)
                activities = await asyncio.to_thread(strava_sync._load_cached)
                result = await asyncio.to_thread(compute_km, activities, start, end, types)
                label = describe_period(start, end)
                km, count = result["total_km"], result["count"]
                word = sport_label(types)
                s = "s" if count != 1 else ""
                reply = f"You logged <b>{km:.1f} km</b> across <b>{count} {word}{s}</b> {label}."
                await update.message.reply_text(reply, parse_mode="HTML")
                return
        # (falls through to Claude if period not recognised)

        history = _conversation_history.setdefault(cid, [])
        history.append({"role": "user", "content": user_text})
        if len(history) > _MAX_HISTORY:
            history[:] = history[-_MAX_HISTORY:]

        system_prompt = await asyncio.to_thread(_build_athlete_context)

        tools = [
            {
                "name": "sync_strava",
                "description": (
                    "Fetch the athlete's latest Strava activities and update the "
                    "local cache. Call this when the athlete asks to sync, refresh, "
                    "update, or fetch their Strava data or recent runs."
                ),
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "save_plan",
                "description": (
                    "Save a modified training plan to disk. Use this when the athlete "
                    "asks to change, adjust, move, or update any part of their plan. "
                    "Construct the complete modified plan — preserving all unchanged "
                    "weeks — and call this tool to persist it."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "plan": {
                            "type": "object",
                            "description": "Complete training plan with 'goal' and 'weeks' array.",
                        }
                    },
                    "required": ["plan"],
                },
            },
            {
                "name": "list_data_files",
                "description": (
                    "List the available data files in the data directory. "
                    "Call this to discover what files exist before reading one."
                ),
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "read_data_file",
                "description": (
                    "Read the contents of a data file by name (e.g. 'athlete.json', "
                    "'training_log.json'). Use list_data_files first if unsure what exists. "
                    "tokens.json is never accessible."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Filename to read, e.g. 'athlete.json'",
                        }
                    },
                    "required": ["filename"],
                },
            },
            {
                "name": "lookup_activities",
                "description": (
                    "Search cached Strava activities by date range. Use this when "
                    "the athlete asks about a specific past run, a particular date, "
                    "their longest run, fastest pace, or any historical session "
                    "detail beyond the last four weeks."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "date_from": {
                            "type": "string",
                            "description": "Start date YYYY-MM-DD (inclusive)",
                        },
                        "date_to": {
                            "type": "string",
                            "description": "End date YYYY-MM-DD (inclusive)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max activities to return (default 10)",
                        },
                    },
                    "required": [],
                },
            },
        ]

        def _execute_tools(msg: object, messages: list) -> list:
            """Run all tool calls in msg and return tool_results list."""
            import strava_sync
            tool_results = []
            for block in msg.content:  # type: ignore[union-attr]
                if block.type != "tool_use":
                    continue
                if block.name == "list_data_files":
                    import _token_utils
                    available = [
                        f.name for f in sorted(_token_utils.DATA_DIR.iterdir())
                        if f.is_file() and f.name not in _BLOCKED_FILES
                    ]
                    result = "Available files: " + ", ".join(available) if available else "No data files found."
                elif block.name == "read_data_file":
                    import _token_utils
                    filename = block.input.get("filename", "")
                    if filename in _BLOCKED_FILES:
                        result = f"{filename} is not accessible."
                    else:
                        path = _token_utils.DATA_DIR / filename
                        if not path.exists():
                            result = f"{filename} not found."
                        else:
                            result = path.read_text()
                elif block.name == "save_plan":
                    import plan as plan_mod
                    try:
                        plan_dict = block.input.get("plan", {})
                        if not isinstance(plan_dict, dict) or "weeks" not in plan_dict:
                            result = "Invalid plan — must contain a 'weeks' array."
                        else:
                            plan_mod._save_plan(plan_dict)
                            result = f"Plan saved ({len(plan_dict['weeks'])} weeks)."
                    except Exception as e:
                        result = f"Failed to save plan: {e}"
                elif block.name == "sync_strava":
                    try:
                        before_ids = {a["id"] for a in strava_sync._load_cached()}
                        strava_sync.sync(365)
                        note = _auto_analyze_new_activities(before_ids)
                        result = "Sync complete. Activities cache updated."
                        if note:
                            result += f"\n\nNew activity analysis:\n{note}"
                    except Exception as e:
                        result = f"Sync failed: {e}"
                elif block.name == "lookup_activities":
                    inp = block.input
                    date_from = inp.get("date_from", "")
                    date_to = inp.get("date_to", "9999-12-31")
                    limit = int(inp.get("limit", 10))
                    acts = [
                        a for a in strava_sync._load_cached()
                        if date_from <= a.get("date", "")[:10] <= date_to
                    ][:limit]
                    if not acts:
                        result = "No activities found for that date range."
                    else:
                        rows = []
                        for a in acts:
                            date = a.get("date", "")[:10]
                            dist = a.get("distance_km", 0)
                            pace = a.get("pace", "N/A")
                            hr = a.get("avg_hr")
                            hr_str = f", HR {hr:.0f} bpm" if hr else ""
                            s = a.get("moving_time_s", 0)
                            t = f"{s//3600}h{(s%3600)//60:02d}m" if s >= 3600 else f"{s//60}:{s%60:02d}"
                            elev = a.get("elevation_m")
                            elev_str = f", elev {elev:.0f}m" if elev else ""
                            rows.append(
                                f"{date} — {a.get('name','Run')}: "
                                f"{dist:.2f}km in {t} @ {pace}/km{hr_str}{elev_str}."
                            )
                        result = "\n".join(rows)
                else:
                    result = f"Unknown tool: {block.name}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
            return tool_results

        def _call() -> str:
            client = anthropic.Anthropic(api_key=api_key)
            messages = list(history)
            cached_system = [
                {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
            ]

            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1500,
                system=cached_system,
                messages=messages,
                tools=tools,
            )

            for _ in range(5):  # max tool-call rounds
                if msg.stop_reason != "tool_use":
                    break
                tool_results = _execute_tools(msg, messages)
                messages = messages + [
                    {"role": "assistant", "content": msg.content},
                    {"role": "user", "content": tool_results},
                ]
                msg = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=1500,
                    system=cached_system,
                    messages=messages,
                    tools=tools,
                )

            return next((b.text for b in msg.content if hasattr(b, "text")), "")

        try:
            reply = await asyncio.to_thread(_call)
        except Exception as e:
            await update.message.reply_text(f"Sorry, something went wrong: {e}")
            return

        if not reply:
            await update.message.reply_text("Sorry, I couldn't generate a response.")
            return

        history.append({"role": "assistant", "content": reply})
        await asyncio.to_thread(_save_history)
        await update.message.reply_text(reply)

    from telegram import BotCommand

    async def _register_commands(application: "Application") -> None:  # type: ignore[name-defined]
        await application.bot.set_my_commands([
            BotCommand("start",   "Show status overview"),
            BotCommand("sync",    "Sync Strava activities"),
            BotCommand("today",   "Today's planned session"),
            BotCommand("week",    "This week vs plan"),
            BotCommand("next",    "Next 5 planned sessions"),
            BotCommand("last",    "Last activity detail"),
            BotCommand("summary", "7-day stats"),
            BotCommand("plan",    "Show full training plan"),
            BotCommand("setplan", "Generate a new plan with AI"),
            BotCommand("analyze", "Analyse recent sessions"),
            BotCommand("load",    "Training load: CTL/ATL/TSB + weekly km"),
            BotCommand("results", "Race results"),
            BotCommand("zones",   "HR and pace zones"),
            BotCommand("clear",   "Clear conversation history"),
            BotCommand("help",    "Show available commands"),
        ])

    _load_history()
    print(f"Starting bot (chat_id={chat_id})...")
    app = ApplicationBuilder().token(token).post_init(_register_commands).build()
    app.add_handler(CommandHandler("start", cmd_start, filters=chat_filter))
    app.add_handler(CommandHandler("sync", cmd_sync, filters=chat_filter))
    app.add_handler(CommandHandler("week", cmd_week, filters=chat_filter))
    app.add_handler(CommandHandler("next", cmd_next, filters=chat_filter))
    app.add_handler(CommandHandler("today", cmd_today, filters=chat_filter))
    app.add_handler(CommandHandler("last", cmd_last, filters=chat_filter))
    app.add_handler(CommandHandler("summary", cmd_summary, filters=chat_filter))
    app.add_handler(CommandHandler("plan", cmd_plan, filters=chat_filter))
    app.add_handler(CommandHandler("setplan", cmd_setplan, filters=chat_filter))
    app.add_handler(CommandHandler("analyze", cmd_analyze, filters=chat_filter))
    app.add_handler(CommandHandler("load", cmd_load, filters=chat_filter))
    app.add_handler(CommandHandler("results", cmd_results, filters=chat_filter))
    app.add_handler(CommandHandler("zones", cmd_zones, filters=chat_filter))
    app.add_handler(CommandHandler("clear", cmd_clear, filters=chat_filter))
    app.add_handler(CommandHandler("help", cmd_help, filters=chat_filter))
    app.add_handler(
        MessageHandler(chat_filter & filters.TEXT & ~filters.COMMAND, cmd_message)
    )
    app.job_queue.run_repeating(_heartbeat, interval=STRAVA_POLL_INTERVAL, first=60)
    app.run_polling()


def morning_briefing() -> None:
    """Send today's session and week progress as a morning briefing message."""
    import plan as plan_mod

    p = plan_mod._load_plan()
    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    # Today's session
    today_session: dict | None = None
    if p:
        for week in p.get("weeks", []):
            for session in week.get("sessions", []):
                if session.get("date", "") == today_str:
                    today_session = session
                    break

    if today_session:
        stype = today_session.get("type", "")
        desc = today_session.get("description", "")
        today_block = f"<b>Today ({today_str})</b>\n→ {stype}: {desc}"
    else:
        today_block = f"<b>Today ({today_str})</b>\nNo session planned — rest day."

    week_block = _format_week_vs_plan()

    text = f"{today_block}\n\n{week_block}"
    _send_telegram_message(text)
    print("Sent morning briefing.")


if __name__ == "__main__":
    fire.Fire({"send": send, "send_summary": send_summary, "morning_briefing": morning_briefing, "bot": bot})
