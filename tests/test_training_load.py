"""Tests for training_load — TSS estimation and CTL/ATL/TSB metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# _estimate_tss
# ---------------------------------------------------------------------------


def test_estimate_tss_with_hr_at_lthr() -> None:
    from coach_utils.training_load import _estimate_tss

    act = {"moving_time_s": 3600, "avg_hr": 170.0, "distance_km": 10.0}
    tss = _estimate_tss(act, lthr=170.0)
    assert tss == pytest.approx(100.0, abs=0.1)


def test_estimate_tss_with_hr_below_lthr() -> None:
    from coach_utils.training_load import _estimate_tss

    # IF = 136/170 = 0.8 → TSS = 1h * 0.64 * 100 = 64
    act = {"moving_time_s": 3600, "avg_hr": 136.0, "distance_km": 10.0}
    tss = _estimate_tss(act, lthr=170.0)
    assert tss == pytest.approx(64.0, abs=0.5)


def test_estimate_tss_no_hr_uses_distance() -> None:
    from coach_utils.training_load import _estimate_tss

    act = {"moving_time_s": 3600, "distance_km": 10.0}
    tss = _estimate_tss(act, lthr=170.0)
    assert tss == pytest.approx(60.0)


def test_estimate_tss_cap_at_300() -> None:
    from coach_utils.training_load import _estimate_tss

    # IF = 200/100 = 2.0, hours = 2 → TSS = 2 * 4 * 100 = 800 → capped at 300
    act = {"moving_time_s": 7200, "avg_hr": 200.0, "distance_km": 30.0}
    tss = _estimate_tss(act, lthr=100.0)
    assert tss == 300.0


def test_estimate_tss_zero() -> None:
    from coach_utils.training_load import _estimate_tss

    act: dict = {}
    tss = _estimate_tss(act, lthr=170.0)
    assert tss == 0.0


# ---------------------------------------------------------------------------
# calculate_load_metrics
# ---------------------------------------------------------------------------


def test_calculate_load_empty() -> None:
    from coach_utils.training_load import calculate_load_metrics

    metrics = calculate_load_metrics([], lthr=170.0)
    assert metrics["ctl"] == 0.0
    assert metrics["atl"] == 0.0
    assert metrics["tsb"] == 0.0


def test_calculate_load_single_activity_today() -> None:
    from coach_utils.training_load import calculate_load_metrics

    today = datetime.now(tz=UTC).date().isoformat()
    acts = [{"date": today + "T00:00:00Z", "moving_time_s": 3600, "avg_hr": 170.0, "distance_km": 10.0}]
    metrics = calculate_load_metrics(acts, lthr=170.0)
    # ATL uses 7-day constant → rises faster than CTL (42-day constant)
    assert metrics["atl"] > metrics["ctl"]
    assert metrics["tsb"] < 0


def test_calculate_load_old_activity_decays_to_zero() -> None:
    from coach_utils.training_load import calculate_load_metrics

    # Activity 400 days ago is outside the 365-day window → not counted
    old_date = (datetime.now(tz=UTC).date() - timedelta(days=400)).isoformat()
    acts = [{"date": old_date + "T00:00:00Z", "moving_time_s": 3600, "avg_hr": 170.0, "distance_km": 10.0}]
    metrics = calculate_load_metrics(acts, lthr=170.0)
    assert metrics["ctl"] == 0.0
    assert metrics["atl"] == 0.0
    assert metrics["tsb"] == 0.0


def test_calculate_load_tsb_equals_ctl_minus_atl() -> None:
    from coach_utils.training_load import calculate_load_metrics

    today = datetime.now(tz=UTC).date().isoformat()
    acts = [
        {"date": today + "T00:00:00Z", "moving_time_s": 3600, "avg_hr": 170.0, "distance_km": 10.0},
        {"date": (datetime.now(tz=UTC).date() - timedelta(days=3)).isoformat() + "T00:00:00Z",
         "moving_time_s": 2700, "avg_hr": 155.0, "distance_km": 8.0},
    ]
    metrics = calculate_load_metrics(acts, lthr=170.0)
    assert metrics["tsb"] == pytest.approx(metrics["ctl"] - metrics["atl"], abs=0.2)


# ---------------------------------------------------------------------------
# weekly_km_trend
# ---------------------------------------------------------------------------


def test_weekly_km_trend_empty() -> None:
    from coach_utils.training_load import weekly_km_trend

    result = weekly_km_trend([], n_weeks=12)
    assert len(result) == 12
    assert all(w["km"] == 0.0 for w in result)


def test_weekly_km_trend_n_weeks_length() -> None:
    from coach_utils.training_load import weekly_km_trend

    result = weekly_km_trend([], n_weeks=8)
    assert len(result) == 8


def test_weekly_km_trend_week_key_format() -> None:
    from coach_utils.training_load import weekly_km_trend

    result = weekly_km_trend([], n_weeks=4)
    import re
    for w in result:
        assert re.match(r"^\d{4}-W\d{2}$", w["week"]), f"Bad format: {w['week']}"


def test_weekly_km_trend_grouping() -> None:
    from coach_utils.training_load import weekly_km_trend

    today = datetime.now(tz=UTC).date()
    monday = today - timedelta(days=today.weekday())
    # Two runs this week
    acts = [
        {"date": monday.isoformat() + "T08:00:00Z", "distance_km": 10.0},
        {"date": monday.isoformat() + "T18:00:00Z", "distance_km": 5.0},
    ]
    result = weekly_km_trend(acts, n_weeks=4)
    # The last entry should be current week
    current_week = result[-1]
    assert current_week["km"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# volume_spike_check
# ---------------------------------------------------------------------------


def test_volume_spike_check_no_data() -> None:
    from coach_utils.training_load import volume_spike_check

    assert volume_spike_check([]) is None


def test_volume_spike_check_no_baseline() -> None:
    from coach_utils.training_load import volume_spike_check

    today = datetime.now(tz=UTC).date()
    monday = today - timedelta(days=today.weekday())
    acts = [{"date": monday.isoformat() + "T00:00:00Z", "distance_km": 50.0}]
    # No last-week activities → last_week_km = 0 → None
    assert volume_spike_check(acts) is None


def test_volume_spike_check_triggers() -> None:
    from coach_utils.training_load import volume_spike_check

    today = datetime.now(tz=UTC).date()
    monday = today - timedelta(days=today.weekday())
    last_monday = monday - timedelta(weeks=1)
    acts = [
        {"date": monday.isoformat() + "T00:00:00Z", "distance_km": 60.0},
        {"date": last_monday.isoformat() + "T00:00:00Z", "distance_km": 50.0},
    ]
    result = volume_spike_check(acts)
    assert result is not None
    assert "60.0" in result
    assert "50.0" in result


def test_volume_spike_check_no_trigger_exactly_10pct() -> None:
    from coach_utils.training_load import volume_spike_check

    today = datetime.now(tz=UTC).date()
    monday = today - timedelta(days=today.weekday())
    last_monday = monday - timedelta(weeks=1)
    # 55 vs 50 → 55 > 55 is False
    acts = [
        {"date": monday.isoformat() + "T00:00:00Z", "distance_km": 55.0},
        {"date": last_monday.isoformat() + "T00:00:00Z", "distance_km": 50.0},
    ]
    assert volume_spike_check(acts) is None


def test_volume_spike_check_no_trigger_5pct() -> None:
    from coach_utils.training_load import volume_spike_check

    today = datetime.now(tz=UTC).date()
    monday = today - timedelta(days=today.weekday())
    last_monday = monday - timedelta(weeks=1)
    # 52.5 vs 50 → 52.5 > 55 is False
    acts = [
        {"date": monday.isoformat() + "T00:00:00Z", "distance_km": 52.5},
        {"date": last_monday.isoformat() + "T00:00:00Z", "distance_km": 50.0},
    ]
    assert volume_spike_check(acts) is None
