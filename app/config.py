"""Loads the business knowledge base and environment settings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

KB_PATH = Path(os.environ.get("KNOWLEDGE_FILE", Path(__file__).resolve().parent.parent / "knowledge" / "business.yaml"))


@dataclass(frozen=True)
class MeetingType:
    id: str
    name: str
    duration: int
    when_to_use: str
    questions: list[str] = field(default_factory=list)
    agenda: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Business:
    name: str
    agent_name: str
    timezone: str
    calendar_id: str
    open_hour: int
    close_hour: int
    working_days: tuple[int, ...]
    min_notice_minutes: int
    about: str
    website: str
    phone: str
    meeting_types: tuple[MeetingType, ...]
    faqs: tuple[dict, ...]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def meeting_type(self, type_id: str | None) -> MeetingType:
        for m in self.meeting_types:
            if m.id == type_id:
                return m
        return self.meeting_types[0]  # first one is the default


@lru_cache(maxsize=1)
def load_business(path: str | None = None) -> Business:
    data = yaml.safe_load(Path(path or KB_PATH).read_text(encoding="utf-8"))
    b = data["business"]
    return Business(
        name=b["name"],
        agent_name=b.get("agent_name", "Sara"),
        timezone=b.get("timezone", "UTC"),
        calendar_id=os.environ.get("GOOGLE_CALENDAR_ID") or b.get("calendar_id", "primary"),
        open_hour=int(b.get("open_hour", 9)),
        close_hour=int(b.get("close_hour", 18)),
        working_days=tuple(b.get("working_days", [1, 2, 3, 4, 5])),
        min_notice_minutes=int(b.get("min_notice_minutes", 60)),
        about=" ".join(str(b.get("about", "")).split()),
        website=b.get("website", ""),
        phone=os.environ.get("PUBLIC_PHONE_NUMBER") or b.get("phone", ""),
        meeting_types=tuple(MeetingType(**m) for m in data["meeting_types"]),
        faqs=tuple(data.get("faqs", [])),
    )


class Settings:
    """Secrets come from environment variables only (set them in Vercel)."""

    apps_script_url = os.environ.get("APPS_SCRIPT_URL", "")
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    google_refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    vapi_public_key = os.environ.get("VAPI_PUBLIC_KEY", "")
    stt_language = os.environ.get("STT_LANGUAGE", "en")
    llm_provider = os.environ.get("LLM_PROVIDER", "openai")
    llm_model = os.environ.get("LLM_MODEL", "gpt-4.1")
    voice_provider = os.environ.get("VOICE_PROVIDER", "vapi")
    voice_id = os.environ.get("VOICE_ID", "Elliot")

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.google_refresh_token)


settings = Settings()
