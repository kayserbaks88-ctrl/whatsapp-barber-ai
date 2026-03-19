import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import is_free, create_booking

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "TrimTech AI")

# Simple in-memory session store
# Good enough for now while testing MVP on Render
SESSIONS: dict[str, dict] = {}

SERVICES = {
    "haircut": {"label": "Haircut", "duration": 30},
    "skin fade": {"label": "Skin Fade", "duration": 30},
    "beard trim": {"label": "Beard Trim", "duration": 20},
    "kids cut": {"label": "Kids Cut", "duration": 30},
}

SERVICE_ALIASES = {
    "haircut": "haircut",
    "trim": "haircut",
    "cut": "haircut",
    "skin fade": "skin fade",
    "fade": "skin fade",
    "beard": "beard trim",
    "beard trim": "beard trim",
    "kids": "kids cut",
    "kids cut": "kids cut",
}


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def normalize_service(text: str | None) -> str | None:
    if not text:
        return None
    low = text.strip().lower()
    for key, value in SERVICE_ALIASES.items():
        if key in low:
            return value
    return None


def format_dt(dt: datetime) -> str:
    return dt.astimezone(TIMEZONE).strftime("%A %H:%M")


def parse_time_text(text: str) -> datetime | None:
    text_low = text.strip().lower()

    if "tomorrow now" in text_low:
        return now_local() + timedelta(days=1)

    dt = dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    return dt


def detect_service_from_text(text: str) -> str | None:
    return normalize_service(text)


def menu_text() -> str:
    return (
        f"Hi 👋 Welcome to {BUSINESS_NAME} 💈\n\n"
        "I can help you with:\n"
        "• Haircut\n"
        "• Skin Fade\n"
        "• Beard Trim\n"
        "• Kids Cut\n\n"
        "You can say things like:\n"
        "• Book haircut tomorrow 3pm\n"
        "• Any slots after 2pm?\n"
        "• Menu\n"
        "• Cancel booking"
    )


def thank_you_text() -> str:
    return "You're welcome! 💈 See you soon 👊🏾"


def ask_name_text() -> str:
    return "Perfect 👌 what name should I book it under?"


def suggest_alt_text(service_key: str, requested: datetime) -> str:
    duration = SERVICES.get(service_key, {"duration": 30})["duration"]
    options = []

    for mins in (30, 60, 90):
        candidate = requested + timedelta(minutes=mins)
        candidate_end = candidate + timedelta(minutes=duration)
        try:
            if is_free(candidate, candidate_end):
                options.append(candidate.strftime("%H:%M"))
        except TypeError:
            # In case your calendar_helper currently accepts only one argument
            if is_free(candidate):
                options.append(candidate.strftime("%H:%M"))

    if options:
        return (
            f"That slot is taken 😬\n"
            f"Next available options: {', '.join(options)}\n"
            "Which one would you like?"
        )

    return "That slot is taken 😬 Got another time you'd like?"


def create_booking_safe(name: str, service_key: str, start_dt: datetime, phone: str) -> None:
    duration = SERVICES.get(service_key, {"duration": 30})["duration"]
    end_dt = start_dt + timedelta(minutes=duration)

    # Try richer signature first, then fall back to your current helper
    try:
        create_booking(
            name=name,
            service=SERVICES.get(service_key, {"label": service_key.title()})["label"],
            start_time=start_dt,
            end_time=end_dt,
            phone=phone,
        )
        return
    except TypeError:
        pass

    try:
        create_booking(phone, service_key, start_dt)
        return
    except TypeError:
        pass

    create_booking(start_dt)


def check_free_safe(service_key: str, start_dt: datetime) -> bool:
    duration = SERVICES.get(service_key, {"duration": 30})["duration"]
    end_dt = start_dt + timedelta(minutes=duration)

    try:
        return is_free(start_dt, end_dt)
    except TypeError:
        return is_free(start_dt)


def reset_session(number: str) -> None:
    SESSIONS.pop(number, None)


def get_session(number: str) -> dict:
    return SESSIONS.get(number, {})


def save_session(number: str, session: dict) -> None:
    SESSIONS[number] = session


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming = request.values.get("Body", "").strip()
    number = request.values.get("From", "").strip()

    resp = MessagingResponse()
    reply = resp.message()

    if not incoming:
        reply.body(menu_text())
        return str(resp)

    text_low = incoming.lower()
    session = get_session(number)

    # Simple conversational commands
    if text_low in {"hi", "hello", "hey", "yo"}:
        reply.body(menu_text())
        return str(resp)

    if "menu" == text_low or text_low == "services":
        reply.body(menu_text())
        return str(resp)

    if "thank" in text_low:
        reply.body(thank_you_text())
        return str(resp)

    # Awaiting name stage
    if session.get("stage") == "awaiting_name":
        customer_name = incoming.strip()
        service_key = session.get("service", "haircut")
        booking_time = session.get("time")

        if not booking_time:
            session["stage"] = "awaiting_time"
            save_session(number, session)
            reply.body("What time would you like? ⏰")
            return str(resp)

        if not check_free_safe(service_key, booking_time):
            reply.body(suggest_alt_text(service_key, booking_time))
            return str(resp)

        create_booking_safe(customer_name, service_key, booking_time, number)

        reply.body(
            f"✅ All set {customer_name} 👌\n"
            f"{SERVICES.get(service_key, {'label': service_key.title()})['label']} booked for {format_dt(booking_time)} 💈"
        )
        reset_session(number)
        return str(resp)

    # Awaiting time stage
    if session.get("stage") == "awaiting_time":
        parsed_time = parse_time_text(incoming)
        if parsed_time:
            session["time"] = parsed_time
            session["stage"] = "awaiting_name"
            save_session(number, session)
            reply.body(ask_name_text())
            return str(resp)

        reply.body("I didn’t catch the time properly 😅 What time would you like?")
        return str(resp)

    # Manual special handling
    if "tomorrow now" in text_low:
        service_key = session.get("service", "haircut")
        session["time"] = now_local() + timedelta(days=1)
        session["service"] = service_key
        session["stage"] = "awaiting_name"
        save_session(number, session)
        reply.body(ask_name_text())
        return str(resp)

    # LLM parse
    data = {}
    try:
        data = llm_extract(incoming) or {}
    except Exception:
        data = {}

    intent = (data.get("intent") or "").lower().strip()
    service_key = normalize_service(data.get("service")) or detect_service_from_text(incoming)
    time_text = data.get("time") or incoming
    parsed_time = None

    if data.get("time"):
        parsed_time = parse_time_text(str(data["time"]))
    else:
        parsed_time = parse_time_text(incoming)

    # Booking intent
    if intent == "book" or service_key:
        service_key = service_key or session.get("service") or "haircut"

        if not parsed_time:
            session["service"] = service_key
            session["stage"] = "awaiting_time"
            save_session(number, session)
            reply.body(
                f"What time would you like your {SERVICES.get(service_key, {'label': service_key.title()})['label'].lower()}? ⏰"
            )
            return str(resp)

        if not check_free_safe(service_key, parsed_time):
            reply.body(suggest_alt_text(service_key, parsed_time))
            return str(resp)

        session["service"] = service_key
        session["time"] = parsed_time
        session["stage"] = "awaiting_name"
        save_session(number, session)
        reply.body(ask_name_text())
        return str(resp)

    # Availability intent
    if intent in {"availability", "check"} and parsed_time:
        service_key = service_key or "haircut"
        if check_free_safe(service_key, parsed_time):
            reply.body(
                f"Yes 👌 {SERVICES.get(service_key, {'label': service_key.title()})['label']} is free for {format_dt(parsed_time)}.\n"
                "What name should I book it under?"
            )
            session["service"] = service_key
            session["time"] = parsed_time
            session["stage"] = "awaiting_name"
            save_session(number, session)
            return str(resp)

        reply.body(suggest_alt_text(service_key, parsed_time))
        return str(resp)

    # Fallback response
    reply.body(
        "I can help you book a haircut, check availability, or show the menu 💈\n\n"
        "Try:\n"
        "• Book haircut tomorrow 3pm\n"
        "• Menu"
    )
    return str(resp)


@app.route("/", methods=["GET"])
def home():
    return "TrimTech AI is live", 200


if __name__ == "__main__":
    app.run(debug=True)
