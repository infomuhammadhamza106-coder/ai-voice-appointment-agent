"""Google Calendar client (REST via httpx) plus an in-memory fallback for demo and tests."""
from __future__ import annotations

import time as _time
import uuid
from datetime import datetime
from typing import Protocol

import httpx

from .config import settings

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_API = "https://www.googleapis.com/calendar/v3"


class Calendar(Protocol):
    name: str

    def busy(self, start: datetime, end: datetime, calendar_id: str) -> list[tuple[datetime, datetime]]: ...

    def create_event(self, calendar_id: str, event: dict, send_invites: bool) -> dict: ...


class GoogleCalendar:
    name = "google"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, http: httpx.Client | None = None):
        self._cid, self._secret, self._refresh = client_id, client_secret, refresh_token
        self._token, self._exp = "", 0.0
        self._http = http or httpx.Client(timeout=8.0)

    def _auth(self) -> dict:
        if not self._token or _time.time() > self._exp - 60:
            r = self._http.post(GOOGLE_TOKEN_URL, data={
                "client_id": self._cid, "client_secret": self._secret,
                "refresh_token": self._refresh, "grant_type": "refresh_token",
            })
            r.raise_for_status()
            body = r.json()
            self._token, self._exp = body["access_token"], _time.time() + int(body.get("expires_in", 3600))
        return {"Authorization": f"Bearer {self._token}"}

    def busy(self, start, end, calendar_id):
        r = self._http.post(f"{CAL_API}/freeBusy", headers=self._auth(), json={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": calendar_id}],
        })
        r.raise_for_status()
        cals = r.json().get("calendars", {})
        cal = cals.get(calendar_id) or next(iter(cals.values()), {})
        if cal.get("errors"):
            raise RuntimeError(f"freeBusy error: {cal['errors']}")
        return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                 datetime.fromisoformat(b["end"].replace("Z", "+00:00"))) for b in cal.get("busy", [])]

    def create_event(self, calendar_id, event, send_invites):
        params = {"sendUpdates": "all" if send_invites else "none", "conferenceDataVersion": 1}
        r = self._http.post(f"{CAL_API}/calendars/{calendar_id}/events", headers=self._auth(), params=params, json=event)
        if r.status_code == 400 and "conferenceData" in event:
            # Some calendars can't create Meet links; retry without one.
            event = {k: v for k, v in event.items() if k != "conferenceData"}
            r = self._http.post(f"{CAL_API}/calendars/{calendar_id}/events", headers=self._auth(), params=params, json=event)
        r.raise_for_status()
        return r.json()


class AppsScriptCalendar:
    """Talks to the Google Apps Script web app in google-apps-script/Code.gs.
    It runs inside the owner's Google account, so no Cloud project or OAuth client is needed."""

    name = "google-apps-script"

    def __init__(self, url: str, http: httpx.Client | None = None):
        self._url = url
        self._http = http or httpx.Client(timeout=15.0, follow_redirects=True)

    def _post(self, payload: dict) -> dict:
        r = self._http.post(self._url, json=payload)
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(f"Apps Script error: {body['error']}")
        return body

    def busy(self, start, end, calendar_id):
        body = self._post({"action": "busy", "start": start.isoformat(), "end": end.isoformat()})
        return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                 datetime.fromisoformat(b["end"].replace("Z", "+00:00"))) for b in body.get("busy", [])]

    def create_event(self, calendar_id, event, send_invites):
        return self._post({"action": "create", "event": event, "sendInvites": send_invites})


class MemoryCalendar:
    """Used when Google credentials are not configured (local dev, tests, dry-run demos)."""

    name = "memory"

    def __init__(self):
        self.events: list[dict] = []

    def busy(self, start, end, calendar_id):
        out = []
        for e in self.events:
            s = datetime.fromisoformat(e["start"]["dateTime"])
            en = datetime.fromisoformat(e["end"]["dateTime"])
            if s < end and en > start:
                out.append((s, en))
        return out

    def create_event(self, calendar_id, event, send_invites):
        ev = {**event, "id": uuid.uuid4().hex[:12], "htmlLink": "memory://event",
              "hangoutLink": "https://meet.google.com/demo-link" if "conferenceData" in event else None}
        self.events.append(ev)
        return ev


_calendar: Calendar | None = None


def get_calendar() -> Calendar:
    global _calendar
    if _calendar is None:
        if settings.apps_script_url:
            _calendar = AppsScriptCalendar(settings.apps_script_url)
        elif settings.google_configured:
            _calendar = GoogleCalendar(settings.google_client_id, settings.google_client_secret, settings.google_refresh_token)
        else:
            _calendar = MemoryCalendar()
    return _calendar


def set_calendar(cal: Calendar) -> None:  # for tests
    global _calendar
    _calendar = cal
