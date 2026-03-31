import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import (
    is_free,
    create_booking,
    list_bookings,
    cancel_booking,
    reschedule_booking,
    BARBERS,
)

app = Flask(__name__)

TIMEZONE = ZoneInfo("Europe/London")
SESSIONS = {}

SERVICES = {
    "haircut": {"label": "Haircut", "minutes": 30},
    "beard trim": {"label": "Beard Trim", "minutes": 20},
    "skin fade": {"label": "Skin Fade", "minutes": 45},
    "kids cut": {"label": "Kids Cut", "minutes": 30},
}


def parse_time(text):
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "STRICT_PARSING": False,
        },
    )


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    session = SESSIONS.get(from_number, {})

    data = llm_extract(text)

    # =========================
    # HUMAN FEEL
    # =========================
    if text.lower() in ["hi", "hello", "hey"]:
        msg.body("Hey 👋 What can I get you booked in for?")
        return str(resp)

    if "thank" in text.lower():
        msg.body("You're welcome 😊")
        return str(resp)

    # =========================
    # CANCEL
    # =========================
    if data["intent"] == "cancel":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings 👍")
            return str(resp)

        cancel_booking(bookings[0]["id"])
        msg.body("No worries 👍 all cancelled.")
        return str(resp)

    # =========================
    # RESCHEDULE
    # =========================
    if data["intent"] == "reschedule":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no booking to change 👍")
            return str(resp)

        session["reschedule_id"] = bookings[0]["id"]
        SESSIONS[from_number] = session

        msg.body("No worries 👍 what time works instead? ⏰")
        return str(resp)

    if session.get("reschedule_id"):
        dt = parse_time(text)

        if not dt:
            msg.body("Try something like 'tomorrow 3pm'")
            return str(resp)

        link = reschedule_booking(session["reschedule_id"], dt)
        SESSIONS.pop(from_number, None)

        msg.body(f"All sorted 👌\n{dt.strftime('%A %I:%M%p')}")
        return str(resp)

    # =========================
    # CAPTURE DETAILS
    # =========================
    if data.get("service"):
        session["service"] = data["service"].strip().lower()
    if data.get("barber") and "barber" not in session:
        session["barber"] = data["barber"].strip().lower()

    if data.get("when_text"):
        session["when"] = data["when_text"]

    SESSIONS[from_number] = session

    # =========================
    # NATURAL FLOW
    # =========================
    if "service" not in session:
        msg.body("What would you like to book? ✂️")
        return str(resp)

    if "barber" not in session:
        msg.body("Who would you like? (Jay or Mike) 💈")
        return str(resp)

    if "when" not in session:
        msg.body("When would you like to come in? ⏰")
        return str(resp)

    # =========================
    # BOOK
    # =========================
    dt = parse_time(session["when"])

    if not dt:
        msg.body("Try something like 'tomorrow 3pm'")
        return str(resp)

    service = SERVICES.get(session["service"])
    barber = BARBERS.get(session["barber"])

    if not service or not barber:
        msg.body("Let’s try that again 👍")
        SESSIONS.pop(from_number, None)
        return str(resp)

    end = dt + timedelta(minutes=service["minutes"])

    if not is_free(dt, end, barber):
        msg.body("That time’s taken 😅 want another?")
        return str(resp)

    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        start_dt=dt,
        minutes=service["minutes"],
        name=name,
        barber=barber,
    )

    SESSIONS.pop(from_number, None)

    msg.body(
        f"Nice one {name} 👌 you're booked in!\n\n"
        f"✂️ {service['label']} with {barber['name']}\n"
        f"📅 {dt.strftime('%A %d %b')}\n"
        f"⏰ {dt.strftime('%I:%M%p')}\n\n"
        f"If you need to change anything just message 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))