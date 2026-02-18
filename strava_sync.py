# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "requests>=2.32",
#     "fire>=0.7",
#     "python-dotenv>=1.0",
# ]
# ///
"""Fetch and cache Strava activities."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fire
import requests
from dotenv import load_dotenv

import _token_utils

load_dotenv()

ACTIVITIES_PATH = _token_utils.DATA_DIR / "activities.json"
PER_PAGE = 200


def format_pace(distance_m: float, elapsed_s: float) -> str:
    """Convert distance (metres) and time (seconds) to min/km pace string."""
    if distance_m <= 0:
        return "N/A"
    pace_s_per_km = elapsed_s / (distance_m / 1000)
    mins = int(pace_s_per_km // 60)
    secs = int(pace_s_per_km % 60)
    return f"{mins}:{secs:02d}"


def normalize_activity(raw: dict) -> dict:
    """Normalise a raw Strava API activity to our schema."""
    distance_m = raw.get("distance", 0)
    moving_time = raw.get("moving_time", 0)
    elapsed_time = raw.get("elapsed_time", moving_time)

    return {
        "id": raw["id"],
        "name": raw.get("name", ""),
        "type": raw.get("type", "Run"),
        "sport_type": raw.get("sport_type", raw.get("type", "Run")),
        "date": raw.get("start_date_local", ""),
        "distance_m": distance_m,
        "distance_km": round(distance_m / 1000, 2),
        "moving_time_s": moving_time,
        "elapsed_time_s": elapsed_time,
        "pace": format_pace(distance_m, moving_time),
        "elevation_m": raw.get("total_elevation_gain", 0),
        "avg_hr": raw.get("average_heartrate"),
        "max_hr": raw.get("max_heartrate"),
        "avg_cadence": raw.get("average_cadence"),
        "suffer_score": raw.get("suffer_score"),
        "calories": raw.get("calories"),
    }


def _load_cached() -> list[dict]:
    """Load cached activities from disk."""
    if not ACTIVITIES_PATH.exists():
        return []
    with open(ACTIVITIES_PATH) as f:
        return json.load(f)


def _save_cached(activities: list[dict]) -> None:
    """Save activities to disk."""
    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACTIVITIES_PATH, "w") as f:
        json.dump(activities, f, indent=2)


def _merge(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new activities into existing, deduplicating by ID (newer wins)."""
    by_id: dict[int, dict] = {}
    for act in existing:
        by_id[act["id"]] = act
    for act in new:
        by_id[act["id"]] = act
    return sorted(by_id.values(), key=lambda a: a["date"], reverse=True)


def sync(days: int = 30) -> None:
    """Fetch the last N days of Strava activities."""
    try:
        token = _token_utils.get_valid_token()
    except RuntimeError as e:
        print(f"Error: {e}")
        return

    after = datetime.now(tz=timezone.utc) - timedelta(days=days)
    after_ts = int(after.timestamp())

    headers = {"Authorization": f"Bearer {token}"}
    all_raw: list[dict] = []
    page = 1

    while True:
        print(f"Fetching page {page}...")
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers=headers,
            params={"after": after_ts, "per_page": PER_PAGE, "page": page},
            timeout=30,
        )

        if resp.status_code == 401:
            print("Error: Unauthorised. Attempting token refresh...")
            try:
                token = _token_utils.get_valid_token()
                headers = {"Authorization": f"Bearer {token}"}
                continue
            except RuntimeError as e:
                print(f"Refresh failed: {e}")
                return

        if resp.status_code == 429:
            print("Error: Rate limited by Strava. Try again later.")
            print(f"Rate limit info: {resp.headers.get('X-RateLimit-Usage', 'N/A')}")
            return

        if resp.status_code != 200:
            print(f"Error: HTTP {resp.status_code}")
            print(resp.text[:500])
            return

        batch = resp.json()
        if not batch:
            break

        all_raw.extend(batch)
        if len(batch) < PER_PAGE:
            break
        page += 1

    normalised = [normalize_activity(a) for a in all_raw]
    existing = _load_cached()
    merged = _merge(existing, normalised)
    _save_cached(merged)
    print(f"Synced {len(normalised)} activities ({len(merged)} total cached)")


def show(id: int | None = None, last: int = 10) -> None:
    """Display cached activities or a specific activity."""
    cached = _load_cached()
    if not cached:
        print("No activities cached. Run: uv run strava_sync.py sync")
        return

    if id is not None:
        match = [a for a in cached if a["id"] == id]
        if not match:
            print(f"Activity {id} not found in cache.")
            return
        print(json.dumps(match[0], indent=2))
        return

    for act in cached[:last]:
        date = act["date"][:10] if act["date"] else "?"
        dist = act.get("distance_km", 0)
        pace = act.get("pace", "N/A")
        hr = act.get("avg_hr")
        hr_str = f"  HR {hr:.0f}" if hr else ""
        print(f"  {date}  {act['name']:<30}  {dist:>6.1f} km  {pace}/km{hr_str}")


if __name__ == "__main__":
    fire.Fire({"sync": sync, "show": show})
