import json
import os
from datetime import datetime, timedelta
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

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))


def parse_natural_time(text: str):
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )


def _safe_json_loads(value: str):
    try:
        return json.loads(value or "{}")
    except:
        return {}


def _tool_defs():
    return [
        {
            "type": "function",
            "name": "book_appointment",
            "description": "Create a booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string", "enum": list(BARBERS.keys())},
                    "service": {"type": "string", "enum": list(SERVICES.keys())},
                    "start_iso": {"type": "string"},
                    "when_text": {"type": "string"},
                },
                "required": ["barber", "service"],
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
                    "when_text": {"type": "string"},
                },
                "required": ["event_id"],
            },
        },
    ]


def _execute_tool(tool_name, args, phone, profile_name, session):
    try:
        phone = phone.replace("whatsapp:", "").strip()

        # ---------------- BOOK ----------------
        if tool_name == "book_appointment":
            barber = args.get("barber")
            service = args.get("service")

            # Parse time
            start_dt = None

            if args.get("start_iso"):
                try:
                    start_dt = datetime.fromisoformat(args["start_iso"])
                except:
                    start_dt = None

            if not start_dt and args.get("when_text"):
                start_dt = parse_natural_time(args["when_text"])

            if not start_dt:
                return {"ok": False, "error": "Invalid time"}

            if start_dt.hour == 0 and start_dt.minute == 0:
                return {"ok": False, "error": "Invalid time"}

            if start_dt < datetime.now(start_dt.tzinfo):
                return {"ok": False, "error": "Time is in the past"}

            minutes = SERVICES[service]["minutes"]
            end_dt = start_dt + timedelta(minutes=minutes)

            if not is_free(start_dt, end_dt, barber):
                return {"ok": False, "error": "Slot not available"}

            result = create_booking(
                phone=phone,
                service_name=service,
                start_dt=start_dt,
                minutes=minutes,
                name=profile_name or "Customer",
                barber=barber,
            )

            return {"ok": True, "booking": result}

        # ---------------- LIST ----------------
        if tool_name == "list_customer_bookings":
            bookings = list_bookings(phone)
            return {"ok": True, "bookings": bookings[:1]}

        # ---------------- CANCEL ----------------
        if tool_name == "cancel_customer_booking":
            result = cancel_booking(args["event_id"])
            return {"ok": bool(result)}

        # ---------------- RESCHEDULE ----------------
        if tool_name == "reschedule_customer_booking":
            new_start = None

            if args.get("new_start_iso"):
                try:
                    new_start = datetime.fromisoformat(args["new_start_iso"])
                except:
                    new_start = None

            if not new_start and args.get("when_text"):
                new_start = parse_natural_time(args["when_text"])

            if not new_start:
                return {"ok": False, "error": "Invalid time"}

            result = reschedule_booking(args["event_id"], new_start)
            return {"ok": bool(result), "booking": result}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_receptionist_agent(user_message, phone, profile_name, session, business_name, timezone_name):
    msg = user_message.lower().strip()

    # ---------- CONFIRM MEMORY ----------
    if msg in ["yes", "yes please", "yeah", "ok", "book it"]:
        pending = session.get("pending_booking")
        if pending:
            result = _execute_tool(
                "book_appointment", pending, phone, profile_name, session
            )
            if result.get("ok"):
                link = result.get("booking", {}).get("link", "")
                return f"Nice one 👌 you're all booked in!\n\n📅 {link}" if link else "Nice one 👌 you're all booked in!"
            return "Sorry, that slot is gone 😅 want another?"

    instructions = f"""
You are a friendly WhatsApp receptionist for {business_name}.

- Be natural, short, human
- Use light emojis (👌 ✂️ 📅)
- Never be robotic

Rules:
- If user gives full booking info → book immediately
- If unclear → ask for missing detail
- Never guess time
- Never book invalid times
- Prefer latest booking when unclear
"""

    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=user_message,
        tools=_tool_defs(),
    )

    for _ in range(5):
        tool_calls = [x for x in response.output if x.type == "function_call"]

        if not tool_calls:
            return response.output_text.strip()

        outputs = []

        for call in tool_calls:
            args = _safe_json_loads(call.arguments)

            # STORE pending booking
            if call.name == "book_appointment":
                session["pending_booking"] = args

            result = _execute_tool(
                call.name, args, phone, profile_name, session
            )

            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result),
                }
            )

        response = client.responses.create(
            model=OPENAI_MODEL,
            previous_response_id=response.id,
            input=outputs,
        )

    return "Something went wrong — try again 👍"