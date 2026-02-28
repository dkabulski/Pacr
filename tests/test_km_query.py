"""Tests for tgbot.km_query — local km query answering without Claude API."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> date:
    return date.today()


# ---------------------------------------------------------------------------
# parse_period
# ---------------------------------------------------------------------------


def test_parse_period_last_year() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    y = today.year
    result = parse_period("how many km last year")
    assert result == (date(y - 1, 1, 1), date(y - 1, 12, 31))


def test_parse_period_this_year() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    result = parse_period("how far this year")
    assert result == (date(today.year, 1, 1), today)


def test_parse_period_last_month() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    y, m = today.year, today.month
    if m == 1:
        pm, py = 12, y - 1
    else:
        pm, py = m - 1, y
    last_day = monthrange(py, pm)[1]
    result = parse_period("how far did I run last month")
    assert result == (date(py, pm, 1), date(py, pm, last_day))


def test_parse_period_this_month() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    result = parse_period("distance this month")
    assert result == (date(today.year, today.month, 1), today)


def test_parse_period_last_week() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    result = parse_period("km last week")
    assert result == (last_monday, last_sunday)


def test_parse_period_this_week() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    this_monday = today - timedelta(days=today.weekday())
    result = parse_period("how far did I run this week")
    assert result == (this_monday, today)


def test_parse_period_all_time() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    result = parse_period("how many km all time")
    assert result == (date(2000, 1, 1), today)


def test_parse_period_ever() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    result = parse_period("how far have I run ever")
    assert result == (date(2000, 1, 1), today)


def test_parse_period_full_year() -> None:
    from tgbot.km_query import parse_period

    result = parse_period("in 2024")
    assert result == (date(2024, 1, 1), date(2024, 12, 31))


def test_parse_period_current_year_by_number() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    result = parse_period(f"how many km in {today.year}")
    assert result == (date(today.year, 1, 1), today)


def test_parse_period_named_month_with_year() -> None:
    from tgbot.km_query import parse_period

    result = parse_period("in January 2025")
    assert result == (date(2025, 1, 1), date(2025, 1, 31))


def test_parse_period_named_month_abbreviation() -> None:
    from tgbot.km_query import parse_period

    result = parse_period("km in jan 2025")
    assert result == (date(2025, 1, 1), date(2025, 1, 31))


def test_parse_period_named_month_no_year_uses_current() -> None:
    from tgbot.km_query import parse_period

    today = _today()
    # Use a past month so we can predict the full range
    result = parse_period("km in december")
    assert result is not None
    start, end = result
    assert start.month == 12
    assert start.day == 1
    assert end.day == 31


def test_parse_period_unrecognised() -> None:
    from tgbot.km_query import parse_period

    result = parse_period("unrecognised gibberish")
    assert result is None


def test_parse_period_no_period_word() -> None:
    from tgbot.km_query import parse_period

    result = parse_period("how are you today")
    assert result is None


# ---------------------------------------------------------------------------
# is_km_query
# ---------------------------------------------------------------------------


def test_is_km_query_km_last_year() -> None:
    from tgbot.km_query import is_km_query

    assert is_km_query("how many km last year") is True


def test_is_km_query_far_run_this_week() -> None:
    from tgbot.km_query import is_km_query

    assert is_km_query("how far did I run this week") is True


def test_is_km_query_total_distance_year() -> None:
    from tgbot.km_query import is_km_query

    assert is_km_query("what's my total distance for 2025") is True


def test_is_km_query_how_are_you_false() -> None:
    from tgbot.km_query import is_km_query

    assert is_km_query("how are you today") is False


def test_is_km_query_plan_my_week_false() -> None:
    from tgbot.km_query import is_km_query

    # "week" is a period word but there is no distance word
    assert is_km_query("plan my week") is False


def test_is_km_query_kilometres_this_month() -> None:
    from tgbot.km_query import is_km_query

    assert is_km_query("total kilometres this month") is True


def test_is_km_query_jog_ever() -> None:
    from tgbot.km_query import is_km_query

    assert is_km_query("how many miles have I jogged ever") is True


def test_is_km_query_only_period_no_distance() -> None:
    from tgbot.km_query import is_km_query

    assert is_km_query("what happened last week") is False


# ---------------------------------------------------------------------------
# compute_km
# ---------------------------------------------------------------------------

_SAMPLE_ACTIVITIES = [
    {"date": "2025-01-10T08:00:00Z", "distance_km": 10.0, "type": "Run"},
    {"date": "2025-01-15T09:00:00Z", "distance_km": 15.5, "type": "Run"},
    {"date": "2025-02-01T07:30:00Z", "distance_km": 8.0, "type": "Run"},
    {"date": "2025-03-20T06:00:00Z", "distance_km": 21.1, "type": "Run"},
    {"date": "2025-01-12T08:00:00Z", "distance_km": 40.0, "type": "Ride"},
    {"date": "2025-01-20T10:00:00Z", "distance_km": 5.0, "type": "Walk"},
]


def test_compute_km_filters_by_range() -> None:
    from tgbot.km_query import compute_km

    result = compute_km(_SAMPLE_ACTIVITIES, date(2025, 1, 1), date(2025, 1, 31))
    assert result["total_km"] == 25.5
    assert result["runs"] == 2


def test_compute_km_excludes_outside_range() -> None:
    from tgbot.km_query import compute_km

    result = compute_km(_SAMPLE_ACTIVITIES, date(2025, 2, 1), date(2025, 2, 28))
    assert result["total_km"] == 8.0
    assert result["runs"] == 1


def test_compute_km_full_range() -> None:
    from tgbot.km_query import compute_km

    result = compute_km(_SAMPLE_ACTIVITIES, date(2025, 1, 1), date(2025, 12, 31))
    assert result["total_km"] == pytest.approx(54.6, abs=0.01)
    assert result["runs"] == 4


def test_compute_km_empty_activities() -> None:
    from tgbot.km_query import compute_km

    result = compute_km([], date(2025, 1, 1), date(2025, 12, 31))
    assert result == {"total_km": 0.0, "runs": 0}


def test_compute_km_boundary_inclusive() -> None:
    from tgbot.km_query import compute_km

    # Exactly on start and end boundaries
    result = compute_km(_SAMPLE_ACTIVITIES, date(2025, 1, 10), date(2025, 1, 10))
    assert result["total_km"] == 10.0
    assert result["runs"] == 1


def test_compute_km_no_match() -> None:
    from tgbot.km_query import compute_km

    result = compute_km(_SAMPLE_ACTIVITIES, date(2024, 1, 1), date(2024, 12, 31))
    assert result == {"total_km": 0.0, "runs": 0}
