import os
import dateparser
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import create_booking, is_free

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

SESSIONS = {}

BARBERS = ["mike", "jay"]

SERVICES = {
    "haircut": 30,
    "skin fade": 45,
    "beard trim": 20,
    "shape up": 20,
    "kids cut": 30
}


def get_session(phone):
    if phone not in SESSIONS:
        SESSIONS[phone] = {}
    return SESSIONS[phone]


def reset_session(phone):
    SESSIONS[phone] = {}


def parse_time(text):
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": "Europe/London",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future"
        }
    )


def service_menu():
    return (
        "💈 Welcome to TrimTech AI\n\n"
        "Choose a service:\n"
        "1. Haircut - £18\n"
        "2. Skin Fade - £22\n"
        "3. Beard Trim - £12\n"
        "4. Shape Up - £10\n"
        "5. Kids Cut - £15\n\n"
        "Reply with the number or name."
    )


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "")
    profile_name = request.values.get("ProfileName", "")

    resp = MessagingResponse()
    session = get_session(from_number)

    data = llm_extract(incoming_msg)
    intent = data.get("intent")

    # ===== SMALL TALK =====
    if intent == "smalltalk":
        resp.message("😊 You're welcome! Just type MENU if you need anything.")
        return str(resp)

    # ===== MENU =====
    if intent == "menu" or not incoming_msg:
        reset_session(from_number)
        resp.message(service_menu())
        session["state"] = "awaiting_service"
        return str(resp)

    # ===== SERVICE =====
    if intent in ["book", "choose_service"]:
        service = data.get("service")

        if not service or service.lower() not in SERVICES:
            resp.message(service_menu())
            return str(resp)

        session["service"] = service.lower()

        resp.message(
            f"✅ Service selected: {service.title()}\n\n"
            "✂️ Choose your barber:\n"
            "1. Mike\n2. Jay\n3. First available"
        )

        session["state"] = "awaiting_barber"
        return str(resp)

    # ===== BARBER =====
    if intent in ["choose_barber", "change_barber"]:
        barber = data.get("barber")

        if not barber:
            resp.message("❌ Please choose Mike, Jay or First available")
            return str(resp)

        session["barber"] = barber.lower()
        session["state"] = "awaiting_time"

        resp.message("📅 What time would you like?")
        return str(resp)

    # ===== TIME =====
    if session.get("state") == "awaiting_time":
        dt = parse_time(incoming_msg)

        if not dt:
            resp.message("❌ Couldn't understand time. Try e.g. tomorrow 2pm")
            return str(resp)

        session["time"] = dt
        session["state"] = "awaiting_name"

        resp.message("Please reply with your name.")
        return str(resp)

    # ===== NAME + BOOK =====
    if session.get("state") == "awaiting_name":
        name = incoming_msg

        result = create_booking(
            phone=from_number,
            service_name=session["service"],
            start_dt=session["time"],
            name=name
        )

        session["last_booking"] = result
        session["name"] = name

        resp.message(
            f"✅ Booking confirmed!\n\n"
            f"Name: {name}\n"
            f"Service: {session['service'].title()}\n"
            f"Barber: {session['barber'].title()}\n"
            f"Time: {session['time'].strftime('%a %d %b %I:%M %p')}\n\n"
            f"Calendar link:\n{result.get('link')}\n\n"
            f"—\n"
            f"Reply with:\n"
            f"• Cancel\n"
            f"• Reschedule\n"
            f"• Change barber\n"
            f"Or type MENU"
        )

        return str(resp)

    # ===== CANCEL =====
    if intent == "cancel":
        booking = session.get("last_booking")

        if booking:
            from calendar_helper import cancel_booking
            cancel_booking(booking["calendar_id"], booking["event_id"])
            resp.message("❌ Booking cancelled.")
        else:
            resp.message("No booking found.")

        return str(resp)

    # ===== RESCHEDULE =====
    if intent == "reschedule":
        session["state"] = "reschedule"
        resp.message("🔁 Send new time (e.g. tomorrow 3pm)")
        return str(resp)

    if session.get("state") == "reschedule":
        dt = parse_time(incoming_msg)

        if not dt:
            resp.message("❌ Couldn't understand time.")
            return str(resp)

        booking = session.get("last_booking")

        if booking:
            from calendar_helper import cancel_booking
            cancel_booking(booking["calendar_id"], booking["event_id"])

            result = create_booking(
                phone=from_number,
                service_name=session["service"],
                start_dt=dt,
                name=session["name"]
            )

            session["last_booking"] = result

            resp.message("✅ Rescheduled successfully!")

        return str(resp)

    # ===== FALLBACK =====
    resp.message("🤖 Sorry, I didn’t understand. Type MENU to start.")

    return str(resp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)