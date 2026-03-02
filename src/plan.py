# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "fire>=0.7",
# ]
# ///
"""Training plan management — read/write data/training_plan.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import fire

import _token_utils


def _plan_path() -> Path:
    return _token_utils.DATA_DIR / "training_plan.json"


def _load_plan() -> dict | None:
    """Load the current training plan from disk."""
    if not _plan_path().exists():
        return None
    with open(_plan_path()) as f:
        return json.load(f)


def _save_plan(plan: dict) -> None:
    """Save the training plan to disk."""
    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_plan_path(), "w") as f:
        json.dump(plan, f, indent=2)


def show() -> None:
    """Display the current training plan."""
    plan = _load_plan()
    if plan is None:
        print("No training plan set. Ask your coach to create one.")
        return
    print(json.dumps(plan, indent=2))


def set() -> None:
    """Set a new training plan from stdin JSON.

    The JSON must contain a 'weeks' array where each week has 'sessions'.
    """
    raw = sys.stdin.read().strip()
    if not raw:
        print("Error: No input received. Pipe JSON via stdin.")
        print("Usage: echo '{...}' | uv run plan.py set")
        return

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON — {e}")
        return

    # Validate structure
    if not isinstance(plan, dict):
        print("Error: Plan must be a JSON object.")
        return

    if "weeks" not in plan:
        print("Error: Plan must contain a 'weeks' array.")
        return

    if not isinstance(plan["weeks"], list):
        print("Error: 'weeks' must be an array.")
        return

    for i, week in enumerate(plan["weeks"]):
        if not isinstance(week, dict):
            print(f"Error: Week {i + 1} must be a JSON object.")
            return
        if "sessions" not in week:
            print(f"Error: Week {i + 1} must contain a 'sessions' array.")
            return

    _save_plan(plan)
    print(f"Training plan saved ({len(plan['weeks'])} weeks)")


def update(week: int) -> None:
    """Update a specific week in the plan from stdin JSON.

    Args:
        week: 1-based week number to replace.
    """
    plan = _load_plan()
    if plan is None:
        print("Error: No training plan exists. Use 'set' first.")
        return

    raw = sys.stdin.read().strip()
    if not raw:
        print("Error: No input received. Pipe week JSON via stdin.")
        return

    try:
        week_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON — {e}")
        return

    if not isinstance(week_data, dict):
        print("Error: Week data must be a JSON object.")
        return

    if "sessions" not in week_data:
        print("Error: Week data must contain a 'sessions' array.")
        return

    idx = week - 1
    if idx < 0 or idx >= len(plan["weeks"]):
        print(f"Error: Week {week} out of range (plan has {len(plan['weeks'])} weeks).")
        return

    plan["weeks"][idx] = week_data
    _save_plan(plan)
    print(f"Week {week} updated")


def clear() -> None:
    """Delete the current training plan."""
    if _plan_path().exists():
        _plan_path().unlink()
        print("Training plan deleted.")
    else:
        print("No training plan to delete.")


if __name__ == "__main__":
    fire.Fire({"show": show, "set": set, "update": update, "clear": clear})
