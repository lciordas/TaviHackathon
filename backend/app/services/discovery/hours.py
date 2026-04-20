"""Business-hours overlap checks for `scheduled_for` against a vendor's hours.

Google Places `regularOpeningHours.periods` is a list of `{open, close}` objects
with `{day, hour, minute}` (day is 0=Sunday … 6=Saturday). A 24/7 vendor is
encoded as a single period with `open` only and no `close`. Cross-midnight is
expressed via `close.day != open.day`.

We convert `scheduled_for` (UTC) to the vendor's local clock using
`utc_offset_minutes` (a snapshot of the vendor's offset at fetch time — DST
boundary cases may be off by 1 hour; acceptable for v0).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY


@dataclass
class HoursCheck:
    is_open: bool
    reason: str  # human-readable; "no_hours_published" / "always_open" / "open_at_local_..." / "closed_at_local_..."


def _to_local(scheduled_for_utc: datetime, utc_offset_minutes: Optional[int]) -> datetime:
    """Convert UTC scheduled_for to the vendor's local wall-clock time."""
    if utc_offset_minutes is None:
        # If we don't know the offset, treat as UTC. Almost never happens in
        # practice — Google returns utcOffsetMinutes whenever the place exists.
        return scheduled_for_utc.astimezone(timezone.utc).replace(tzinfo=None)
    offset = timezone(timedelta(minutes=utc_offset_minutes))
    return scheduled_for_utc.astimezone(offset).replace(tzinfo=None)


def _minutes_of_week(dt: datetime) -> int:
    """Encode (weekday, hour, minute) as a single int 0..(7*1440-1).

    Python's weekday() is 0=Monday; Google uses 0=Sunday. We normalize to
    Google's convention so we can compare directly to period.day fields.
    """
    google_dow = (dt.weekday() + 1) % 7  # Mon=0 → 1, …, Sun=6 → 0
    return google_dow * MINUTES_PER_DAY + dt.hour * 60 + dt.minute


def _period_to_interval(period: dict[str, Any]) -> Optional[tuple[int, int]]:
    """Convert a Places opening_hours period to a (start, end) minutes-of-week range.

    For cross-midnight periods (close.day != open.day), returns end > start
    using a wraparound +MINUTES_PER_WEEK if needed.
    24-hour vendors (open present, close absent) are handled by the caller.
    """
    open_ = period.get("open") or period.get("openTime") or {}
    close = period.get("close") or period.get("closeTime")
    if not open_ or close is None:
        return None
    o_day = int(open_.get("day", 0))
    o_hour = int(open_.get("hour", 0))
    o_min = int(open_.get("minute", 0))
    c_day = int(close.get("day", 0))
    c_hour = int(close.get("hour", 0))
    c_min = int(close.get("minute", 0))

    start = o_day * MINUTES_PER_DAY + o_hour * 60 + o_min
    end = c_day * MINUTES_PER_DAY + c_hour * 60 + c_min
    if end <= start:
        end += MINUTES_PER_WEEK
    return (start, end)


def is_24_7(periods: list[dict[str, Any]]) -> bool:
    """A 24/7 vendor: a single period with open only, no close."""
    if not periods:
        return False
    if len(periods) != 1:
        return False
    p = periods[0]
    has_open = bool(p.get("open") or p.get("openTime"))
    has_close = bool(p.get("close") or p.get("closeTime"))
    return has_open and not has_close


def check_overlap(
    scheduled_for_utc: datetime,
    regular_opening_hours: Optional[dict[str, Any]],
    utc_offset_minutes: Optional[int],
) -> HoursCheck:
    """Return whether the vendor is open at scheduled_for.

    Permissive:
      - missing hours → pass (we don't have evidence they're closed).
      - 24/7 → pass.
    """
    if not regular_opening_hours or not regular_opening_hours.get("periods"):
        return HoursCheck(is_open=True, reason="no_hours_published")

    periods = regular_opening_hours["periods"]
    if is_24_7(periods):
        return HoursCheck(is_open=True, reason="always_open")

    local = _to_local(scheduled_for_utc, utc_offset_minutes)
    target = _minutes_of_week(local)

    # Try both target and target+1week to handle wrap intervals starting late in the week.
    for t in (target, target + MINUTES_PER_WEEK):
        for period in periods:
            interval = _period_to_interval(period)
            if interval is None:
                continue
            start, end = interval
            if start <= t < end:
                return HoursCheck(
                    is_open=True,
                    reason=f"open_at_local_{local.strftime('%a_%H:%M')}",
                )

    return HoursCheck(
        is_open=False,
        reason=f"closed_at_local_{local.strftime('%a_%H:%M')}",
    )
