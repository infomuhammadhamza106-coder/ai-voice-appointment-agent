from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import tools
from app.assistant import build_assistant, system_prompt
from app.calendar import MemoryCalendar, set_calendar
from app.config import load_business
from app.main import app
from app import scheduling as s

BIZ = load_business()
TZ = BIZ.tz


class FrozenNow:
    """Pin "now" to a known Wednesday 10:00 local time."""
    value = datetime(2026, 11, 18, 10, 0, tzinfo=TZ)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    cal = MemoryCalendar()
    set_calendar(cal)
    monkeypatch.setattr(tools, "_now", lambda biz: FrozenNow.value)
    # existing meeting Thursday 15:00-16:00
    cal.events.append({"start": {"dateTime": "2026-11-19T15:00:00+05:00"}, "end": {"dateTime": "2026-11-19T16:00:00+05:00"}})
    return cal


def test_free_slot_available():
    assert tools.check_availability({"start_time": "2026-11-19T11:00:00+05:00"}).startswith("AVAILABLE")


def test_busy_slot_offers_alternatives():
    r = tools.check_availability({"start_time": "2026-11-19T15:00:00+05:00"})
    assert r.startswith("NOT AVAILABLE") and "already booked" in r and "Open times" in r
    assert "3:00 PM" not in r.split("Open times")[1]


def test_meeting_type_duration_is_respected():
    # 14:30 + 45-min technical consultation overlaps the 15:00 meeting
    assert tools.check_availability({"start_time": "2026-11-19T14:30:00+05:00", "meeting_type": "discovery_call"}).startswith("AVAILABLE")
    assert tools.check_availability({"start_time": "2026-11-19T14:30:00+05:00", "meeting_type": "technical_consultation"}).startswith("NOT")


def test_weekend_moves_to_next_working_day():
    r = tools.check_availability({"start_time": "2026-11-21T11:00:00+05:00"})  # Saturday
    assert "Saturdays" in r and "Open times" in r


def test_after_hours_and_past():
    assert "outside our hours" in tools.check_availability({"start_time": "2026-11-19T21:00:00+05:00"})
    assert "already passed" in tools.check_availability({"start_time": "2026-11-17T11:00:00+05:00"})


def test_other_timezone_is_converted():
    # 10:00 UTC == 15:00 PKT -> busy
    assert tools.check_availability({"start_time": "2026-11-19T10:00:00Z"}).startswith("NOT")


def test_garbage_time():
    assert tools.check_availability({"start_time": "next tuesday-ish"}).startswith("ERROR")


def test_find_open_slots_lists_times():
    r = tools.find_open_slots({"date": "2026-11-19", "meeting_type": "project_kickoff"})
    assert "Open times" in r and "60-minute Project Kickoff" in r


def test_booking_creates_tailored_event(setup):
    r = tools.book_appointment({
        "name": "Ayesha Khan", "company": "SmileCare Dental", "email": "Ayesha@SmileCare.pk",
        "meeting_type": "ai_agent_demo", "start_time": "2026-11-19T12:00:00+05:00",
        "caller_brief": "Ayesha runs a dental clinic with 80 calls a day and wants AI to handle bookings.",
        "answers": [{"question": "Industry?", "answer": "Dental clinic"}],
    })
    assert r.startswith("BOOKED") and "ayesha@smilecare.pk" in r
    ev = setup.events[-1]
    assert ev["summary"] == "AI Voice Agent Demo: Ayesha Khan (SmileCare Dental)"
    assert "80 calls a day" in ev["description"] and "SUGGESTED AGENDA" in ev["description"]
    assert ev["attendees"][0]["email"] == "ayesha@smilecare.pk"
    assert ev["end"]["dateTime"].startswith("2026-11-19T12:30")
    assert "conferenceData" in ev


def test_no_double_booking():
    args = {"name": "A", "meeting_type": "discovery_call", "start_time": "2026-11-19T12:00:00+05:00", "caller_brief": "x"}
    assert tools.book_appointment(args).startswith("BOOKED")
    assert tools.book_appointment({**args, "name": "B"}).startswith("NOT BOOKED")


def test_invalid_email_rejected():
    r = tools.book_appointment({"name": "A", "email": "ali at gmail", "meeting_type": "discovery_call",
                                "start_time": "2026-11-19T12:00:00+05:00", "caller_brief": "x"})
    assert r.startswith("ERROR") and "spell" in r


def test_prompt_contains_knowledge_and_time():
    p = system_prompt(BIZ, FrozenNow.value)
    assert "Wednesday, November 18, 2026" in p and "UTC+05:00" in p
    assert "discovery_call" in p and "1,500 US dollars" in p


def test_webhook_tool_calls_and_assistant_request():
    c = TestClient(app)
    r = c.post("/api/vapi/webhook", json={"message": {"type": "tool-calls", "call": {"customer": {"number": "+923001234567"}},
        "toolCallList": [{"id": "call_1", "type": "function", "function": {"name": "book_appointment", "arguments": {
            "name": "Bilal", "meeting_type": "discovery_call", "start_time": "2026-11-20T10:00:00+05:00", "caller_brief": "Wants an app."}}}]}})
    body = r.json()
    assert body["results"][0]["toolCallId"] == "call_1" and body["results"][0]["result"].startswith("BOOKED")

    r = c.post("/api/vapi/webhook", headers={"host": "demo.vercel.app"}, json={"message": {"type": "assistant-request"}})
    a = r.json()["assistant"]
    assert a["model"]["tools"][0]["server"]["url"] == "https://demo.vercel.app/api/vapi/webhook"
    assert {t.get("function", {}).get("name") for t in a["model"]["tools"]} >= {"check_availability", "find_open_slots", "book_appointment"}


def test_health_and_web_config():
    c = TestClient(app)
    assert c.get("/api/health").json()["calendar"] == "memory"
    assert "assistant" in c.get("/api/assistant").json()


def test_apps_script_adapter_contract():
    import httpx
    from app.calendar import AppsScriptCalendar
    seen = []

    def handler(request):
        import json as _j
        body = _j.loads(request.content)
        seen.append(body)
        if body["action"] == "busy":
            return httpx.Response(200, json={"busy": [{"start": "2026-11-19T10:00:00.000Z", "end": "2026-11-19T11:00:00.000Z"}]})
        return httpx.Response(200, json={"id": "abc", "htmlLink": "x", "hangoutLink": "https://meet.google.com/x"})

    cal = AppsScriptCalendar("https://script.google.com/macros/s/X/exec", http=httpx.Client(transport=httpx.MockTransport(handler)))
    set_calendar(cal)
    r = tools.check_availability({"start_time": "2026-11-19T15:30:00+05:00"})  # 10:30Z busy
    assert r.startswith("NOT AVAILABLE")
    r = tools.book_appointment({"name": "Zain", "email": "zain@x.com", "meeting_type": "discovery_call",
                                "start_time": "2026-11-19T12:00:00+05:00", "caller_brief": "Needs a website."})
    assert r.startswith("BOOKED")
    create = [b for b in seen if b["action"] == "create"][0]
    assert create["sendInvites"] is True and create["event"]["attendees"][0]["email"] == "zain@x.com"
