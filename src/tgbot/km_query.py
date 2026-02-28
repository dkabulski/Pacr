"""Pure-stdlib module for answering local km queries without Claude API calls."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

MONTH_NAMES: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_DISTANCE_RE = re.compile(
    r"\b(?:km|kilometers?|kilometres?|distance|far|ran|run|jog|miles?)\b",
    re.IGNORECASE,
)

_MONTH_NAMES_PATTERN = "|".join(re.escape(k) for k in MONTH_NAMES)
_PERIOD_RE = re.compile(
    rf"(?:\b(?:year|month|week|ever|{_MONTH_NAMES_PATTERN}|20\d{{2}})\b)"
    r"|(?:all[\s\-]?time)",
    re.IGNORECASE,
)


def parse_period(text: str) -> tuple[date, date] | None:
    """Parse a natural-language period from text into (start, end) dates.

    Checks in priority order (most-specific first). Returns None if the
    period is not recognised.
    """
    t = text.lower()
    today = date.today()
    y, m = today.year, today.month

    if "last year" in t:
        return date(y - 1, 1, 1), date(y - 1, 12, 31)

    if "this year" in t:
        return date(y, 1, 1), today

    if "last month" in t:
        if m == 1:
            pm, py = 12, y - 1
        else:
            pm, py = m - 1, y
        last_day = monthrange(py, pm)[1]
        return date(py, pm, 1), date(py, pm, last_day)

    if "this month" in t:
        return date(y, m, 1), today

    if "last week" in t:
        last_monday = today - timedelta(days=today.weekday() + 7)
        last_sunday = last_monday + timedelta(days=6)
        return last_monday, last_sunday

    if "this week" in t:
        this_monday = today - timedelta(days=today.weekday())
        return this_monday, today

    if re.search(r"\ball\s*time\b", t) or re.search(r"\bever\b", t):
        return date(2000, 1, 1), today

    # Named month, optionally followed by a year: "January 2025" or "January"
    month_match = re.search(
        rf"\b({_MONTH_NAMES_PATTERN})\b(?:\s+(\d{{4}}))?",
        t,
    )

    # Standalone 4-digit year: "in 2024"
    year_match = re.search(r"\b(20\d{2})\b", t)

    if month_match:
        month_num = MONTH_NAMES[month_match.group(1)]
        if month_match.group(2):
            year_num = int(month_match.group(2))
        elif year_match:
            year_num = int(year_match.group(1))
        else:
            year_num = y
        last_day = monthrange(year_num, month_num)[1]
        start = date(year_num, month_num, 1)
        end = date(year_num, month_num, last_day)
        if year_num == y and month_num == m:
            end = min(end, today)
        return start, end

    if year_match:
        year_num = int(year_match.group(1))
        if year_num == y:
            return date(y, 1, 1), today
        return date(year_num, 1, 1), date(year_num, 12, 31)

    return None


def is_km_query(text: str) -> bool:
    """Return True iff text contains both a distance word and a period word."""
    return bool(_DISTANCE_RE.search(text)) and bool(_PERIOD_RE.search(text))


_RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}


def _is_run(act: dict) -> bool:
    """Return True if the activity is a running activity."""
    return act.get("type") in _RUN_TYPES or act.get("sport_type") in _RUN_TYPES


def compute_km(activities: list[dict], start: date, end: date) -> dict:
    """Sum distance_km for Run activities within [start, end] inclusive."""
    start_str = start.isoformat()
    end_str = end.isoformat()
    total_km = 0.0
    runs = 0
    for act in activities:
        if not _is_run(act):
            continue
        act_date = act.get("date", "")[:10]
        if start_str <= act_date <= end_str:
            total_km += act.get("distance_km", 0.0)
            runs += 1
    return {"total_km": total_km, "runs": runs}


def describe_period(start: date, end: date) -> str:
    """Return a human-readable label for a (start, end) date range."""
    today = date.today()
    y = today.year

    # All time
    if start == date(2000, 1, 1):
        return "all time"

    # This week: Monday → today
    this_monday = today - timedelta(days=today.weekday())
    if start == this_monday and end == today:
        return "this week"

    # Last week: Monday → Sunday
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    if start == last_monday and end == last_sunday:
        return f"last week ({start.day}–{end.day} {end.strftime('%b')})"

    # Partial current year: Jan 1 → today
    if start == date(y, 1, 1) and end == today:
        return f"in {y} so far"

    # Full calendar year: Jan 1 → Dec 31
    if (
        start.month == 1
        and start.day == 1
        and end.month == 12
        and end.day == 31
        and start.year == end.year
    ):
        return f"in {start.year}"

    # Full or partial month
    if start.year == end.year and start.month == end.month and start.day == 1:
        last_day = monthrange(start.year, start.month)[1]
        month_name = start.strftime("%B")
        if end.day == last_day:
            return f"in {month_name} {start.year}"
        return f"in {month_name} {start.year} so far"

    # Fallback
    return f"{start} to {end}"
