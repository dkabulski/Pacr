"""Tests for tgbot.debrief — RPE parsing and debrief persistence."""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# parse_rpe tests
# ---------------------------------------------------------------------------


def test_parse_rpe_bare_digit() -> None:
    from tgbot.debrief import parse_rpe

    rpe, notes = parse_rpe("7")
    assert rpe == 7
    assert notes == "7"


def test_parse_rpe_with_commentary() -> None:
    from tgbot.debrief import parse_rpe

    text = "7 — legs felt heavy"
    rpe, notes = parse_rpe(text)
    assert rpe == 7
    assert notes == text


def test_parse_rpe_10() -> None:
    from tgbot.debrief import parse_rpe

    rpe, notes = parse_rpe("10 all out")
    assert rpe == 10
    assert notes == "10 all out"


def test_parse_rpe_no_number() -> None:
    from tgbot.debrief import parse_rpe

    rpe, notes = parse_rpe("felt hard")
    assert rpe is None
    assert notes == "felt hard"


def test_parse_rpe_skip() -> None:
    from tgbot.debrief import parse_rpe

    rpe, notes = parse_rpe("skip")
    assert rpe is None
    assert notes == "skip"


def test_parse_rpe_skip_case_insensitive() -> None:
    from tgbot.debrief import parse_rpe

    rpe, notes = parse_rpe("Skip")
    assert rpe is None
    assert notes == "Skip"


def test_parse_rpe_rejects_zero() -> None:
    from tgbot.debrief import parse_rpe

    rpe, _ = parse_rpe("0")
    assert rpe is None


def test_parse_rpe_rejects_eleven() -> None:
    from tgbot.debrief import parse_rpe

    rpe, _ = parse_rpe("11 hard")
    assert rpe is None


# ---------------------------------------------------------------------------
# save_debrief / load_debriefs tests
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_data_dir: Path) -> None:
    from tgbot.debrief import load_debriefs, save_debrief

    save_debrief(
        activity_id=12345678,
        activity_name="Morning Run",
        activity_date="2026-02-28",
        rpe=7,
        notes="7 — legs felt heavy",
    )
    debriefs = load_debriefs()
    assert "12345678" in debriefs
    entry = debriefs["12345678"]
    assert entry["activity_id"] == 12345678
    assert entry["activity_name"] == "Morning Run"
    assert entry["activity_date"] == "2026-02-28"
    assert entry["rpe"] == 7
    assert entry["notes"] == "7 — legs felt heavy"
    assert "recorded_at" in entry


def test_save_debrief_overwrites_existing(tmp_data_dir: Path) -> None:
    from tgbot.debrief import load_debriefs, save_debrief

    save_debrief(12345678, "Morning Run", "2026-02-28", 7, "first entry")
    save_debrief(12345678, "Morning Run", "2026-02-28", 9, "updated entry")
    debriefs = load_debriefs()
    assert debriefs["12345678"]["rpe"] == 9
    assert debriefs["12345678"]["notes"] == "updated entry"


def test_load_debriefs_empty(tmp_data_dir: Path) -> None:
    from tgbot.debrief import load_debriefs

    result = load_debriefs()
    assert result == {}


def test_multiple_activities(tmp_data_dir: Path) -> None:
    from tgbot.debrief import load_debriefs, save_debrief

    save_debrief(111, "Run A", "2026-02-27", 6, "easy day")
    save_debrief(222, "Run B", "2026-02-28", 8, "hard session")
    debriefs = load_debriefs()
    assert len(debriefs) == 2
    assert debriefs["111"]["rpe"] == 6
    assert debriefs["222"]["rpe"] == 8
