import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import is_free, create_booking, BARBERS

app = Flask(__name__)

TIMEZONE = ZoneInfo("Europe/London")

# 🔑 MEMORY (per user)
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

    # =========================
    # SESSION LOAD
    # =========================
    session = SESSIONS.get(from_number, {})

    # =========================
    # HUMAN REPLIES FIRST
    # =========================
    if any(word in text_lower for word in ["hi", "hello", "hey"]):
        msg.body("Hey 👋 what can I book for you?")
        return str(resp)

    if any(word in text_lower for word in ["thanks", "thank you", "cheers"]):
        msg.body("You're welcome 😊 just message me anytime to book.")
        return str(resp)

    if any(word in text_lower for word in ["bye", "see you", "later"]):
        msg.body("See you soon 👋")
        return str(resp)

    # =========================
    # AI EXTRACTION
    # =========================
    data = llm_extract(text)

    # merge into session (THIS IS THE MAGIC)
    if data.get("service"):
        session["service"] = data["service"]

    if data.get("barber"):
        session["barber"] = data["barber"]

    if data.get("when_text"):
        session["when_text"] = data["when_text"]

    if data.get("name"):
        session["name"] = data["name"]
    else:
        session["name"] = session.get("name", profile_name)

    # =========================
    # ASK MISSING INFO
    # =========================
    if "service" not in session:
        msg.body("What would you like to book? ✂️")
        SESSIONS[from_number] = session
        return str(resp)

    if session["service"] not in SERVICES:
        msg.body("I can do haircut or beard trim 👍")
        return str(resp)

    if "barber" not in session or session["barber"] not in BARBERS:
        msg.body("Which barber would you like? (Jay or Mike)")
        SESSIONS[from_number] = session
        return str(resp)

    if "when_text" not in session:
        msg.body("What time works for you? ⏰")
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
        msg.body("I didn’t catch that time 🤔 try 'tomorrow 3pm'")
        return str(resp)

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]

    end_dt = dt + timedelta(minutes=service["minutes"])

    # =========================
    # CHECK AVAILABILITY
    # =========================
    if not is_free(dt, end_dt, barber):
        msg.body("That slot is taken 😅 try another time")
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

    # clear session after success
    SESSIONS.pop(from_number, None)

    # =========================
    # CONFIRMATION
    # =========================
    msg.body(
        f"✅ Booked!\n"
        f"{service['label']} with {barber['name']}\n"
        f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
        f"{result.get('link', '')}\n\n"
        f"Need to change anything? Just tell me 👍"
    )

    return str(resp)


import os

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))