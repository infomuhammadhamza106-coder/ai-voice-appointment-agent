"""Pure scheduling logic: parse caller times, validate against business rules, find open slots.

Kept free of I/O so it is fully unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import Business

Busy = list[tuple[datetime, datetime]]


def parse_start(value: str, biz: Business) -> datetime | None:
    """ISO 8601 with or without offset. Naive times are taken as business-local time."""
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=biz.tz)
    return dt.astimezone(biz.tz)


def parse_day(value: str, biz: Business) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except (ValueError, AttributeError):
        return None


def day_window(d: date, biz: Business) -> tuple[datetime, datetime]:
    return (
        datetime.combine(d, time(biz.open_hour), tzinfo=biz.tz),
        datetime.combine(d, time(biz.close_hour), tzinfo=biz.tz),
    )


def overlaps(start: datetime, end: datetime, busy: Busy) -> bool:
    return any(start < b_end and end > b_start for b_start, b_end in busy)


def say(dt: datetime) -> str:
    """Speech-friendly: 'Tuesday, November 25 at 3:00 PM'."""
    return dt.strftime("%A, %B %-d at %-I:%M %p")


def say_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p")


@dataclass
class SlotCheck:
    ok: bool
    reason: str = ""


def check_rules(start: datetime, minutes: int, biz: Business, now: datetime) -> SlotCheck:
    end = start + timedelta(minutes=minutes)
    open_, close = day_window(start.date(), biz)
    if start < now + timedelta(minutes=biz.min_notice_minutes):
        return SlotCheck(False, "That time has already passed or is too soon to book.")
    if start.isoweekday() not in biz.working_days:
        return SlotCheck(False, f"We don't take meetings on {start.strftime('%A')}s.")
    if start < open_ or end > close:
        return SlotCheck(
            False,
            f"That's outside our hours of {biz.open_hour % 12 or 12} AM to {biz.close_hour % 12 or 12} PM.",
        )
    return SlotCheck(True)


def open_slots(d: date, minutes: int, busy: Busy, biz: Business, now: datetime, step: int = 30) -> list[datetime]:
    if d.isoweekday() not in biz.working_days:
        return []
    open_, close = day_window(d, biz)
    earliest = now + timedelta(minutes=biz.min_notice_minutes)
    slots, t = [], open_
    while t + timedelta(minutes=minutes) <= close:
        if t >= earliest and not overlaps(t, t + timedelta(minutes=minutes), busy):
            slots.append(t)
        t += timedelta(minutes=step)
    return slots


def nearest(slots: list[datetime], target: datetime, k: int = 3) -> list[datetime]:
    return sorted(sorted(slots, key=lambda s: abs((s - target).total_seconds()))[:k])


def next_working_day(d: date, biz: Business) -> date:
    d += timedelta(days=1)
    while d.isoweekday() not in biz.working_days:
        d += timedelta(days=1)
    return d
