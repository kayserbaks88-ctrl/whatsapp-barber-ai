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


# ---------------- TIME PARSER ----------------
def parse_time(text):
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )


# ---------------- TOOL DEFINITIONS ----------------
def tools():
    return [
        {
            "type": "function",
            "name": "book",
            "description": "Create booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "barber": {"type": "string"},
                    "when_text": {"type": "string"},
                },
                "required": ["service", "barber", "when_text"],
            },
        },
        {
            "type": "function",
            "name": "list",
            "description": "List bookings",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


# ---------------- EXECUTE TOOL ----------------
def run_tool(name, args, phone, profile_name):
    phone = phone.replace("whatsapp:", "").strip()

    if name == "book":
        service = args.get("service")
        barber = args.get("barber")
        when_text = args.get("when_text")

        if service not in SERVICES or barber not in BARBERS:
            return {"ok": False, "error": "Missing info"}

        start = parse_time(when_text)
        if not start:
            return {"ok": False, "error": "Invalid time"}

        if start.hour == 0:
            return {"ok": False, "error": "Invalid time"}

        if start < datetime.now(start.tzinfo):
            return {"ok": False, "error": "Past time"}

        minutes = SERVICES[service]["minutes"]
        end = start + timedelta(minutes=minutes)

        if not is_free(start, end, barber):
            return {"ok": False, "error": "Not free"}

        booking = create_booking(
            phone=phone,
            service_name=service,
            start_dt=start,
            minutes=minutes,
            name=profile_name or "Customer",
            barber=barber,
        )

        return {"ok": True, "booking": booking}

    if name == "list":
        data = list_bookings(phone)
        return {"ok": True, "bookings": data[:1]}

    return {"ok": False}


# ---------------- MAIN AGENT ----------------
def run_receptionist_agent(user_message, phone, profile_name, session, business_name, timezone_name):
    msg = user_message.lower().strip()

    # -------- YES CONFIRM --------
    if msg in ["yes", "yes please", "yeah", "ok"]:
        pending = session.get("pending")
        if pending:
            result = run_tool("book", pending, phone, profile_name)
            if result.get("ok"):
                link = result["booking"].get("link", "")
                return f"Nice one 👌 you're booked in!\n📅 {link}" if link else "Nice one 👌 booked!"
            return "That slot just went 😅 want another?"

    # -------- BUILD MEMORY --------
    messages = []

    for h in session.get("history", [])[-10:]:
        messages.append({
            "role": h["role"],
            "content": h["content"]
        })

    messages.append({
        "role": "user",
        "content": user_message
    })

    # -------- SYSTEM PROMPT --------
    instructions = f"""
You are a friendly WhatsApp receptionist for {business_name}.

STYLE:
- Natural, short, human
- Light emojis (👌 ✂️ 📅)

RULES:
- Remember previous messages
- Combine info across messages
- Do NOT ask twice

BOOKING:
- If service + barber + time → call tool immediately
- Otherwise ask only for missing part
"""

    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=messages,
        tools=tools(),
    )

    for _ in range(5):
        calls = [x for x in response.output if x.type == "function_call"]

        if not calls:
            return response.output_text.strip()

        outputs = []

        for c in calls:
            args = json.loads(c.arguments or "{}")

            if c.name == "book":
                session["pending"] = args

            result = run_tool(c.name, args, phone, profile_name)

            outputs.append({
                "type": "function_call_output",
                "call_id": c.call_id,
                "output": json.dumps(result),
            })

        response = client.responses.create(
            model=OPENAI_MODEL,
            previous_response_id=response.id,
            input=outputs,
        )

    return "Try again 👍"