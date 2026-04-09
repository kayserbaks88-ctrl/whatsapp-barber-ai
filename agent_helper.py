import json
import os
from datetime import datetime
from typing import Any
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import dateparser

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))


def parse_when_text(text: str):
    if not text:
        return None

    now = datetime.now(TIMEZONE)
    text_lower = text.lower()

    # Step 1: parse ONLY time
    parsed_time = dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
        },
    )

    if not parsed_time:
        return None

    hour = parsed_time.hour
    minute = parsed_time.minute

    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    # 🧠 TODAY
    if "today" in text_lower:
        target_date = now

    # 🧠 TOMORROW
    elif "tomorrow" in text_lower:
        target_date = now + timedelta(days=1)

    # 🧠 WEEKDAYS
    else:
        target_date = now

        for day_name, day_num in weekdays.items():
            if day_name in text_lower:
                current_day = now.weekday()
                days_ahead = day_num - current_day

                if days_ahead <= 0:
                    days_ahead += 7

                if "next" in text_lower:
                    days_ahead += 7

                target_date = now + timedelta(days=days_ahead)
                break

    # 🔥 BUILD FINAL DATE CLEANLY
    final_dt = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        tzinfo=TIMEZONE,
    )

    return final_dt
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
    for key, svc in SERVICES.items():
        lines.append(f"- {svc['label']} ({svc['minutes']} mins)")
    return "\n".join(lines)


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "show_services",
            "description": "Show the services menu when the user asks what services are available, prices/durations, or seems unsure what to book.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "check_availability",
            "description": "Check if a barber is free at a specific start time for a service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {
                        "type": "string",
                        "enum": list(BARBERS.keys()),
                    },
                    "service": {
                        "type": "string",
                        "enum": list(SERVICES.keys()),
                    },
                    "start_iso": {
                        "type": "string",
                        "description": "Booking start datetime in ISO 8601 format with timezone offset.",
                    },
                },
                "required": ["barber", "service", "start_iso"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "book_appointment",
            "description": "Create a booking when the user has given enough details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {
                        "type": "string",
                        "enum": list(BARBERS.keys()),
                    },
                    "service": {
                        "type": "string",
                        "enum": list(SERVICES.keys()),
                    },
                    "start_iso": {
                        "type": "string",
                        "description": "Booking start datetime in ISO 8601 format with timezone offset.",
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Customer name if known.",
                    },
                },
                "required": ["barber", "service", "start_iso"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_customer_bookings",
            "description": "List the user's upcoming bookings by phone number.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "cancel_customer_booking",
            "description": "Cancel one of the user's bookings by event id. Use after first listing bookings if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event id of the booking to cancel.",
                    }
                },
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "reschedule_customer_booking",
            "description": "Move an existing booking to a new date/time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event id of the booking to move.",
                    },
                    "new_start_iso": {
                        "type": "string",
                        "description": "New appointment start datetime in ISO 8601 format with timezone offset.",
                    },
                },
                "required": ["event_id", "new_start_iso"],
                "additionalProperties": False,
            },
        },
    ]


def _execute_tool(tool_name: str, args: dict, phone: str, profile_name: str | None) -> dict:
    try:
        if tool_name == "show_services":
            return {
                "ok": True,
                "services": SERVICES,
                "text": _friendly_services_text(),
            }

        if tool_name == "check_availability":
            barber = args["barber"]
            service = args["service"]
            start_dt = datetime.fromisoformat(args["start_iso"])
            minutes = SERVICES[service]["minutes"]
            end_dt = start_dt.replace() + __import__("datetime").timedelta(minutes=minutes)
            free = is_free(start_dt, end_dt, barber)
            return {
                "ok": True,
                "free": free,
                "barber": barber,
                "service": service,
                "start_iso": start_dt.isoformat(),
                "minutes": minutes,
            }

        if tool_name == "book_appointment":
            try:
                barber = args["barber"]
                service = args["service"]
                start_iso = args.get("start_iso")

                if not start_iso:
                    return {"ok": False, "error": "Missing start time"}

                start_dt = datetime.fromisoformat(start_iso)
                minutes = SERVICES[service]["minutes"]

                customer_name = args.get("customer_name") or profile_name or "Customer"

                print("BOOKING:", barber, service, start_dt)

                result = create_booking(
                    phone=phone,
                    service_name=service,
                    start_dt=start_dt,
                    minutes=minutes,
                    name=customer_name,
                    barber=barber,
                )

                print("BOOKED:", result)

                return {
                    "ok": True,
                    **result
                }

            except Exception as e:
                print("BOOKING ERROR:", str(e))
                return {
                    "ok": False,
                    "error": str(e)
                }

        

        

        customer_name = args.get("customer_name") or "Customer"

        when_text = args.get("when") or args.get("start_dt")
        print("RAW INPUT:", when_text)

        start_dt = parse_when_text(when_text)
        print("PARSED DATE:", start_dt)

        result = create_booking(
            phone=phone,
            service_name=service,
            start_dt=start_dt,
            minutes=minutes,
            name=customer_name,
            barber=barber,
        )
        return {
                "ok": True,
                "booking": result,
                "barber": barber,
                "service": service,
                "start_iso": start_dt.isoformat(),
                "minutes": minutes,
                "customer_name": customer_name,
            }

        if tool_name == "list_customer_bookings":
            bookings = list_bookings(phone)
            return {
                "ok": True,
                "bookings": bookings,
            }

        if tool_name == "cancel_customer_booking":
            event_id = args["event_id"]
            result = cancel_booking(event_id)
            return {
                "ok": bool(result),
                "cancelled": bool(result),
                "event_id": event_id,
            }

        if tool_name == "reschedule_customer_booking":
            event_id = args["event_id"]
            new_start = datetime.fromisoformat(args["new_start_iso"])
            result = reschedule_booking(event_id, new_start)
            return {
                "ok": bool(result),
                "rescheduled": bool(result),
                "event_id": event_id,
                "new_start_iso": new_start.isoformat(),
                "result": result,
            }

        return {"ok": False, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "tool_name": tool_name,
            "args": args,
        }


def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:
    customer_name = (profile_name or "").strip()

    recent_history = session.get("history", [])[-12:]
    history_text = ""
    for item in recent_history:
        role = item.get("role", "user")
        content = item.get("content", "")
        history_text += f"{role.upper()}: {content}\n"

    instructions = f"""
You are the WhatsApp receptionist for {business_name}.

Style:
- Sound like a friendly human receptionist.
- Use natural WhatsApp language.
- Use a few light emojis, not too many.
- Be warm, clear, and business-like.
- Never mention tools, JSON, schemas, function calls, or internal logic.

Business context:
- Timezone: {timezone_name}
- Customer phone: {phone}
- Customer profile name: {customer_name or "unknown"}

Barbers:
{json.dumps(BARBERS, indent=2)}

Services:
{json.dumps(SERVICES, indent=2)}

Rules:
- Prefer natural conversation over rigid menus.
- Only show the services menu if the user asks what is available, pricing/duration, or they are too vague.
- If booking info is incomplete, ask only for the missing detail.
- If the user wants to cancel or reschedule, first identify the booking clearly.
- If there is exactly one upcoming booking and the user says "cancel it" or "move it", you may use that booking.
- Always use tools for booking, listing, cancelling, rescheduling, or availability checks.
- Do not pretend a booking/cancel/reschedule succeeded unless the tool result says it succeeded.
- For successful bookings, confirm barber, service, date, time, and include the calendar link if present.
- For list_bookings results, summarise them neatly.
- Keep replies short and natural.

Recent conversation:
{history_text}
""".strip()

    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=user_message,
        tools=_tool_defs(),
    )

    # Tool loop
    for _ in range(6):
        tool_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]

        if not tool_calls:
            text = (response.output_text or "").strip()
            if text:
                return text
            return "No worries 👍 I didn’t quite catch that. Tell me what you’d like to do with your booking."

        tool_outputs = []

        for call in tool_calls:
            args = _safe_json_loads(call.arguments)
            result = _execute_tool(call.name, args, phone=phone, profile_name=profile_name)
            tool_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result),
                }
            )

        response = client.responses.create(
            model=OPENAI_MODEL,
            previous_response_id=response.id,
            input=tool_outputs,
        )

    return "Sorry — something got stuck on my side. Send that again and I’ll sort it 👍"