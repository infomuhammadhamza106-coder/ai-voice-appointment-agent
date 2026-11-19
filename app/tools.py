"""Tool handlers called by the voice agent during a call. Each returns a short plain-text
result that the LLM turns into speech, so results are written to be easy to say aloud."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta

from . import scheduling as s
from .calendar import get_calendar
from .config import Business, load_business

log = logging.getLogger("tools")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def _now(biz: Business) -> datetime:
    return datetime.now(biz.tz)


def _alternatives(day, minutes, target, biz, now) -> str:
    cal = get_calendar()
    for attempt in range(2):
        open_, close = s.day_window(day, biz)
        busy = cal.busy(open_, close, biz.calendar_id)
        slots = s.open_slots(day, minutes, busy, biz, now)
        if slots:
            near = s.nearest(slots, target if attempt == 0 else open_)
            prefix = "" if attempt == 0 else f"Nothing is open that day. The next open day is {day.strftime('%A, %B %-d')}: "
            return prefix + "Open times: " + ", ".join(s.say_time(t) for t in near) + "."
        day = s.next_working_day(day, biz)
    return "No open times in the next two working days. Ask the caller for another day."


def check_availability(args: dict, biz: Business | None = None) -> str:
    biz = biz or load_business()
    mt = biz.meeting_type(args.get("meeting_type"))
    minutes = int(args.get("duration_minutes") or mt.duration)
    start = s.parse_start(str(args.get("start_time", "")), biz)
    if not start:
        return "ERROR: I couldn't understand that date and time. Ask the caller to repeat it."
    now = _now(biz)
    rule = s.check_rules(start, minutes, biz, now)
    day = start.date() if start.date() >= now.date() else now.date()
    if not rule.ok:
        return f"NOT AVAILABLE: {rule.reason} {_alternatives(day, minutes, start, biz, now)}"
    end = start + timedelta(minutes=minutes)
    if s.overlaps(start, end, get_calendar().busy(start, end, biz.calendar_id)):
        return f"NOT AVAILABLE: {s.say(start)} is already booked. {_alternatives(day, minutes, start, biz, now)}"
    return f"AVAILABLE: {s.say(start)} is free for a {minutes}-minute {mt.name}."


def find_open_slots(args: dict, biz: Business | None = None) -> str:
    biz = biz or load_business()
    mt = biz.meeting_type(args.get("meeting_type"))
    now = _now(biz)
    day = s.parse_day(str(args.get("date", "")), biz) or now.date()
    if day < now.date():
        day = now.date()
    if day.isoweekday() not in biz.working_days:
        day = s.next_working_day(day, biz)
    open_, _ = s.day_window(day, biz)
    return f"For a {mt.duration}-minute {mt.name} on {day.strftime('%A, %B %-d')}: " + _alternatives(
        day, mt.duration, open_ + timedelta(hours=3), biz, now)


def build_event(args: dict, start: datetime, minutes: int, biz: Business) -> dict:
    mt = biz.meeting_type(args.get("meeting_type"))
    name = (args.get("name") or "Caller").strip()
    company = (args.get("company") or "").strip()
    email = (args.get("email") or "").strip().lower()
    phone = (args.get("phone") or "").strip()
    brief = (args.get("caller_brief") or "").strip()
    answers = args.get("answers") or []

    lines = [f"Booked by {biz.agent_name}, the {biz.name} AI voice agent.", "",
             "CALLER", f"- Name: {name}"]
    if company:
        lines.append(f"- Company: {company}")
    lines += [f"- Phone: {phone or 'n/a'}", f"- Email: {email or 'n/a'}", "",
              f"MEETING TYPE: {mt.name} ({minutes} min)", ""]
    if brief:
        lines += ["CALLER BRIEF", brief, ""]
    if answers:
        lines.append("DISCOVERY ANSWERS")
        for a in answers:
            if isinstance(a, dict):
                lines.append(f"- {a.get('question', '')}: {a.get('answer', '')}")
            else:
                lines.append(f"- {a}")
        lines.append("")
    lines.append("SUGGESTED AGENDA")
    lines += [f"{i}. {item}" for i, item in enumerate(mt.agenda, 1)]

    title = f"{mt.name}: {name}" + (f" ({company})" if company else "")
    event = {
        "summary": title,
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": biz.timezone},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": biz.timezone},
        "attendees": [{"email": email, "displayName": name}] if email else [],
        "reminders": {"useDefault": True},
        "conferenceData": {"createRequest": {"requestId": uuid.uuid4().hex,
                                             "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
    }
    return event


def book_appointment(args: dict, biz: Business | None = None) -> str:
    biz = biz or load_business()
    mt = biz.meeting_type(args.get("meeting_type"))
    minutes = int(args.get("duration_minutes") or mt.duration)
    start = s.parse_start(str(args.get("start_time", "")), biz)
    if not start:
        return "ERROR: Invalid start_time. Confirm the date and time with the caller and try again."
    if not (args.get("name") or "").strip():
        return "ERROR: Caller name is missing. Ask for their name first."
    email = (args.get("email") or "").strip().lower()
    if email and not EMAIL_RE.match(email):
        return f"ERROR: The email '{email}' doesn't look valid. Ask the caller to spell it again."

    now = _now(biz)
    rule = s.check_rules(start, minutes, biz, now)
    if not rule.ok:
        return f"NOT BOOKED: {rule.reason} {_alternatives(start.date(), minutes, start, biz, now)}"
    cal = get_calendar()
    end = start + timedelta(minutes=minutes)
    if s.overlaps(start, end, cal.busy(start, end, biz.calendar_id)):  # re-check: no double booking
        return f"NOT BOOKED: that slot was just taken. {_alternatives(start.date(), minutes, start, biz, now)}"

    ev = cal.create_event(biz.calendar_id, build_event(args, start, minutes, biz), send_invites=bool(email))
    log.info("booked event %s for %s at %s", ev.get("id"), args.get("name"), start.isoformat())
    parts = [f"BOOKED: {mt.name} for {args['name'].strip()} on {s.say(start)} Pakistan time, {minutes} minutes."
             if biz.timezone == "Asia/Karachi" else
             f"BOOKED: {mt.name} for {args['name'].strip()} on {s.say(start)} ({biz.timezone}), {minutes} minutes."]
    if email:
        parts.append(f"A calendar invite with a Google Meet link was sent to {email}.")
    else:
        parts.append("No email was given, so no invite was sent.")
    return " ".join(parts)


HANDLERS = {
    "check_availability": check_availability,
    "find_open_slots": find_open_slots,
    "book_appointment": book_appointment,
}


def run_tool(name: str, args: dict) -> str:
    fn = HANDLERS.get(name)
    if not fn:
        return f"ERROR: unknown tool {name}"
    try:
        return fn(args or {})
    except Exception as e:  # never crash the call, let the agent recover gracefully
        log.exception("tool %s failed", name)
        return f"ERROR: the calendar system had a problem ({type(e).__name__}). Apologize and offer to have the team call back."
