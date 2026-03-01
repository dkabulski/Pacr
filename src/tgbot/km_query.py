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

# Activity-type sets (Strava type / sport_type values)
_RUN_TYPES: set[str] = {"Run", "TrailRun", "VirtualRun"}
_CYCLE_TYPES: set[str] = {"Ride", "VirtualRide", "EBikeRide", "MountainBikeRide"}
_HIKE_TYPES: set[str] = {"Hike"}
_SWIM_TYPES: set[str] = {"Swim"}
_WALK_TYPES: set[str] = {"Walk"}

# Distance / sport words that signal a km query
_DISTANCE_RE = re.compile(
    r"\b(?:km|kilometers?|kilometres?|distance|far|miles?"
    r"|ran|run(?:ning)?|jog(?:ging)?"
    r"|cycled?|cycling|biked?|biking|rode"
    r"|hiked?|hiking"
    r"|swam|swim(?:ming)?"
    r"|walk(?:ed)?|walking)\b",
    re.IGNORECASE,
)

_MONTH_NAMES_PATTERN = "|".join(re.escape(k) for k in MONTH_NAMES)
_PERIOD_RE = re.compile(
    rf"(?:\b(?:year|month|week|ever|lifetime|{_MONTH_NAMES_PATTERN}|20\d{{2}})\b)"
    r"|(?:all[\s\-]?time)",
    re.IGNORECASE,
)

# Sport detection patterns
_CYCLE_RE = re.compile(r"\b(?:cycled?|cycling|biked?|biking|rode)\b", re.IGNORECASE)
_HIKE_RE = re.compile(r"\b(?:hiked?|hiking)\b", re.IGNORECASE)
_SWIM_RE = re.compile(r"\b(?:swam|swim(?:ming)?)\b", re.IGNORECASE)
_WALK_RE = re.compile(r"\b(?:walk(?:ed)?|walking)\b", re.IGNORECASE)


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

    if re.search(r"\ball\s*time\b", t) or re.search(r"\bever\b", t) or "lifetime" in t:
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
    """Return True iff text contains both a distance/sport word and a period word."""
    return bool(_DISTANCE_RE.search(text)) and bool(_PERIOD_RE.search(text))


def parse_sport(text: str) -> set[str]:
    """Return the set of Strava activity types implied by the text.

    Checks cycling → hiking → swimming → walking in that order.
    Defaults to running types if no sport-specific word is found.
    """
    if _CYCLE_RE.search(text):
        return _CYCLE_TYPES
    if _HIKE_RE.search(text):
        return _HIKE_TYPES
    if _SWIM_RE.search(text):
        return _SWIM_TYPES
    if _WALK_RE.search(text):
        return _WALK_TYPES
    return _RUN_TYPES


def sport_label(types: set[str]) -> str:
    """Return the singular activity word for a set of Strava types."""
    if types <= _RUN_TYPES:
        return "run"
    if types <= _CYCLE_TYPES:
        return "ride"
    if types <= _HIKE_TYPES:
        return "hike"
    if types <= _SWIM_TYPES:
        return "swim"
    if types <= _WALK_TYPES:
        return "walk"
    return "activity"


def compute_km(
    activities: list[dict],
    start: date,
    end: date,
    types: set[str] | None = None,
) -> dict:
    """Sum distance_km for activities of given types within [start, end] inclusive.

    types defaults to running types when not provided.
    Returns {"total_km": float, "count": int}.
    """
    if types is None:
        types = _RUN_TYPES
    start_str = start.isoformat()
    end_str = end.isoformat()
    total_km = 0.0
    count = 0
    for act in activities:
        if act.get("type") not in types and act.get("sport_type") not in types:
            continue
        act_date = act.get("date", "")[:10]
        if start_str <= act_date <= end_str:
            total_km += act.get("distance_km", 0.0)
            count += 1
    return {"total_km": total_km, "count": count}


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
