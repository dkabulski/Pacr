# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "python-telegram-bot[job-queue]>=21.0",
#     "fire>=0.7",
#     "python-dotenv>=1.0",
#     "requests>=2.32",
#     "beautifulsoup4>=4.12",
#     "anthropic>=0.30",
#     "chromadb>=0.6",
# ]
# ///
"""Telegram integration — send notifications and interactive bot."""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from datetime import time as dt_time
from pathlib import Path

# When run as a uv script, add src/ to sys.path so sibling modules resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fire
from dotenv import load_dotenv

load_dotenv()

import _token_utils  # noqa: E402

_token_utils.configure_logging()
logger = logging.getLogger("pacr")

STRAVA_POLL_INTERVAL = int(os.environ.get("STRAVA_POLL_INTERVAL", "1800"))
STRAVA_ANALYSIS_DELAY = int(os.environ.get("STRAVA_ANALYSIS_DELAY", "600"))  # 10 min
MORNING_CHECKIN_HOUR = int(os.environ.get("MORNING_CHECKIN_HOUR", "8"))
WEEKLY_DEBRIEF_HOUR = int(os.environ.get("WEEKLY_DEBRIEF_HOUR", "19"))
STRAVA_WEBHOOK_PORT = int(os.environ.get("STRAVA_WEBHOOK_PORT", "0"))

# Re-export from submodules so tests can import via `tgbot.bot.*`
from tgbot.claude_chat import (  # noqa: E402, F401
    TOOLS,
    call_claude,
    execute_tools,
)
from tgbot.context import (  # noqa: E402, F401
    CLAUDE_MODEL,
    _build_athlete_context,
    _calculate_vdot,
    _compute_goal_pace,
    _generate_plan_with_claude,
    _vdot_paces,
)
from tgbot.debrief import parse_rpe, save_debrief  # noqa: E402, F401
from tgbot.formatters import (  # noqa: E402, F401
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
from tgbot.handlers import (  # noqa: E402, F401
    _BLOCKED_FILES,
    _MAX_CHATS,
    _MAX_HISTORY,
    _RATE_LIMIT,
    _RATE_WINDOW,
    BotConfig,
    _auto_analyse_new_activities,
    _deferred_analysis,
    _filter_by_sport,
    _heartbeat,
    _load_history,
    _load_settings,
    _run_analysis,
    _save_history,
    _save_settings,
    _validate_data_path,
    cmd_adherence,
    cmd_analyse,
    cmd_breakdown,
    cmd_clear,
    cmd_countdown,
    cmd_edit_week,
    cmd_help,
    cmd_last,
    cmd_load,
    cmd_message,
    cmd_model,
    cmd_motivation,
    cmd_next,
    cmd_pace,
    cmd_plan,
    cmd_plan_overview,
    cmd_predict,
    cmd_readiness,
    cmd_results,
    cmd_setplan,
    cmd_sport,
    cmd_start,
    cmd_summary,
    cmd_sync,
    cmd_today,
    cmd_week,
    cmd_wellness,
    cmd_zones,
    morning_checkin,
    weekly_debrief,
)
from tgbot.km_query import (  # noqa: E402, F401
    SPORT_KEY_MAP,
    VALID_SPORT_KEYS,
    compute_km,
    describe_period,
    is_km_query,
    parse_period,
    parse_sport,
    sport_label,
    types_for_key,
)
from tgbot.telegram_send import (  # noqa: E402, F401
    TELEGRAM_MAX_LENGTH,
    _get_bot_config,
    _send_telegram_message,
)

# Legacy module-level aliases for backwards compatibility with tests
_conversation_history: dict[int, list[dict]] = {}
_pending_debriefs: dict[int, dict] = {}
_pending_analysis: dict[int, dict] = {}
_rate_timestamps: dict = {}


# ---------------------------------------------------------------------------
# Fire commands
# ---------------------------------------------------------------------------


def send(text: str) -> None:
    """Send a message to Telegram."""
    result = _send_telegram_message(text)
    if result.get("ok"):
        logger.info("Message sent.")
    else:
        logger.error("Failed to send message: %s", result)


def send_summary(period: str = "daily") -> None:
    """Send a summary to Telegram.

    Args:
        period: "daily" for latest activity, "weekly" for 7-day stats.
    """
    if period == "weekly":
        summary = _weekly_summary()
        text = _format_weekly_summary(summary)
    else:
        from strava_utils import strava_sync

        activities = strava_sync._load_cached()
        if not activities:
            text = "No activities cached. Run a sync first."
        else:
            text = _format_activity_summary(activities[0])

    _send_telegram_message(text)
    logger.info("Sent %s summary.", period)


def bot() -> None:
    """Start the interactive Telegram bot (long-polling)."""
    from telegram import BotCommand
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        MessageHandler,
        filters,
    )

    token, chat_id = _get_bot_config()
    chat_filter = filters.Chat(chat_id=int(chat_id))

    config = BotConfig(chat_id=chat_id)
    _load_history(config)
    _load_settings(config)

    async def _register_commands(application: object) -> None:
        await application.bot.set_my_commands(
            [  # type: ignore[union-attr]
                BotCommand("start", "Show status overview"),
                BotCommand("sync", "Sync Strava activities"),
                BotCommand("today", "Today's planned session"),
                BotCommand("countdown", "Days to race day"),
                BotCommand("week", "This week vs plan"),
                BotCommand("next", "Next 5 planned sessions"),
                BotCommand("last", "Last activity detail"),
                BotCommand("summary", "7-day stats"),
                BotCommand("plan", "Show current week of training plan"),
                BotCommand("planview", "Week-by-week plan overview with km totals"),
                BotCommand("editweek", "Edit a specific week with natural language"),
                BotCommand("setplan", "Generate a new plan with AI"),
                BotCommand("analyse", "Analyse last activity"),
                BotCommand("reanalyse", "Re-analyse last activity on demand"),
                BotCommand("load", "Training load: CTL/ATL/TSB + weekly km"),
                BotCommand("readiness", "Race readiness assessment"),
                BotCommand("results", "Race results"),
                BotCommand("predict", "Predict race times from VDOT"),
                BotCommand("pace", "Pace calculator + training zones"),
                BotCommand("zones", "HR and pace zones"),
                BotCommand("adherence", "Plan adherence score"),
                BotCommand("breakdown", "Volume by HR zone (last N weeks)"),
                BotCommand("motivation", "Get a motivational quote"),
                BotCommand("wellness", "Injury and wellness log"),
                BotCommand("sport", "Set activity type filter"),
                BotCommand("model", "Switch AI model (haiku/sonnet/opus)"),
                BotCommand("clear", "Clear conversation history"),
                BotCommand("help", "Show available commands"),
            ]
        )

    logger.info(
        "Starting bot (chat_id=%s, poll_interval=%ss, analysis_delay=%ss)",
        chat_id,
        STRAVA_POLL_INTERVAL,
        STRAVA_ANALYSIS_DELAY,
    )
    app = ApplicationBuilder().token(token).post_init(_register_commands).build()
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", cmd_start, filters=chat_filter))
    app.add_handler(CommandHandler("sync", cmd_sync, filters=chat_filter))
    app.add_handler(CommandHandler("week", cmd_week, filters=chat_filter))
    app.add_handler(CommandHandler("next", cmd_next, filters=chat_filter))
    app.add_handler(CommandHandler("today", cmd_today, filters=chat_filter))
    app.add_handler(CommandHandler("countdown", cmd_countdown, filters=chat_filter))
    app.add_handler(CommandHandler("last", cmd_last, filters=chat_filter))
    app.add_handler(CommandHandler("summary", cmd_summary, filters=chat_filter))
    app.add_handler(CommandHandler("plan", cmd_plan, filters=chat_filter))
    app.add_handler(CommandHandler("planview", cmd_plan_overview, filters=chat_filter))
    app.add_handler(CommandHandler("editweek", cmd_edit_week, filters=chat_filter))
    app.add_handler(CommandHandler("setplan", cmd_setplan, filters=chat_filter))
    app.add_handler(CommandHandler("analyse", cmd_analyse, filters=chat_filter))
    app.add_handler(CommandHandler("reanalyse", cmd_analyse, filters=chat_filter))
    app.add_handler(CommandHandler("load", cmd_load, filters=chat_filter))
    app.add_handler(CommandHandler("readiness", cmd_readiness, filters=chat_filter))
    app.add_handler(CommandHandler("results", cmd_results, filters=chat_filter))
    app.add_handler(CommandHandler("predict", cmd_predict, filters=chat_filter))
    app.add_handler(CommandHandler("pace", cmd_pace, filters=chat_filter))
    app.add_handler(CommandHandler("zones", cmd_zones, filters=chat_filter))
    app.add_handler(CommandHandler("sport", cmd_sport, filters=chat_filter))
    app.add_handler(CommandHandler("model", cmd_model, filters=chat_filter))
    app.add_handler(CommandHandler("adherence", cmd_adherence, filters=chat_filter))
    app.add_handler(CommandHandler("breakdown", cmd_breakdown, filters=chat_filter))
    app.add_handler(CommandHandler("motivation", cmd_motivation, filters=chat_filter))
    app.add_handler(CommandHandler("wellness", cmd_wellness, filters=chat_filter))
    app.add_handler(CommandHandler("clear", cmd_clear, filters=chat_filter))
    app.add_handler(CommandHandler("help", cmd_help, filters=chat_filter))
    app.add_handler(
        MessageHandler(chat_filter & filters.TEXT & ~filters.COMMAND, cmd_message)
    )
    app.job_queue.run_repeating(_heartbeat, interval=STRAVA_POLL_INTERVAL, first=60)
    app.job_queue.run_daily(
        morning_checkin,
        time=dt_time(hour=MORNING_CHECKIN_HOUR, minute=0, tzinfo=UTC),
        name="morning_checkin",
    )
    logger.info("Morning check-in scheduled at %02d:00 UTC", MORNING_CHECKIN_HOUR)
    app.job_queue.run_daily(
        weekly_debrief,
        time=dt_time(hour=WEEKLY_DEBRIEF_HOUR, minute=0, tzinfo=UTC),
        name="weekly_debrief",
    )
    logger.info(
        "Weekly debrief scheduled at %02d:00 UTC on Sundays", WEEKLY_DEBRIEF_HOUR
    )

    if STRAVA_WEBHOOK_PORT:
        import threading

        from strava_utils.strava_webhook import serve as _webhook_serve

        wh_thread = threading.Thread(
            target=_webhook_serve,
            kwargs={"port": STRAVA_WEBHOOK_PORT},
            daemon=True,
        )
        wh_thread.start()
        logger.info("Webhook server started on port %d", STRAVA_WEBHOOK_PORT)

        async def _webhook_event_checker(context: object) -> None:
            """Process any Strava push events written by the webhook server."""
            from strava_utils.strava_webhook import _pop_events

            events = _pop_events()
            new_act_ids = {
                e["object_id"]
                for e in events
                if e.get("object_type") == "activity"
                and e.get("aspect_type") == "create"
            }
            if not new_act_ids:
                return
            cfg = app.bot_data["config"]
            cid = int(cfg.chat_id)
            logger.info(
                "Webhook: %d new activity event(s) — scheduling analysis",
                len(new_act_ids),
            )
            existing = cfg.pending_analysis.get(cid)
            existing_ids = (existing or {}).get("new_act_ids", [])
            merged = list(set(list(new_act_ids) + existing_ids))
            if existing:
                for job in context.job_queue.get_jobs_by_name(existing["job_name"]):  # type: ignore[union-attr]
                    job.schedule_removal()
            job_name = f"webhook_{next(iter(new_act_ids))}"
            cfg.pending_analysis[cid] = {"job_name": job_name, "new_act_ids": merged}
            await context.bot.send_message(  # type: ignore[union-attr]
                chat_id=cfg.chat_id,
                text=(
                    f"<b>Strava webhook:</b> {len(new_act_ids)} new activity event(s). "
                    f"Analysing in {STRAVA_ANALYSIS_DELAY // 60} min\u2026"
                ),
                parse_mode="HTML",
            )
            context.job_queue.run_once(  # type: ignore[union-attr]
                _deferred_analysis,
                when=STRAVA_ANALYSIS_DELAY,
                data={"new_act_ids": merged},
                name=job_name,
            )

        app.job_queue.run_repeating(_webhook_event_checker, interval=60, first=30)
        logger.info("Webhook event checker scheduled (every 60 s)")

    app.run_polling()


def morning_briefing() -> None:
    """Send today's session and week progress as a morning briefing message."""
    from coach_utils import plan as plan_mod

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
    logger.info("Sent morning briefing.")


if __name__ == "__main__":
    fire.Fire(
        {
            "send": send,
            "send_summary": send_summary,
            "morning_briefing": morning_briefing,
            "bot": bot,
        }
    )
