# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "python-telegram-bot>=21.0",
#     "fire>=0.7",
#     "python-dotenv>=1.0",
#     "requests>=2.32",
#     "beautifulsoup4>=4.12",
# ]
# ///
"""Telegram integration — send notifications and interactive bot."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

import fire
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_MAX_LENGTH = 4096


# ---------------------------------------------------------------------------
# Config helpers
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
# Data helpers — import existing modules
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

    # Show current week (last week in plan, or find by date)
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


def _format_status() -> str:
    """Brief status: last sync, plan exists, today's session."""
    import strava_sync

    lines = ["<b>RunWhisperer Status</b>"]

    # Last sync
    activities = strava_sync._load_cached()
    if activities:
        last_date = activities[0].get("date", "")[:10]
        lines.append(f"Last activity: {last_date} ({len(activities)} cached)")
    else:
        lines.append("No activities synced yet")

    # Plan
    import plan as plan_mod

    p = plan_mod._load_plan()
    if p:
        goal = p.get("goal", "")
        weeks = len(p.get("weeks", []))
        lines.append(f"Plan: {goal} ({weeks} weeks)")
    else:
        lines.append("Plan: none set")

    # Today
    session = _today_session()
    if session:
        stype = session.get("type", "unknown")
        lines.append(f"Today: {stype.title()}")
    else:
        lines.append("Today: rest day / no plan")

    return "\n".join(lines)


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
        filters,
    )

    token, chat_id = _get_bot_config()
    chat_filter = filters.Chat(chat_id=int(chat_id))

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
            await asyncio.to_thread(strava_sync.sync, 7)
            activities = await asyncio.to_thread(strava_sync._load_cached)
            await update.message.reply_text(
                f"Sync complete. {len(activities)} activities cached.",
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

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "<b>Available Commands</b>\n\n"
            "/start — Greeting and status\n"
            "/sync — Sync Strava activities (last 7 days)\n"
            "/plan — Show training plan summary\n"
            "/today — Today's prescribed session\n"
            "/analyze — Analyse latest activity\n"
            "/results — Show race results\n"
            "/help — Show this help message"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    print(f"Starting bot (chat_id={chat_id})...")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start, filters=chat_filter))
    app.add_handler(CommandHandler("sync", cmd_sync, filters=chat_filter))
    app.add_handler(CommandHandler("plan", cmd_plan, filters=chat_filter))
    app.add_handler(CommandHandler("today", cmd_today, filters=chat_filter))
    app.add_handler(CommandHandler("analyze", cmd_analyze, filters=chat_filter))
    app.add_handler(CommandHandler("results", cmd_results, filters=chat_filter))
    app.add_handler(CommandHandler("help", cmd_help, filters=chat_filter))
    app.run_polling()


if __name__ == "__main__":
    fire.Fire({"send": send, "send_summary": send_summary, "bot": bot})
