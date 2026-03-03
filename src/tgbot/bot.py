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

if os.environ.get("LOG_FORMAT") == "json":
    import json as _json_log

    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            return _json_log.dumps(
                {
                    "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                }
            )

    _handler = logging.StreamHandler()
    _handler.setFormatter(_JsonFormatter())
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
logger = logging.getLogger("pacr")

STRAVA_POLL_INTERVAL = int(os.environ.get("STRAVA_POLL_INTERVAL", "1800"))
STRAVA_ANALYSIS_DELAY = int(os.environ.get("STRAVA_ANALYSIS_DELAY", "600"))  # 10 min
MORNING_CHECKIN_HOUR = int(os.environ.get("MORNING_CHECKIN_HOUR", "8"))

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
    cmd_clear,
    cmd_help,
    cmd_last,
    cmd_load,
    cmd_message,
    cmd_next,
    cmd_plan,
    cmd_results,
    cmd_setplan,
    cmd_sport,
    cmd_start,
    cmd_summary,
    cmd_sync,
    cmd_today,
    cmd_week,
    cmd_zones,
    morning_checkin,
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
                BotCommand("week", "This week vs plan"),
                BotCommand("next", "Next 5 planned sessions"),
                BotCommand("last", "Last activity detail"),
                BotCommand("summary", "7-day stats"),
                BotCommand("plan", "Show full training plan"),
                BotCommand("setplan", "Generate a new plan with AI"),
                BotCommand("analyse", "Analyse last activity"),
                BotCommand("reanalyse", "Re-analyse last activity on demand"),
                BotCommand("load", "Training load: CTL/ATL/TSB + weekly km"),
                BotCommand("results", "Race results"),
                BotCommand("zones", "HR and pace zones"),
                BotCommand("adherence", "Plan adherence score"),
                BotCommand("sport", "Set activity type filter"),
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
    app.add_handler(CommandHandler("last", cmd_last, filters=chat_filter))
    app.add_handler(CommandHandler("summary", cmd_summary, filters=chat_filter))
    app.add_handler(CommandHandler("plan", cmd_plan, filters=chat_filter))
    app.add_handler(CommandHandler("setplan", cmd_setplan, filters=chat_filter))
    app.add_handler(CommandHandler("analyse", cmd_analyse, filters=chat_filter))
    app.add_handler(CommandHandler("reanalyse", cmd_analyse, filters=chat_filter))
    app.add_handler(CommandHandler("load", cmd_load, filters=chat_filter))
    app.add_handler(CommandHandler("results", cmd_results, filters=chat_filter))
    app.add_handler(CommandHandler("zones", cmd_zones, filters=chat_filter))
    app.add_handler(CommandHandler("sport", cmd_sport, filters=chat_filter))
    app.add_handler(CommandHandler("adherence", cmd_adherence, filters=chat_filter))
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
