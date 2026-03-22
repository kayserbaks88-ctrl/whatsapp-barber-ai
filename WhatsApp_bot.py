import os
import re
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


def now_local():
    return datetime.now(TIMEZONE)


def normalize_service(text):
    if not text:
        return None
    low = text.lower()
    for key, value in SERVICE_ALIASES.items():
        if key in low:
            return value
    return None


def format_dt(dt):
    return dt.astimezone(TIMEZONE).strftime("%A %H:%M")


def parse_time_text(text):
    if not text:
        return None

    if "tomorrow now" in text.lower():
        return now_local() + timedelta(days=1)

    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )


def parse_time_from_selection(text, base_date):
    if not base_date:
        return None

    match = re.search(r"\b(\d{1,2}):?(\d{2})?\b", text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)

    return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


def menu_text():
    return (
        f"Hi 👋 Welcome to {BUSINESS_NAME} 💈\n\n"
        "• Haircut\n"
        "• Skin Fade\n"
        "• Beard Trim\n"
        "• Kids Cut\n\n"
        "Try:\n"
        "• Book haircut tomorrow 3pm\n"
        "• Any slots after 2pm?\n"
        "• Menu\n"
        "• Cancel booking"
    )


def ask_name_text():
    return "Perfect 👌 what name should I book it under?"


def suggest_alt_text(service_key, requested, session, number):
    duration = SERVICES.get(service_key, {"duration": 30})["duration"]
    options = []

    for mins in (30, 60, 90):
        candidate = requested + timedelta(minutes=mins)
        end = candidate + timedelta(minutes=duration)

        try:
            free = is_free(candidate, end)
        except TypeError:
            free = is_free(candidate)

        if free:
            options.append(candidate.strftime("%H:%M"))

    if options:
        session["stage"] = "choosing_slot"
        session["requested_time"] = requested
        SESSIONS[number] = session

        return (
            f"That slot is taken 😬\n"
            f"Next available options: {', '.join(options)}\n"
            "Which one would you like?"
        )

    return "That slot is taken 😬 Try another time?"


def create_booking_safe(name, service_key, start_dt, phone):
    duration = SERVICES.get(service_key, {"duration": 30})["duration"]
    end_dt = start_dt + timedelta(minutes=duration)

    try:
        create_booking(
            name=name,
            service=service_key,
            start_time=start_dt,
            end_time=end_dt,
            phone=phone,
        )
    except TypeError:
        create_booking(phone, service_key, start_dt)


def check_free_safe(service_key, start_dt):
    duration = SERVICES.get(service_key, {"duration": 30})["duration"]
    end_dt = start_dt + timedelta(minutes=duration)

    try:
        return is_free(start_dt, end_dt)
    except TypeError:
        return is_free(start_dt)


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming = request.values.get("Body", "").strip()
    number = request.values.get("From", "").strip()

    resp = MessagingResponse()
    reply = resp.message()

    session = SESSIONS.get(number, {})

    if not incoming:
        reply.body(menu_text())
        return str(resp)

    text_low = incoming.lower()

    # --- SLOT SELECTION ---
    if session.get("stage") == "choosing_slot":
        base = session.get("requested_time")
        chosen = parse_time_from_selection(incoming, base)

        if chosen:
            service = session.get("service", "haircut")

            if check_free_safe(service, chosen):
                session["time"] = chosen
                session["stage"] = "awaiting_name"
                SESSIONS[number] = session
                reply.body(ask_name_text())
                return str(resp)

            reply.body("That slot just got taken 😅 try another one?")
            return str(resp)

    # --- NAME STAGE ---
    if session.get("stage") == "awaiting_name":
        name = incoming
        service = session.get("service", "haircut")
        time = session.get("time")

        if not time:
            session["stage"] = "awaiting_time"
            SESSIONS[number] = session
            reply.body(f"What time would you like your {service}? ⏰")
            return str(resp)

        if not check_free_safe(service, time):
            reply.body(suggest_alt_text(service, time, session, number))
            return str(resp)

        create_booking_safe(name, service, time, number)

        reply.body(
            f"✅ All set {name} 👌\n"
            f"{service.title()} booked for {format_dt(time)} 💈\n\n"
            "You can also:\n"
            "• Cancel booking\n"
            "• Reschedule\n"
            "• Book another service"
        )
        SESSIONS.pop(number, None)
        return str(resp)

    # --- GREETING / MENU / THANKS ---
    if text_low in {"hi", "hello", "hey"}:
        reply.body(menu_text())
        return str(resp)

    if text_low in {"menu", "services"}:
        reply.body(menu_text())
        return str(resp)

    if "thank" in text_low:
        reply.body("You're welcome! 💈 See you soon 👊🏾")
        return str(resp)

    # --- LLM ---
    try:
        data = llm_extract(incoming) or {}
    except Exception:
        data = {}

    intent = (data.get("intent") or "").lower().strip()
    if not service:
        service = session.get("service") or "haircut"
    parsed_time = parse_time_text(data.get("time") or incoming)
    
    # --- BOOKING FLOW ---
    if intent == "book" or service:
        if not parsed_time:
            session["service"] = service
            session["stage"] = "awaiting_time"
            SESSIONS[number] = session
            reply.body(f"What time would you like your {service}? ⏰")
            return str(resp)

        if not check_free_safe(service, parsed_time):
            reply.body(suggest_alt_text(service, parsed_time, session, number))
            return str(resp)

        session["service"] = service
        session["time"] = parsed_time
        session["stage"] = "awaiting_name"
        SESSIONS[number] = session
        reply.body(ask_name_text())
        return str(resp)

    # --- FALLBACK ---
    reply.body(menu_text())
    return str(resp)


@app.route("/", methods=["GET"])
def home():
    return "TrimTech AI is live", 200

if __name__ == "__main__":
    port = int(os.evniron.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)