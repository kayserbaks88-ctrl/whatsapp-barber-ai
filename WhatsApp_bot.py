import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import create_booking

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

SESSIONS = {}

SERVICES = {
    "haircut": {"label": "Haircut", "duration": 30},
    "skin fade": {"label": "Skin Fade", "duration": 45},
    "beard trim": {"label": "Beard Trim", "duration": 20},
    "shape up": {"label": "Shape Up", "duration": 20},
    "kids cut": {"label": "Kids Cut", "duration": 30},
}

BARBERS = {
    "mike": os.getenv("BARBER_MIKE_CALENDAR_ID"),
    "jay": os.getenv("BARBER_JAY_CALENDAR_ID"),
}

# -----------------------
# HELPERS
# -----------------------

def get_session(phone):
    if phone not in SESSIONS:
        SESSIONS[phone] = {}
    return SESSIONS[phone]

def parse_time(text):
    dt = dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    return dt

# -----------------------
# ROUTE
# -----------------------

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").strip()

    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(from_number)
    text = incoming_msg.lower()

    # -----------------------
    # LLM FIRST
    # -----------------------

    ai = llm_extract(text)
    intent = ai.get("intent")
    service = ai.get("service")
    when_text = ai.get("time")

    # -----------------------
    # SMALL TALK
    # -----------------------

    if intent == "greeting":
        msg.body("😊 Hey! What can I book for you?")
        return str(resp)

    if intent == "thanks":
        msg.body("😊 You're welcome! Let me know if you need anything.")
        return str(resp)

    # -----------------------
    # NATURAL BOOKING
    # -----------------------

    if intent == "book" and service:
        session["service"] = service

        if service in SERVICES:
            if when_text:
                dt = parse_time(when_text)

                if dt:
                    session["time"] = dt

                    msg.body(
                        f"Got it 👌 {service.title()} {when_text}\n\nWhich barber?\n1. Mike\n2. Jay"
                    )
                    session["state"] = "awaiting_barber"
                    return str(resp)

        msg.body("Which service would you like?")
        return str(resp)

    # -----------------------
    # SELECT BARBER
    # -----------------------

    if session.get("state") == "awaiting_barber":
        if "mike" in text or text == "1":
            session["barber"] = "mike"
        elif "jay" in text or text == "2":
            session["barber"] = "jay"
        else:
            msg.body("Please choose: Mike or Jay")
            return str(resp)

        msg.body("Please confirm your name.")
        session["state"] = "awaiting_name"
        return str(resp)

    # -----------------------
    # FINAL CONFIRM
    # -----------------------

    if session.get("state") == "awaiting_name":
        name = incoming_msg.strip()
        session["name"] = name

        service_key = session["service"]
        barber_key = session["barber"]
        dt = session["time"]

        duration = SERVICES[service_key]["duration"]
        calendar_id = BARBERS[barber_key]

        result = create_booking(
            phone=from_number,
            service_name=SERVICES[service_key]["label"],
            start_dt=dt,
            minutes=duration,
            name=name,
            barber_name=barber_key.title(),
            calendar_id=calendar_id,
        )

        msg.body(
            f"""✅ Booking confirmed!

Name: {name}
Service: {SERVICES[service_key]['label']}
Barber: {barber_key.title()}
Time: {dt.strftime('%a %d %b %I:%M %p')}

📅 {result.get("link")}

You can reply:
• CHANGE
• RESCHEDULE
• CANCEL
"""
        )

        SESSIONS.pop(from_number, None)
        return str(resp)

    # -----------------------
    # CANCEL / RESCHEDULE
    # -----------------------

    if intent == "cancel":
        msg.body("No problem 👍 Which booking would you like to cancel?")
        return str(resp)

    if intent == "reschedule":
        msg.body("Sure 👌 What new time would you like?")
        return str(resp)

    if intent == "availability":
        msg.body("Let me check available times for you ⏳")
        return str(resp)

    # -----------------------
    # FALLBACK
    # -----------------------

    msg.body("I can help you book, change or cancel appointments 😊")
    return str(resp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)