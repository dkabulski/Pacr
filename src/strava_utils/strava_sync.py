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
import logging
import sys
import time as _time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fire
import requests
from dotenv import load_dotenv

import _token_utils

load_dotenv()

logger = logging.getLogger("pacr")

_RETRYABLE = {500, 502, 503, 504}
_MAX_RETRIES = 3


def _strava_get(
    url: str,
    headers: dict,
    params: dict | None = None,
    timeout: int = 30,
) -> requests.Response:
    """GET with exponential backoff for transient Strava errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(
                url, headers=headers, params=params, timeout=timeout
            )
        except requests.exceptions.Timeout:
            if attempt == _MAX_RETRIES - 1:
                raise
            _time.sleep(2 ** attempt)
            continue
        if resp.status_code in _RETRYABLE:
            if attempt == _MAX_RETRIES - 1:
                msg = f"Strava API error after retries: HTTP {resp.status_code}"
                raise RuntimeError(msg)
            logger.warning(
                "Strava HTTP %d, retry %d/%d",
                resp.status_code,
                attempt + 1,
                _MAX_RETRIES,
            )
            _time.sleep(2 ** attempt)
            continue
        return resp
    raise RuntimeError("Strava request failed")


def _activities_path() -> Path:
    return _token_utils.DATA_DIR / "activities.json"


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
        "description": raw.get("description", "") or "",
    }


def _load_cached() -> list[dict]:
    """Load cached activities from disk."""
    if not _activities_path().exists():
        return []
    with open(_activities_path()) as f:
        return json.load(f)


def _fetch_description(activity_id: int) -> str:
    """Fetch the description of a single activity.

    The list API returns SummaryActivity which omits description; this calls
    GET /activities/{id} (DetailedActivity) to retrieve it.
    Returns an empty string if unavailable or on error.
    """
    token = _token_utils.get_valid_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = _strava_get(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers=headers,
    )
    if resp.status_code != 200:
        return ""
    return resp.json().get("description", "") or ""


def _save_cached(activities: list[dict]) -> None:
    """Save activities to disk."""
    _token_utils.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_activities_path(), "w") as f:
        json.dump(activities, f, indent=2)


def _merge(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new activities into existing, deduplicating by ID (newer wins)."""
    by_id: dict[int, dict] = {}
    for act in existing:
        by_id[act["id"]] = act
    for act in new:
        by_id[act["id"]] = act
    return sorted(by_id.values(), key=lambda a: a["date"], reverse=True)


def sync(days: int = 365) -> None:
    """Fetch the last N days of Strava activities."""
    token = _token_utils.get_valid_token()

    after = datetime.now(tz=UTC) - timedelta(days=days)
    after_ts = int(after.timestamp())

    headers = {"Authorization": f"Bearer {token}"}
    all_raw: list[dict] = []
    page = 1

    while True:
        logger.info("Fetching Strava page %d…", page)
        resp = _strava_get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers=headers,
            params={"after": after_ts, "per_page": PER_PAGE, "page": page},
        )

        if resp.status_code == 401:
            logger.warning("Strava token expired, refreshing…")
            token = _token_utils.get_valid_token()
            headers = {"Authorization": f"Bearer {token}"}
            continue

        if resp.status_code == 429:
            usage = resp.headers.get("X-RateLimit-Usage", "N/A")
            raise RuntimeError(f"Strava rate limit hit (usage: {usage}). Try again later.")

        if resp.status_code != 200:
            raise RuntimeError(f"Strava API error: HTTP {resp.status_code} — {resp.text[:200]}")

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
    logger.info("Synced %d activities (%d total cached)", len(normalised), len(merged))


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
    import os as _os

    if _os.environ.get("LOG_FORMAT") == "json":
        class _JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return json.dumps({
                    "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                })

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
    fire.Fire({"sync": sync, "show": show})
