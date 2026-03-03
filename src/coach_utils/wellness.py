"""Injury and wellness tracking — log, resolve, and detect patterns."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("pacr")


def _wellness_path() -> Path:
    import _token_utils

    return _token_utils.DATA_DIR / "wellness_log.json"


def _load_log() -> list[dict]:
    path = _wellness_path()
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _save_log(entries: list[dict]) -> None:
    import _token_utils

    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_wellness_path(), "w") as f:
        json.dump(entries, f, indent=2)


def log_entry(
    entry_type: str,
    body_part: str,
    severity: int,
    notes: str = "",
) -> dict:
    """Log a new wellness entry.

    Args:
        entry_type: Type of issue (e.g. 'pain', 'soreness', 'tightness', 'fatigue').
        body_part: Affected body part (e.g. 'left knee', 'right calf').
        severity: 1-10 severity scale, clamped to range.
        notes: Optional free-text notes.

    Returns:
        The created entry dict.
    """
    severity = max(1, min(10, severity))
    entry = {
        "id": str(uuid.uuid4())[:8],
        "date": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
        "type": entry_type,
        "body_part": body_part.lower().strip(),
        "severity": severity,
        "notes": notes,
        "status": "active",
    }
    entries = _load_log()
    entries.append(entry)
    _save_log(entries)
    logger.info(
        "Wellness entry logged: %s %s severity %d", entry_type, body_part, severity
    )
    return entry


def get_active_issues() -> list[dict]:
    """Return all active wellness issues, sorted newest first."""
    entries = _load_log()
    active = [e for e in entries if e.get("status") == "active"]
    active.sort(key=lambda e: e.get("date", ""), reverse=True)
    return active


def resolve_entry(entry_id: str) -> bool:
    """Resolve a wellness entry by ID. Returns True if found and resolved."""
    entries = _load_log()
    for entry in entries:
        if entry.get("id") == entry_id:
            entry["status"] = "resolved"
            entry["resolved_date"] = datetime.now(tz=UTC).strftime("%Y-%m-%d")
            _save_log(entries)
            logger.info("Wellness entry resolved: %s", entry_id)
            return True
    return False


def detect_patterns() -> list[dict]:
    """Detect concerning wellness patterns.

    Patterns:
    - recurring: same body part 3+ times in 30 days
    - escalating: severity increasing for same body part
    - chronic: active issue older than 14 days

    Returns list of pattern dicts with keys: type, body_part, detail.
    """
    entries = _load_log()
    patterns: list[dict] = []

    cutoff_30d = (datetime.now(tz=UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
    cutoff_14d = (datetime.now(tz=UTC) - timedelta(days=14)).strftime("%Y-%m-%d")

    # Group recent entries by body part
    recent_by_part: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("date", "") >= cutoff_30d:
            part = e.get("body_part", "")
            recent_by_part.setdefault(part, []).append(e)

    # Recurring: 3+ entries for same body part in 30 days
    for part, part_entries in recent_by_part.items():
        if len(part_entries) >= 3:
            patterns.append(
                {
                    "type": "recurring",
                    "body_part": part,
                    "detail": f"{len(part_entries)} entries in the last 30 days",
                }
            )

    # Escalating: severity increasing across entries for same body part
    for part, part_entries in recent_by_part.items():
        sorted_entries = sorted(part_entries, key=lambda e: e.get("date", ""))
        if len(sorted_entries) >= 2:
            severities = [e.get("severity", 0) for e in sorted_entries]
            if all(
                severities[i] < severities[i + 1] for i in range(len(severities) - 1)
            ):
                patterns.append(
                    {
                        "type": "escalating",
                        "body_part": part,
                        "detail": (
                            f"severity rising: "
                            f"{' \u2192 '.join(str(s) for s in severities)}"
                        ),
                    }
                )

    # Chronic: active issues older than 14 days
    for e in entries:
        if e.get("status") == "active" and e.get("date", "") < cutoff_14d:
            patterns.append(
                {
                    "type": "chronic",
                    "body_part": e.get("body_part", ""),
                    "detail": f"active since {e.get('date', '?')}",
                }
            )

    return patterns
