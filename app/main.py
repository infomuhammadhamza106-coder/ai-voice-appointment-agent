"""FastAPI app: Vapi webhook (assistant-request + tool-calls) and endpoints for the web test page."""
from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .assistant import build_assistant
from .calendar import get_calendar
from .config import load_business, settings
from .tools import run_tool

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("webhook")

app = FastAPI(title="AI Voice Appointment Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


def base_url(request: Request) -> str:
    if os.environ.get("PUBLIC_BASE_URL"):
        return os.environ["PUBLIC_BASE_URL"].rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    proto = request.headers.get("x-forwarded-proto", "https")
    return f"{proto}://{host}"


@app.get("/api/health")
def health():
    biz = load_business()
    return {"ok": True, "business": biz.name, "calendar": get_calendar().name,
            "vapi_public_key_set": bool(settings.vapi_public_key), "timezone": biz.timezone}


@app.get("/api/assistant")
def assistant_for_web(request: Request):
    """Used by the web test page: returns the public key + a fresh assistant config."""
    biz = load_business()
    return {"publicKey": settings.vapi_public_key, "phone": biz.phone, "business": biz.name,
            "assistant": build_assistant(base_url(request))}


def _tool_calls(msg: dict) -> list[tuple[str, str, dict]]:
    calls = msg.get("toolCallList") or msg.get("toolCalls") or \
        [t.get("toolCall", {}) for t in msg.get("toolWithToolCallList", [])]
    out = []
    for c in calls:
        fn = c.get("function") or {}
        name = fn.get("name") or c.get("name", "")
        args = fn.get("arguments", c.get("arguments", {})) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        out.append((c.get("id", ""), name, args))
    return out


@app.post("/api/vapi/webhook")
async def vapi_webhook(request: Request):
    body = await request.json()
    msg = body.get("message", {})
    kind = msg.get("type")

    if kind == "assistant-request":  # inbound phone call: Vapi asks which assistant to use
        return {"assistant": build_assistant(base_url(request))}

    if kind == "tool-calls":
        customer = (msg.get("call") or {}).get("customer") or {}
        results = []
        for call_id, name, args in _tool_calls(msg):
            if name == "book_appointment" and not args.get("phone") and customer.get("number"):
                args["phone"] = customer["number"]  # caller ID from the phone network
            result = run_tool(name, args)
            log.info("tool %s(%s) -> %s", name, json.dumps(args)[:300], result)
            results.append({"toolCallId": call_id, "result": result})
        return {"results": results}

    if kind == "end-of-call-report":
        log.info("call ended: %s | summary: %s", msg.get("endedReason"), (msg.get("analysis") or {}).get("summary"))
    return {}
