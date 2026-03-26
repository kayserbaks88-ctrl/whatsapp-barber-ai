import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from calendar_helper import (
    is_free,
    create_booking,
    list_upcoming,
    cancel_booking,
    BARBERS
)

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

SESSIONS = {}

SERVICES = {
    "haircut": {"label": "Haircut", "duration": 30},
    "beard trim": {"label": "Beard Trim", "duration": 20},
}

def get_session(phone):
    if phone not in SESSIONS:
        SESSIONS[phone] = {}
    return SESSIONS[phone]


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    session = get_session(from_number)

    resp = MessagingResponse()
    msg = resp.message()

    text_lower = text.lower()

    # =========================
    # START
    # =========================
    if not session:
        msg.body("Hey 👋 what can I book for you?")
        return str(resp)

    # =========================
    # SERVICE DETECTION
    # =========================
    if text_lower in SERVICES:
        session["service"] = text_lower
        session["awaiting_barber"] = True
        msg.body(f"Nice 👌 booking a {SERVICES[text_lower]['label']}.\nAny barber preference?")
        return str(resp)

    # =========================
    # BARBER
    # =========================
    if session.get("awaiting_barber"):
        if text_lower in BARBERS:
            session["barber"] = BARBERS[text_lower]
            session["awaiting_barber"] = False
            session["awaiting_time"] = True
            msg.body("Perfect 👍 what time works for you?")
            return str(resp)

    # =========================
    # TIME
    # =========================
    if session.get("awaiting_time"):
        dt = dateparser.parse(
            text,
            settings={
                "TIMEZONE": "Europe/London",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )

        if not dt:
            msg.body("Didn’t catch that time 🤔 try like 'tomorrow 3pm'")
            return str(resp)

        service = SERVICES[session["service"]]
        barber = session["barber"]

        end_dt = dt + timedelta(minutes=service["duration"])

        if not is_free(dt, end_dt, barber):
            msg.body("That slot’s taken 😬 try another time")
            return str(resp)

        result = create_booking(
            phone=from_number,
            service_name=service["label"],
            start_dt=dt,
            minutes=service["duration"],
            name=profile_name,
            barber=barber,
        )

        session.clear()

        msg.body(
            f"✅ Booked!\n"
            f"{service['label']} with {barber['name']}\n"
            f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
            f"{result.get('link', '')}"
        )

        return str(resp)

    # =========================
    # DEFAULT
    # =========================
    msg.body("Hey 👋 what can I book for you?")
    return str(resp)


if __name__ == "__main__":
    app.run(port=10000)