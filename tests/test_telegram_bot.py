"""Tests for tgbot.bot and tgbot.formatters modules."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


def test_get_bot_config_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import tgbot.bot as bot_mod

    token, chat_id = bot_mod._get_bot_config()
    assert token == "fake-token"
    assert chat_id == "12345"


def test_get_bot_config_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import tgbot.bot as bot_mod

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN not set"):
        bot_mod._get_bot_config()


def test_get_bot_config_missing_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    import tgbot.bot as bot_mod

    with pytest.raises(RuntimeError, match="TELEGRAM_CHAT_ID not set"):
        bot_mod._get_bot_config()


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------


def test_format_activity_summary(sample_activities: list[dict]) -> None:
    import tgbot.formatters as fmt_mod

    text = fmt_mod._format_activity_summary(sample_activities[0])
    assert "<b>Morning Run</b>" in text
    assert "10.0 km" in text
    assert "5:00/km" in text
    assert "145 bpm" in text


def test_format_today_session_with_session() -> None:
    import tgbot.formatters as fmt_mod

    session = {"type": "easy", "description": "Easy 8km", "distance_km": 8}
    text = fmt_mod._format_today_session(session)
    assert "<b>Today: Easy</b>" in text
    assert "Easy 8km" in text
    assert "8 km" in text


def test_format_today_session_none() -> None:
    import tgbot.formatters as fmt_mod

    text = fmt_mod._format_today_session(None)
    assert "rest day" in text.lower() or "no plan" in text.lower()


def test_format_plan_summary(sample_plan: dict) -> None:
    import tgbot.formatters as fmt_mod

    text = fmt_mod._format_plan_summary(sample_plan)
    assert "<b>Training Plan</b>" in text
    assert "Sub-45 10K" in text
    assert "Weeks: 1" in text


def test_format_results_with_data() -> None:
    import tgbot.formatters as fmt_mod

    results = [
        {"date": "15 Jun 24", "event": "5K", "time": "17:30", "position": 3},
        {"date": "01 Apr 24", "event": "10K", "time": "36:45"},
    ]
    text = fmt_mod._format_results(results)
    assert "<b>Race Results</b>" in text
    assert "5K" in text
    assert "17:30" in text
    assert "#3" in text
    assert "10K" in text


def test_format_results_empty() -> None:
    import tgbot.formatters as fmt_mod

    text = fmt_mod._format_results([])
    assert "No race results" in text


def test_format_weekly_summary() -> None:
    import tgbot.formatters as fmt_mod

    summary = {
        "runs": 4,
        "total_km": 35.5,
        "total_time_s": 12600,
        "avg_pace": "5:55",
    }
    text = fmt_mod._format_weekly_summary(summary)
    assert "<b>Weekly Summary" in text
    assert "Activities: 4" in text
    assert "35.5 km" in text
    assert "5:55/km" in text


# ---------------------------------------------------------------------------
# Data helper tests
# ---------------------------------------------------------------------------


def test_today_session_found(tmp_data_dir: Path, sample_plan: dict) -> None:
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    sample_plan["weeks"][0]["sessions"][0]["date"] = today

    plan_path = tmp_data_dir / "training_plan.json"
    with open(plan_path, "w") as f:
        json.dump(sample_plan, f)

    import tgbot.formatters as fmt_mod

    session = fmt_mod._today_session()
    assert session is not None
    assert session["type"] == "easy"


def test_today_session_not_found(tmp_data_dir: Path, sample_plan: dict) -> None:
    plan_path = tmp_data_dir / "training_plan.json"
    with open(plan_path, "w") as f:
        json.dump(sample_plan, f)

    import tgbot.formatters as fmt_mod

    session = fmt_mod._today_session()
    # sample_plan dates are 2025-01-15..17, won't match today
    assert session is None


def test_weekly_summary_with_data(
    tmp_data_dir: Path, sample_activities: list[dict]
) -> None:
    # Set activity dates to within last 7 days
    now = datetime.now(tz=UTC)
    sample_activities[0]["date"] = (now - timedelta(days=1)).isoformat()
    sample_activities[1]["date"] = (now - timedelta(days=2)).isoformat()

    activities_path = tmp_data_dir / "activities.json"
    with open(activities_path, "w") as f:
        json.dump(sample_activities, f)

    import tgbot.formatters as fmt_mod

    summary = fmt_mod._weekly_summary()
    assert summary["runs"] == 2
    assert summary["total_km"] == 15.0


def test_weekly_summary_empty(tmp_data_dir: Path) -> None:
    import tgbot.formatters as fmt_mod

    summary = fmt_mod._weekly_summary()
    assert summary["runs"] == 0
    assert summary["total_km"] == 0


# ---------------------------------------------------------------------------
# Send tests
# ---------------------------------------------------------------------------


def test_send_calls_telegram_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import tgbot.bot as bot_mod

    with patch.object(
        bot_mod,
        "_send_telegram_message",
        return_value={"ok": True},
    ) as mock_send:
        bot_mod.send("Hello!")
        mock_send.assert_called_once_with("Hello!")


# ---------------------------------------------------------------------------
# Path validation tests
# ---------------------------------------------------------------------------


def test_validate_data_path_traversal(tmp_data_dir: Path) -> None:
    import tgbot.bot as bot_mod

    assert bot_mod._validate_data_path("../../.env") is None


def test_validate_data_path_blocked(tmp_data_dir: Path) -> None:
    import tgbot.bot as bot_mod

    assert bot_mod._validate_data_path("tokens.json") is None


def test_validate_data_path_valid(tmp_data_dir: Path) -> None:
    import tgbot.bot as bot_mod

    (tmp_data_dir / "activities.json").write_text("[]")
    result = bot_mod._validate_data_path("activities.json")
    assert result is not None
    assert result.name == "activities.json"


# ---------------------------------------------------------------------------
# Send tests (continued)
# ---------------------------------------------------------------------------


def test_send_truncates_long_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify message is truncated at 4096 chars."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    import tgbot.bot as bot_mod

    captured_text: list[str] = []

    def mock_urlopen(req):
        body = json.loads(req.data.decode())
        captured_text.append(body["text"])

        class FakeResp:
            def read(self):
                return json.dumps({"ok": True}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return FakeResp()

    long_text = "x" * 5000
    with patch("urllib.request.urlopen", mock_urlopen):
        bot_mod._send_telegram_message(long_text)

    assert len(captured_text[0]) <= bot_mod.TELEGRAM_MAX_LENGTH
    assert captured_text[0].endswith("...")
