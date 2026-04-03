import json
import os
from datetime import datetime, timedelta
from typing import Any

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


# -------------------------------
# SAFE JSON
# -------------------------------
def _safe_json_loads(value: str) -> dict:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


# -------------------------------
# MEMORY EXTRACTION (🔥 KEY)
# -------------------------------
def _update_memory(session: dict, user_message: str):
    msg = (user_message or "").lower()
    data = session.setdefault("data", {})

    # service
    if "haircut" in msg:
        data["service"] = "haircut"
    elif "beard" in msg:
        data["service"] = "beard trim"
    elif "fade" in msg:
        data["service"] = "skin fade"
    elif "kid" in msg:
        data["service"] = "kids cut"

    # barber
    if "jay" in msg:
        data["barber"] = "jay"
    elif "mike" in msg:
        data["barber"] = "mike"


# -------------------------------
# TOOL DEFINITIONS
# -------------------------------
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
                    "customer_name": {"type": "string"},
                },
                "required": ["barber", "service", "start_iso"],
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


# -------------------------------
# TOOL EXECUTION
# -------------------------------
def _execute_tool(tool_name: str, args: dict, phone: str, profile_name: str | None):
    try:
        if tool_name == "book_appointment":
            barber = args["barber"]
            service = args["service"]
            start_dt = datetime.fromisoformat(args["start_iso"])
            minutes = SERVICES[service]["minutes"]

            result = create_booking(
                phone=phone,
                service_name=service,
                start_dt=start_dt,
                minutes=minutes,
                name=profile_name or "Customer",
                barber=barber,
            )

            return {"ok": True, "booking": result}

        if tool_name == "list_customer_bookings":
            return {"ok": True, "bookings": list_bookings(phone)}

        if tool_name == "cancel_customer_booking":
            ok = cancel_booking(args["event_id"])
            return {"ok": ok}

        if tool_name == "reschedule_customer_booking":
            ok = reschedule_booking(
                args["event_id"],
                datetime.fromisoformat(args["new_start_iso"]),
            )
            return {"ok": ok}

        return {"ok": False, "error": "unknown tool"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# -------------------------------
# MAIN AGENT
# -------------------------------
def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:

    # 🔥 UPDATE MEMORY FIRST
    _update_memory(session, user_message)
    data = session.get("data", {})

    # -------------------------------
    # TRY DIRECT BOOKING (🔥 MAGIC)
    # -------------------------------
    if all(k in data for k in ["service", "barber", "when"]):
        try:
            start_dt = datetime.fromisoformat(data["when"])
            minutes = SERVICES[data["service"]]["minutes"]

            if is_free(start_dt, start_dt + timedelta(minutes=minutes), data["barber"]):
                result = create_booking(
                    phone,
                    data["service"],
                    start_dt,
                    minutes,
                    profile_name or "Customer",
                    data["barber"],
                )

                session["data"] = {}  # clear after booking

                link = result.get("link", "")
                return f"Nice one 👌 you're booked in!\n{link}"

        except:
            pass

    # -------------------------------
    # BUILD HISTORY
    # -------------------------------
    history = session.setdefault("history", [])
    history.append({"role": "user", "content": user_message})
    history = history[-10:]

    history_text = "\n".join(
        f"{h['role']}: {h['content']}" for h in history
    )

    # -------------------------------
    # AI INSTRUCTIONS (🔥 FIXED)
    # -------------------------------
    instructions = f"""
You are a friendly WhatsApp barber receptionist for {business_name}.

STYLE:
- Natural, human
- Short replies
- Light emojis

CRITICAL RULES:
- NEVER ask for info already given
- ALWAYS use conversation memory
- Combine messages

BOOKING LOGIC:
- Required: service, barber, time
- If all present → proceed to booking
- Do NOT repeat questions

Conversation:
{history_text}
"""

    # -------------------------------
    # CALL AI
    # -------------------------------
    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=user_message,
        tools=_tool_defs(),
    )

    text = (response.output_text or "").strip()

    if not text:
        return "No worries 👍 tell me what you'd like to book."

    history.append({"role": "assistant", "content": text})

    return text