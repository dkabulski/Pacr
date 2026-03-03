"""Lightweight Telegram message sender (stdlib only, no circular deps)."""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger("pacr")

TELEGRAM_MAX_LENGTH = 4096


def _get_bot_config() -> tuple[str, str]:
    """Read TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment."""
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
