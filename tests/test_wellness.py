"""Tests for wellness tracking."""

from __future__ import annotations

import json
from pathlib import Path

from coach_utils import wellness


def test_log_entry(tmp_data_dir: Path) -> None:
    """log_entry creates and persists an entry."""
    entry = wellness.log_entry("pain", "left knee", 5, "after long run")
    assert entry["type"] == "pain"
    assert entry["body_part"] == "left knee"
    assert entry["severity"] == 5
    assert entry["status"] == "active"
    # Verify persisted
    log = json.loads((tmp_data_dir / "wellness_log.json").read_text())
    assert len(log) == 1


def test_get_active_issues(tmp_data_dir: Path) -> None:
    """Active issues filtered correctly."""
    wellness.log_entry("pain", "left knee", 5)
    wellness.log_entry("soreness", "right calf", 3)
    active = wellness.get_active_issues()
    assert len(active) == 2


def test_resolve_entry(tmp_data_dir: Path) -> None:
    """Resolving an entry sets status and resolved_date."""
    entry = wellness.log_entry("pain", "left knee", 5)
    result = wellness.resolve_entry(entry["id"])
    assert result is True
    active = wellness.get_active_issues()
    assert len(active) == 0


def test_resolve_nonexistent(tmp_data_dir: Path) -> None:
    """Resolving a nonexistent entry returns False."""
    result = wellness.resolve_entry("nonexistent")
    assert result is False


def test_severity_clamped(tmp_data_dir: Path) -> None:
    """Severity is clamped to 1-10."""
    low = wellness.log_entry("pain", "knee", -5)
    high = wellness.log_entry("pain", "knee", 15)
    assert low["severity"] == 1
    assert high["severity"] == 10


def test_detect_recurring_pattern(tmp_data_dir: Path) -> None:
    """3+ entries for same body part in 30 days triggers recurring pattern."""
    for _ in range(3):
        wellness.log_entry("pain", "left knee", 4)
    patterns = wellness.detect_patterns()
    recurring = [p for p in patterns if p["type"] == "recurring"]
    assert len(recurring) == 1
    assert recurring[0]["body_part"] == "left knee"


def test_detect_chronic_issue(tmp_data_dir: Path) -> None:
    """Active issue older than 14 days triggers chronic pattern."""
    # Manually create an old entry
    entries = [
        {
            "id": "old123",
            "date": "2025-01-01",
            "type": "pain",
            "body_part": "right achilles",
            "severity": 6,
            "notes": "",
            "status": "active",
        }
    ]
    (tmp_data_dir / "wellness_log.json").write_text(json.dumps(entries))
    patterns = wellness.detect_patterns()
    chronic = [p for p in patterns if p["type"] == "chronic"]
    assert len(chronic) == 1
    assert chronic[0]["body_part"] == "right achilles"
