"""Post-run debrief storage and RPE parsing."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path


def _debrief_path() -> Path:
    import _token_utils

    return _token_utils.DATA_DIR / "debriefs.json"


def load_debriefs() -> dict[str, dict]:
    """Load all debriefs, keyed by str(activity_id)."""
    path = _debrief_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_debrief(
    activity_id: int,
    activity_name: str,
    activity_date: str,
    rpe: int,
    notes: str,
) -> None:
    """Save (or overwrite) debrief for a given activity."""
    path = _debrief_path()
    debriefs = load_debriefs()
    debriefs[str(activity_id)] = {
        "activity_id": activity_id,
        "activity_name": activity_name,
        "activity_date": activity_date,
        "rpe": rpe,
        "notes": notes,
        "recorded_at": datetime.now(tz=UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(debriefs, indent=2))


def parse_rpe(text: str) -> tuple[int | None, str]:
    """Extract RPE (1-10) from user text.

    Returns (rpe, notes) where notes is the original text.
    If text is "skip" (case-insensitive, stripped), returns (None, text).
    If no valid RPE is found, returns (None, text).
    """
    if text.strip().lower() == "skip":
        return None, text
    m = re.search(r"\b(10|[1-9])(?!\d)", text)
    if m:
        return int(m.group(1)), text
    return None, text
