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

# ------------------ CONFIG ------------------

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ------------------ PARSER ------------------

def parse_when_text(text: str):
    if not text:
        return None

    now = datetime.now(TIMEZONE)

    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now,
        },
    )

# ------------------ HELPERS ------------------

def _safe_json_loads(value: str) -> dict:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}

def _friendly_services_text() -> str:
    return "\n".join(
        f"- {svc['label']} ({svc['minutes']} mins)"
        for svc in SERVICES.values()
    )

# ------------------ TOOLS ------------------

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
            "description": "List bookings",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "cancel_customer_booking",
            "description": "Cancel booking",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        },
        {
            "type": "function",
            "name": "reschedule_customer_booking",
            "description": "Reschedule booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "new_start_iso": {"type": "string"},
                },
                "required": ["event_id", "new_start_iso"],
            },
        },
    ]

# ------------------ TOOL EXECUTION ------------------

def _execute_tool(tool_name: str, args: dict, phone: str, profile_name: str | None):
    try:
        if tool_name == "check_availability":
            barber = args["barber"]
            service = args["service"]
            start_dt = parse_when_text(args["when"])

            if not start_dt:
                return {"ok": False, "error": "Invalid date"}

            minutes = SERVICES[service]["minutes"]
            end_dt = start_dt + timedelta(minutes=minutes)

            return {
                "ok": True,
                "free": is_free(start_dt, end_dt, barber),
                "start": start_dt.isoformat(),
            }

        if tool_name == "book_appointment":
            barber = args["barber"]
            service = args["service"]
            start_dt = parse_when_text(args["when"])

            if not start_dt:
                return {"ok": False, "error": "Invalid date"}

            minutes = SERVICES[service]["minutes"]
            customer_name = args.get("customer_name") or profile_name or "Customer"

            booking = create_booking(
                phone=phone,
                service_name=service,
                start_dt=start_dt,
                minutes=minutes,
                name=customer_name,
                barber=barber,
            )

            return {"ok": True, "result": booking}

        if tool_name == "list_customer_bookings":
            return {"ok": True, "bookings": list_bookings(phone)}

        if tool_name == "cancel_customer_booking":
            success = cancel_booking(args["event_id"])
            return {"ok": success}

        if tool_name == "reschedule_customer_booking":
            new_dt = datetime.fromisoformat(args["new_start_iso"])
            result = reschedule_booking(args["event_id"], new_dt)
            return {"ok": bool(result), "result": result}

        return {"ok": False, "error": "Unknown tool"}

    except Exception as e:
        return {"ok": False, "error": str(e)}

# ------------------ MAIN AGENT ------------------

def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:

    instructions = f"""
    You are the WhatsApp receptionist for {business_name}.

    STYLE:
    - Sound like a real human texting on WhatsApp
    - Be friendly, relaxed and natural
    - Keep messages SHORT (1–2 lines max)
    - Use light emojis occasionally 🙂
    - NEVER sound like a chatbot or menu system

     IMPORTANT:
    - NEVER say things like "How can I assist you today?"
    - NEVER offer menus or options like "book or check availability"
    - NEVER ask for all details at once

    BEHAVIOUR:
    - Ask ONLY for missing info
    - If user says "haircut" → NEVER switch to beard trim
    - Stick EXACTLY to what user asked
    - If unavailable → say:
    "That time isn’t available — want another time?"

    BOOKINGS:
    - ONLY confirm using tool result
    - NEVER make up times or dates
    - Always include link if available

    TONE EXAMPLES:
    BAD ❌:
    "Please provide your name and preferred date and time"

    GOOD ✅:
    "Nice 👌 what time were you thinking?"

    BAD ❌:
    "Would you like to book or check availability?"

    GOOD ✅:
    "Yeah of course 👍 when do you want to come in?"

    """

    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=user_message,
        tools=_tool_defs(),
    )

    for _ in range(5):
        tool_calls = [x for x in response.output if getattr(x, "type", None) == "function_call"]

        if not tool_calls:
            return (response.output_text or "").strip()

        for call in tool_calls:
            args = _safe_json_loads(call.arguments)
            result = _execute_tool(call.name, args, phone, profile_name)

            if result.get("ok") and result.get("result"):
                b = result["result"]

                return f"""✅ You're all set!

✂️ {b.get('service')} with {b.get('barber')}
📅 {b.get('start')}
⏱️ {SERVICES.get(b.get('service'), {}).get('minutes')} mins

🔗 {b.get('link')}
"""

        response = client.responses.create(
            model=OPENAI_MODEL,
            previous_response_id=response.id,
            input=[
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result),
                }
            ],
        )

    return "Something went wrong — try again 👍"