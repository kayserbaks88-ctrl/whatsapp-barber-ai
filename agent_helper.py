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


def _is_confirm(text: str) -> bool:
    text = (text or "").strip().lower()
    return text in {"yes", "yes please", "yeah", "yep", "ok", "okay", "go ahead", "confirm", "book it"}


def _is_cancel_text(text: str) -> bool:
    text = (text or "").lower()
    return any(w in text for w in ["cancel", "delete booking"])


def _is_reschedule_text(text: str) -> bool:
    text = (text or "").lower()
    return any(w in text for w in ["reschedule", "move", "change time", "change it", "move it"])


def _parse_when(text: str):
    return dateparser.parse(
        text,
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )


def _format_booking(b: dict, i: int | None = None) -> str:
    start = datetime.fromisoformat(b["start"]).astimezone(TIMEZONE)
    end = datetime.fromisoformat(b["end"]).astimezone(TIMEZONE)
    label = f"{i}. " if i else ""
    barber = BARBERS.get(b.get("barber"), {}).get("name", b.get("barber", ""))
    service = SERVICES.get(b.get("service"), {}).get("label", b.get("service", "Booking"))
    return f"{label}{start.strftime('%A %d %b')} at {start.strftime('%-I:%M %p')} - {service} with {barber}"


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "show_services",
            "description": "Show available services",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "check_availability",
            "description": "Check if a barber is free",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string", "enum": list(BARBERS.keys())},
                    "service": {"type": "string", "enum": list(SERVICES.keys())},
                    "when": {"type": "string"},
                },
                "required": ["barber", "service", "when"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "book_appointment",
            "description": "Create a booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "barber": {"type": "string", "enum": list(BARBERS.keys())},
                    "service": {"type": "string", "enum": list(SERVICES.keys())},
                    "when": {"type": "string"},
                    "customer_name": {"type": "string"},
                },
                "required": ["barber", "service", "when"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_customer_bookings",
            "description": "List bookings",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "cancel_customer_booking",
            "description": "Cancel booking",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "selection": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
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
                    "selection": {"type": "string"},
                    "when": {"type": "string"},
                },
                "required": ["when"],
                "additionalProperties": False,
            },
        },
    ]


def _execute_tool(tool_name: str, args: dict, phone: str, profile_name: str | None, session: dict) -> dict:
    print("🔥 TOOL NAME CALLED:", tool_name)
    print("📦 ARGS:", args)

    customer = session.setdefault("customer", {})
    if profile_name:
        customer["name"] = profile_name

    customer_name = (args.get("customer_name") or customer.get("name") or profile_name or "Customer").strip()

    try:
        if tool_name == "show_services":
            return {"ok": True, "text": _friendly_services_text()}

        if tool_name == "check_availability":
            barber = args["barber"]
            service = args["service"]
            when_text = args["when"]

            start_dt = _parse_when(when_text)
            if not start_dt:
                return {"ok": False, "error": "invalid_time"}

            minutes = SERVICES[service]["minutes"]
            end_dt = start_dt + timedelta(minutes=minutes)
            free = is_free(start_dt, end_dt, barber)

            if free:
                session["pending_booking"] = {
                    "barber": barber,
                    "service": service,
                    "when": when_text,
                    "start_iso": start_dt.isoformat(),
                }

            return {
                "ok": True,
                "free": free,
                "barber": barber,
                "service": service,
                "start_iso": start_dt.isoformat(),
                "minutes": minutes,
            }

        if tool_name == "book_appointment":
            barber = args["barber"]
            service = args["service"]
            when_text = args["when"]

            start_dt = _parse_when(when_text)
            if not start_dt:
                return {"ok": False, "error": "invalid_time"}

            print("🕒 FINAL DATETIME:", start_dt)
            print("💈 BARBER:", barber)

            minutes = SERVICES[service]["minutes"]

            result = create_booking(
                phone=phone,
                service_name=service,
                start_dt=start_dt,
                minutes=minutes,
                name=customer_name,
                barber=barber,
            )

            if not result or not result.get("id"):
                return {"ok": False, "error": "booking_failed"}

            customer["last_booking"] = {"barber": barber, "service": service}
            session.pop("pending_booking", None)

            return {
                "ok": True,
                "booking": result,
                "link": result.get("link"),
                "customer_name": customer_name,
            }

        if tool_name == "list_customer_bookings":
            bookings = list_bookings(phone)
            return {"ok": True, "bookings": bookings}

        if tool_name == "cancel_customer_booking":
            bookings = list_bookings(phone)
            if not bookings:
                return {"ok": False, "error": "no_bookings"}

            selection = args.get("selection") or args.get("event_id")

            if len(bookings) > 1:
                if not selection or not str(selection).isdigit():
                    session["pending_cancel"] = {"bookings": bookings}
                    return {"ok": False, "error": "multiple_bookings", "bookings": bookings}

                index = int(selection) - 1
                if index < 0 or index >= len(bookings):
                    return {"ok": False, "error": "invalid_selection"}

                booking = bookings[index]
            else:
                booking = bookings[0]
            
            if result:
                session["last_booking"] = {
                    "id": booking["id"],
                    "barber": booking.get("barber"),
                    "service": booking.get("service"),
                }
            result = cancel_booking(booking["id"])
            session.pop("pending_cancel", None)

            return {
                "ok": bool(result),
                "cancelled": bool(result),
                "booking": booking,
            }

        if tool_name == "reschedule_customer_booking":
            bookings = list_bookings(phone)
            if not bookings:
                return {"ok": False, "error": "no_bookings"}

            when_text = args.get("when")
            selection = args.get("selection") or args.get("event_id")

            if len(bookings) > 1:
                if not selection or not str(selection).isdigit():
                    session["pending_reschedule"] = {
                        "when": when_text,
                        "bookings": bookings,
                    }
                    return {"ok": False, "error": "multiple_bookings", "bookings": bookings}

                index = int(selection) - 1
                if index < 0 or index >= len(bookings):
                    return {"ok": False, "error": "invalid_selection"}

                booking = bookings[index]
            else:
                booking = bookings[0]

            original_dt = datetime.fromisoformat(booking["start"]).astimezone(TIMEZONE)
            parsed = _parse_when(when_text)

            if not parsed:
                return {"ok": False, "error": "invalid_time"}

            new_start = original_dt.replace(
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            if result:
                session["last_booking"] = {
                    "id": booking["id"],
                    "barber": booking.get("barber"),
                    "service": booking.get("service"),
                }
            result = reschedule_booking(booking["id"], new_start)
            session.pop("pending_reschedule", None)

            return {
                "ok": bool(result),
                "rescheduled": bool(result),
                "booking": result,
            }

        return {"ok": False, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        print("❌ TOOL ERROR:", tool_name, e)
        return {"ok": False, "error": str(e), "tool_name": tool_name, "args": args}


def _book_pending(phone: str, profile_name: str | None, session: dict) -> str | None:
    pending = session.get("pending_booking")
    if not pending:
        return None

    customer = session.setdefault("customer", {})
    if profile_name:
        customer["name"] = profile_name

    name = customer.get("name") or profile_name or "Customer"
    barber = pending["barber"]
    service = pending["service"]
    start_dt = datetime.fromisoformat(pending["start_iso"])
    minutes = SERVICES[service]["minutes"]

    try:
        result = create_booking(
            phone=phone,
            service_name=service,
            start_dt=start_dt,
            minutes=minutes,
            name=name,
            barber=barber,
        )

        if not result or not result.get("id"):
            return "Sorry, I couldn’t complete that booking. Try another time?"

        session.pop("pending_booking", None)
        customer["last_booking"] = {"barber": barber, "service": service}

        service_label = SERVICES[service]["label"]
        barber_name = BARBERS[barber]["name"]
        nice_time = start_dt.astimezone(TIMEZONE).strftime("%A %d %b at %-I:%M %p")
        link = result.get("link")

        msg = f"Nice one {name} 👌 you’re booked in!\n\n{service_label} with {barber_name}\n{nice_time}"
        if link:
            msg += f"\n\nCalendar link: {link}"
        return msg

    except Exception as e:
        print("❌ PENDING BOOKING ERROR:", e)
        return f"Sorry {name}, I couldn’t book that slot. It may have just been taken."


def _handle_pending_selection(user_message: str, phone: str, profile_name: str | None, session: dict) -> str | None:
    text = (user_message or "").strip().lower()
    match = re.search(r"\b(\d+)\b", text)
    if not match:
        return None

    selection = match.group(1)

    if session.get("pending_reschedule"):
        pending = session["pending_reschedule"]
        result = _execute_tool(
            "reschedule_customer_booking",
            {"selection": selection, "when": pending["when"]},
            phone,
            profile_name,
            session,
        )

        if result.get("ok") and result.get("rescheduled"):
            new_start = result["booking"]["start"]
            dt = datetime.fromisoformat(new_start).astimezone(TIMEZONE)
            return f"Done 👍 I’ve moved that booking to {dt.strftime('%A %d %b at %-I:%M %p')}."

        return "Sorry, I couldn’t reschedule that one. The slot may already be taken."

    if session.get("pending_cancel"):
        result = _execute_tool(
            "cancel_customer_booking",
            {"selection": selection},
            phone,
            profile_name,
            session,
        )

        if result.get("ok") and result.get("cancelled"):
            return "Done 👍 I’ve cancelled that booking for you."

        return "Sorry, I couldn’t cancel that booking."

    return None


def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:
    session["last_user_message"] = user_message

    customer = session.setdefault("customer", {})
    if profile_name:
        customer["name"] = profile_name

    customer_name = customer.get("name") or (profile_name or "").strip()

    if _is_confirm(user_message):
        pending_reply = _book_pending(phone, profile_name, session)
        if pending_reply:
            return pending_reply

    selection_reply = _handle_pending_selection(user_message, phone, profile_name, session)
    if selection_reply:
        return selection_reply

    recent_history = session.get("history", [])[-12:]
    history_text = ""
    for item in recent_history:
        role = item.get("role", "user")
        content = item.get("content", "")
        history_text += f"{role.upper()}: {content}\n"

    current_time = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M")

    instructions = f"""
You are the WhatsApp receptionist for {business_name}.

Style:
- Sound like a friendly human receptionist.
- Use natural WhatsApp language.
- Use a few light emojis, not too many.
- Be warm, clear, and business-like.
- Never mention tools, JSON, schemas, function calls, or internal logic.

Business context:
- Current date/time: {current_time}
- Timezone: {timezone_name}
- Customer phone: {phone}
- Customer profile name: {customer_name or "unknown"}

Barbers:
{json.dumps(BARBERS, indent=2)}

Services:
{json.dumps(SERVICES, indent=2)}

STRICT TOOL RULES:
- If user provides barber, service, and time, you MUST call book_appointment.
- If user gives service/barber then later gives time, call check_availability first.
- If user confirms with yes/ok, do not ask for details again.
- Never confirm a booking unless a tool result says it succeeded.
- If user asks to cancel, call cancel_customer_booking.
- If user asks to move/change/reschedule, call reschedule_customer_booking.
- Never tell the customer to use Google Calendar manually.
- If multiple bookings are returned, ask which booking by number.
- If customer replies with a number, use that number as the selection.
- If rescheduling, never create a new booking.
- DO NOT confirm bookings unless the book_appointment tool has been called successfully.

-  If the user provides a time and confirms, you MUST call the booking tool.

-  Never say "you have an appointment" unless it is already stored in the system.
Rules:
- Prefer natural conversation over rigid menus.
- Only show services menu if asked or if user is too vague.
- If booking info is incomplete, ask only for the missing detail.
- For successful bookings, confirm barber, service, date, time, and include calendar link if present.
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

    for _ in range(6):
        tool_calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]

        if not tool_calls:
            text = (response.output_text or "").strip()
            if text:
                return text
            return "No worries 👍 I didn’t quite catch that. What would you like to do?"

        tool_outputs = []

        for call in tool_calls:
            args = _safe_json_loads(call.arguments)
            result = _execute_tool(
                call.name,
                args,
                phone=phone,
                profile_name=profile_name,
                session=session,
            )
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