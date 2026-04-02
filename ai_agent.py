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


def _safe_json_loads(value: str) -> dict:
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _friendly_services_text() -> str:
    lines = []
    for svc in SERVICES.values():
        lines.append(f"- {svc['label']} ({svc['minutes']} mins)")
    return "\n".join(lines)


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "show_services",
            "description": "Show services menu",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "check_availability",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string", "enum": list(BARBERS.keys())},
                    "service": {"type": "string", "enum": list(SERVICES.keys())},
                    "start_iso": {"type": "string"},
                },
                "required": ["barber", "service", "start_iso"],
            },
        },
        {
            "type": "function",
            "name": "book_appointment",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string"},
                    "service": {"type": "string"},
                    "start_iso": {"type": "string"},
                    "customer_name": {"type": "string"},
                },
                "required": ["barber", "service", "start_iso"],
            },
        },
    ]


def _execute_tool(tool_name: str, args: dict, phone: str, profile_name: str | None) -> dict:
    try:
        if tool_name == "show_services":
            return {"ok": True, "text": _friendly_services_text()}

        if tool_name == "check_availability":
            start_dt = datetime.fromisoformat(args["start_iso"])
            minutes = SERVICES[args["service"]]["minutes"]
            end_dt = start_dt + timedelta(minutes=minutes)
            free = is_free(start_dt, end_dt, args["barber"])
            return {"ok": True, "free": free}

        if tool_name == "book_appointment":
            start_dt = datetime.fromisoformat(args["start_iso"])
            minutes = SERVICES[args["service"]]["minutes"]

            result = create_booking(
                phone=phone,
                service_name=args["service"],
                start_dt=start_dt,
                minutes=minutes,
                name=profile_name or "Customer",
                barber=args["barber"],
            )

            return {"ok": True, "booking": result}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:

    msg = user_message.lower().strip()

    # ✅ HANDLE YES CONFIRM
    if msg in ["yes", "yes please", "ok", "confirm"]:
        pending = session.get("pending_booking")
        if pending:
            result = _execute_tool("book_appointment", pending, phone, profile_name)
            if result.get("ok"):
                link = result.get("booking", {}).get("link", "")
                return f"Nice one 👌 you're booked!\n📅 {link}"
            return "That slot just went 😅 want another?"
    
    history_text = "\n".join(
        [f"{h['role']}: {h['content']}" for h in session.get("history", [])[-10:]]
    )
    # ✅ AI SYSTEM PROMPT
    instructions = f"""
    You are a friendly WhatsApp barber receptionist for {business_name}.

    Rules:
    - Be natural and human
    - Use light emojis
    - NEVER ask for info already given
    - If user already mentioned service, barber, or time → DO NOT ask again
    - Combine messages (e.g. "haircut with Mike" + "6pm tomorrow")

    Extract and remember from conversation:
    - service
    - barber
    - date/time

    If all details are present → proceed to booking

    Conversation:
    {history_text}
    """

    # ✅ FIRST CALL
    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=user_message,
        tools=_tool_defs(),
    )

    # ✅ TOOL LOOP
    for _ in range(5):
        tool_calls = [
            item for item in response.output
            if getattr(item, "type", None) == "function_call"
        ]

        if not tool_calls:
            text = (response.output_text or "").strip()
            return text or "Tell me what you'd like 👍"

        tool_outputs = []

        for call in tool_calls:
            args = _safe_json_loads(call.arguments)

            # 🔥 store booking for confirmation
            if call.name == "book_appointment":
                session["pending_booking"] = args

            result = _execute_tool(call.name, args, phone, profile_name)

            tool_outputs.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result),
            })

        response = client.responses.create(
            model=OPENAI_MODEL,
            previous_response_id=response.id,
            input=tool_outputs,
        )

    return "Something went wrong — try again 👍"