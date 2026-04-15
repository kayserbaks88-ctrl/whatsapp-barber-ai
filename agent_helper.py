import json
import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
import dateparser
from openai import OpenAI

from calendar_helper import (
    BARBERS,
    SERVICES,
    cancel_booking,
    create_booking,
    is_free,
    list_bookings,
    reschedule_booking,
)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# =========================
# 🔥 FIXED DATE PARSER
# =========================
def parse_when_text(text: str):
    if not text:
        return None

    now = datetime.now(TIMEZONE)

    parsed = dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now,
        },
    )

    return parsed


# =========================
# HELPERS
# =========================
def _safe_json_loads(value: str) -> dict:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _friendly_services_text() -> str:
    return "\n".join(
        [f"- {svc['label']} ({svc['minutes']} mins)" for svc in SERVICES.values()]
    )


# =========================
# 🔥 CLEAN TOOL DEFS
# =========================
def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "check_availability",
            "description": "Check if a time is free",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string", "enum": list(BARBERS.keys())},
                    "service": {"type": "string", "enum": list(SERVICES.keys())},
                    "when": {"type": "string"},
                },
                "required": ["barber", "service", "when"],
            },
        },
        {
            "type": "function",
            "name": "book_appointment",
            "description": "Create a booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string"},
                    "service": {"type": "string"},
                    "when": {"type": "string"},
                    "customer_name": {"type": "string"},
                },
                "required": ["barber", "service", "when"],
            },
        },
        {
            "type": "function",
            "name": "list_customer_bookings",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "cancel_customer_booking",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        },
        {
            "type": "function",
            "name": "reschedule_customer_booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "new_when": {"type": "string"},
                },
                "required": ["event_id", "new_when"],
            },
        },
    ]


# =========================
# 🔥 TOOL EXECUTION (FIXED)
# =========================
def _execute_tool(tool_name: str, args: dict, phone: str, profile_name: str | None):
    try:
        if tool_name == "check_availability":
            start_dt = parse_when_text(args["when"])
            if not start_dt:
                return {"ok": False}

            minutes = SERVICES[args["service"]]["minutes"]
            end_dt = start_dt + timedelta(minutes=minutes)

            free = is_free(start_dt, end_dt, args["barber"])

            return {
                "ok": True,
                "free": free,
                "start": start_dt.isoformat(),
                "service": args["service"],
                "barber": args["barber"],
            }

        if tool_name == "book_appointment":
            start_dt = parse_when_text(args["when"])
            if not start_dt:
                return {"ok": False}

            minutes = SERVICES[args["service"]]["minutes"]

            result = create_booking(
                phone=phone,
                service_name=args["service"],
                start_dt=start_dt,
                minutes=minutes,
                name=args.get("customer_name") or profile_name or "Customer",
                barber=args["barber"],
            )

            return {"ok": True, "result": result}

        if tool_name == "list_customer_bookings":
            return {"ok": True, "bookings": list_bookings(phone)}

        if tool_name == "cancel_customer_booking":
            cancel_booking(args["event_id"])
            return {"ok": True}

        if tool_name == "reschedule_customer_booking":
            new_dt = parse_when_text(args["new_when"])
            result = reschedule_booking(args["event_id"], new_dt)
            return {"ok": True, "result": result}

    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {"ok": False}


# =========================
# 🔥 MAIN AGENT (UNCHANGED FEEL)
# =========================
def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=user_message,
        tools=_tool_defs(),
    )

    for item in response.output:
        if getattr(item, "type", None) == "function_call":
            args = _safe_json_loads(item.arguments)
            result = _execute_tool(item.name, args, phone, profile_name)

            if result.get("ok") and result.get("result"):
                booking = result["result"]

                return f"""✅ You're all set!

✂️ {booking.get('service', '').title()} with {booking.get('barber', '').title()}
📅 {booking.get('start')}
⏱️ Duration: {SERVICES.get(booking.get('service', ''), {}).get('minutes')} mins

🔗 {booking.get('link')}
"""

    return (response.output_text or "").strip() or "Just send that again 👍"