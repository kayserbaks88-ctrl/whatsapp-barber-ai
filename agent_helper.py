import json
import os
import re
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

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))


# ======================
# ✅ CLEAN TIME PARSER (NO BUGS)
# ======================
def _parse_when(text: str):
    return dateparser.parse(
        text,
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )


# ======================
# HELPERS
# ======================
def _safe_json_loads(value: str) -> dict:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _is_confirm(text: str) -> bool:
    return text.lower() in ["yes", "yes please", "yeah", "yep", "ok", "book it"]


def _is_cancel(text: str) -> bool:
    return "cancel" in text.lower()


def _is_reschedule(text: str) -> bool:
    return any(x in text.lower() for x in ["move", "reschedule", "change"])


# ======================
# TOOLS
# ======================
def _tool_defs():
    return [
        {
            "type": "function",
            "name": "book_appointment",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string"},
                    "service": {"type": "string"},
                    "when": {"type": "string"},
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
                "properties": {
                    "selection": {"type": "string"},
                },
            },
        },
        {
            "type": "function",
            "name": "reschedule_customer_booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "selection": {"type": "string"},
                    "when": {"type": "string"},
                },
                "required": ["when"],
            },
        },
    ]


# ======================
# TOOL EXECUTION
# ======================
def _execute_tool(tool_name, args, phone, profile_name, session):
    customer = session.setdefault("customer", {})

    try:
        # ======================
        # BOOK
        # ======================
        if tool_name == "book_appointment":
            start_dt = _parse_when(args["when"])
            minutes = SERVICES[args["service"]]["minutes"]

            result = create_booking(
                phone,
                args["service"],
                start_dt,
                minutes,
                profile_name or "Customer",
                args["barber"],
            )

            # ✅ MEMORY
            session["last_booking"] = result

            return {"ok": True, "booking": result}

        # ======================
        # LIST
        # ======================
        if tool_name == "list_customer_bookings":
            return {"ok": True, "bookings": list_bookings(phone)}

        # ======================
        # CANCEL
        # ======================
        if tool_name == "cancel_customer_booking":
            bookings = list_bookings(phone)
            if not bookings:
                return {"ok": False}

            booking = bookings[0]  # simple version
            result = cancel_booking(booking["id"])

            return {"ok": bool(result)}

        # ======================
        # RESCHEDULE (FIXED)
        # ======================
        if tool_name == "reschedule_customer_booking":
            bookings = list_bookings(phone)
            if not bookings:
                return {"ok": False}

            booking = bookings[0]

            new_start = _parse_when(args["when"])

            result = reschedule_booking(booking["id"], new_start)

            if result:
                session["last_booking"] = result

            return {"ok": bool(result), "booking": result}

    except Exception as e:
        print("ERROR:", e)
        return {"ok": False}


# ======================
# MAIN AGENT
# ======================
def run_receptionist_agent(user_message, phone, profile_name, session, *_):

    # ✅ MEMORY SHORTCUTS
    if _is_cancel(user_message) and session.get("last_booking"):
        _execute_tool(
            "cancel_customer_booking",
            {},
            phone,
            profile_name,
            session,
        )
        return "Done 👍 I’ve cancelled your booking."

    if _is_reschedule(user_message) and session.get("last_booking"):
        session["pending_reschedule"] = True
        return "What time would you like to move it to?"

    if session.get("pending_reschedule"):
        session.pop("pending_reschedule")
        result = _execute_tool(
            "reschedule_customer_booking",
            {"when": user_message},
            phone,
            profile_name,
            session,
        )

        if result.get("ok"):
            dt = datetime.fromisoformat(result["booking"]["start"]).astimezone(TIMEZONE)
            return f"Done 👍 moved to {dt.strftime('%A %d %b at %-I:%M %p')}."

        return "Sorry, that slot isn’t available."

    # ======================
    # NORMAL AI FLOW
    # ======================
    response = client.responses.create(
        model=OPENAI_MODEL,
        input=user_message,
        tools=_tool_defs(),
    )

    for _ in range(5):
        calls = [x for x in response.output if x.type == "function_call"]

        if not calls:
            return response.output_text or "👍"

        outputs = []
        for call in calls:
            result = _execute_tool(
                call.name,
                _safe_json_loads(call.arguments),
                phone,
                profile_name,
                session,
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

    return "Something went wrong 👍"