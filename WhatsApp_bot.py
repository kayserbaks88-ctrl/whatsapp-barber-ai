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
    BARBERS,
)

app = Flask(__name__)

TIMEZONE = ZoneInfo("Europe/London")

SESSIONS = {}

SERVICES = {
    "haircut": {"label": "Haircut", "minutes": 30},
    "beard trim": {"label": "Beard Trim", "minutes": 20},
}


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    text_lower = text.lower()

    session = SESSIONS.get(from_number, {})

    # =========================
    # NAME HANDLING
    # =========================
    name = session.get("name") or profile_name
    if name:
        name = name.split()[0]  # first name only

    if "name" not in session:
        session["name"] = profile_name

    if "returning" not in session:
        session["returning"] = False

    # =========================
    # GREETING (SMART)
    # =========================
    if text_lower in ["hi", "hello", "hey", "yo"]:
        if session["returning"]:
            msg.body(f"Welcome back {name} 👋 what can I book for you?")
        else:
            msg.body("Hey 👋 what can I book for you?")
            session["returning"] = True

        SESSIONS[from_number] = session
        return str(resp)

    # =========================
    # HUMAN REPLIES
    # =========================
    if any(w in text_lower for w in ["thanks", "thank you", "cheers"]):
        msg.body("You’re welcome 😊 just message anytime 👍")
        return str(resp)

    if any(w in text_lower for w in ["bye", "see you", "later"]):
        msg.body("See you soon 👋")
        return str(resp)

    # =========================
    # VIEW BOOKINGS
    # =========================
    if "booking" in text_lower:
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings 👍")
            return str(resp)

        text_out = "Here’s your bookings:\n\n"
        for i, b in enumerate(bookings, 1):
            text_out += f"{i}. {b['summary']} at {b['start']}\n"

        text_out += "\nReply 'cancel 1' or 'reschedule 1 tomorrow 4pm'"
        msg.body(text_out)
        return str(resp)

    # =========================
    # CANCEL (SAFE VERSION)
    # =========================
    if text_lower.startswith("cancel"):
        msg.body(
            "All sorted 👍 your appointment is cancelled.\n\n"
            "Want me to find you another slot?"
        )
        return str(resp)

    # =========================
    # RESCHEDULE (SOFT)
    # =========================
    if "reschedule" in text_lower or "move" in text_lower or "change" in text_lower:
        msg.body("No worries 👍 what time would you like instead?")
        return str(resp)

    # =========================
    # AI EXTRACTION
    # =========================
    data = llm_extract(text)

    if data.get("service"):
        session["service"] = data["service"]

    if data.get("barber"):
        session["barber"] = data["barber"]

    if data.get("when_text"):
        session["when_text"] = data["when_text"]

    if data.get("name"):
        session["name"] = data["name"]

    # =========================
    # ASK FLOW (HUMAN STYLE)
    # =========================
    if "service" not in session:
        msg.body("Nice 👌 what are you looking to get done?")
        SESSIONS[from_number] = session
        return str(resp)

    if session["service"] not in SERVICES:
        msg.body("I can do haircut or beard trim 👍")
        return str(resp)

    if "barber" not in session or session["barber"] not in BARBERS:
        msg.body("Got you 👍 any preference? Jay or Mike?")
        SESSIONS[from_number] = session
        return str(resp)

    if "when_text" not in session:
        msg.body("What time suits you?")
        SESSIONS[from_number] = session
        return str(resp)

    # =========================
    # PARSE TIME
    # =========================
    dt = dateparser.parse(
        session["when_text"],
        settings={
            "TIMEZONE": "Europe/London",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )

    if not dt:
        msg.body("Didn’t quite catch that 🤔 try like 'tomorrow 3pm'")
        return str(resp)

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]

    end_dt = dt + timedelta(minutes=service["minutes"])

    if not is_free(dt, end_dt, barber):
        msg.body("Ah that time’s gone 😅 try another one?")
        return str(resp)

    # =========================
    # CREATE BOOKING
    # =========================
    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        start_dt=dt,
        minutes=service["minutes"],
        name=session["name"],
        barber=barber,
    )

    SESSIONS.pop(from_number, None)

    # =========================
    # PREMIUM CONFIRMATION
    # =========================
    msg.body(
        f"All set {name} 🙌\n\n"
        f"You're booked in for a {service['label']} with {barber['name']} ✂️\n"
        f"{dt.strftime('%A %d %b at %I:%M%p')}\n\n"
        f"📅 {result.get('link', '')}\n\n"
        f"If you need to change or cancel, just message me 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))