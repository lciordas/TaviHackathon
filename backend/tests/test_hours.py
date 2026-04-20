"""Hours-overlap edge cases.

Day numbering follows Google Places: 0=Sunday, 1=Monday, ..., 6=Saturday.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.discovery.hours import check_overlap, is_24_7


def _wo_period(open_day: int, open_h: int, open_m: int, close_day: int, close_h: int, close_m: int) -> dict:
    return {
        "open": {"day": open_day, "hour": open_h, "minute": open_m},
        "close": {"day": close_day, "hour": close_h, "minute": close_m},
    }


def _open_only(day: int, hour: int = 0, minute: int = 0) -> dict:
    return {"open": {"day": day, "hour": hour, "minute": minute}}


# ---------------------------------------------------------------------------
# 24/7
# ---------------------------------------------------------------------------

def test_is_24_7_true_for_single_open_no_close():
    assert is_24_7([_open_only(0)]) is True


def test_is_24_7_false_for_normal_hours():
    assert is_24_7([_wo_period(1, 9, 0, 1, 17, 0)]) is False


def test_24_7_vendor_always_open():
    # 2026-04-19 is a Sunday — a normal-hours vendor would be closed.
    sched = datetime(2026, 4, 19, 23, 30, tzinfo=timezone.utc)
    res = check_overlap(sched, {"periods": [_open_only(0)]}, utc_offset_minutes=-300)
    assert res.is_open is True
    assert res.reason == "always_open"


# ---------------------------------------------------------------------------
# Missing data → permissive
# ---------------------------------------------------------------------------

def test_no_hours_published_passes():
    sched = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    res = check_overlap(sched, None, utc_offset_minutes=-300)
    assert res.is_open is True
    assert res.reason == "no_hours_published"


def test_empty_periods_passes():
    sched = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    res = check_overlap(sched, {"periods": []}, utc_offset_minutes=-300)
    assert res.is_open is True


# ---------------------------------------------------------------------------
# Normal weekday business hours
# ---------------------------------------------------------------------------

def test_open_during_business_hours_local():
    # Mon (Google day=1) 09:00-17:00 local. Vendor in US Eastern (UTC-4 in April).
    periods = [_wo_period(1, 9, 0, 1, 17, 0)]
    # 2026-04-20 is a Monday. 14:00 UTC = 10:00 EDT = within 09:00-17:00.
    sched = datetime(2026, 4, 20, 14, 0, tzinfo=timezone.utc)
    res = check_overlap(sched, {"periods": periods}, utc_offset_minutes=-240)
    assert res.is_open is True


def test_closed_outside_business_hours_local():
    periods = [_wo_period(1, 9, 0, 1, 17, 0)]
    # 2026-04-20 22:00 EDT = 02:00 UTC next day; outside 09:00-17:00.
    sched = datetime(2026, 4, 21, 2, 0, tzinfo=timezone.utc)
    res = check_overlap(sched, {"periods": periods}, utc_offset_minutes=-240)
    assert res.is_open is False
    assert "closed_at_local" in res.reason


def test_closed_on_unscheduled_day():
    # Mon-Fri 09-17 only. 2026-04-19 is a Sunday.
    periods = [_wo_period(d, 9, 0, d, 17, 0) for d in (1, 2, 3, 4, 5)]
    sched = datetime(2026, 4, 19, 14, 0, tzinfo=timezone.utc)
    res = check_overlap(sched, {"periods": periods}, utc_offset_minutes=-240)
    assert res.is_open is False


# ---------------------------------------------------------------------------
# Cross-midnight
# ---------------------------------------------------------------------------

def test_cross_midnight_period_late_night():
    # Sat 22:00 -> Sun 03:00 (Google days 6 -> 0)
    periods = [_wo_period(6, 22, 0, 0, 3, 0)]
    # 2026-04-19 is a Sunday. 06:30 UTC = 01:30 CDT (Saturday-night Sunday-morning).
    sched = datetime(2026, 4, 19, 6, 30, tzinfo=timezone.utc)
    res = check_overlap(sched, {"periods": periods}, utc_offset_minutes=-300)
    assert res.is_open is True


def test_cross_midnight_period_returns_to_close():
    # Sat 22:00 -> Sun 03:00. Sun 04:00 local should be closed.
    periods = [_wo_period(6, 22, 0, 0, 3, 0)]
    sched = datetime(2026, 4, 19, 9, 0, tzinfo=timezone.utc)  # 04:00 CDT Sunday
    res = check_overlap(sched, {"periods": periods}, utc_offset_minutes=-300)
    assert res.is_open is False
