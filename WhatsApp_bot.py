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

SERVICES = {
    "haircut": {"label": "Haircut", "duration": 30},
    "beard trim": {"label": "Beard Trim", "duration": 20},
}

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    data = llm_extract(text)

    intent = data.get("intent")
    service_key = data.get("service")
    barber_key = data.get("barber")
    when_text = data.get("when_text")
    name = data.get("name") or profile_name

    # =========================
    # BOOKING FLOW
    # =========================
    if intent == "book":

        if not service_key or service_key not in SERVICES:
            msg.body("What would you like to book? ✂️")
            return str(resp)

        if not barber_key or barber_key not in BARBERS:
            msg.body("Which barber would you like? (Jay or Mike)")
            return str(resp)

        if not when_text:
            msg.body("What time works for you? ⏰")
            return str(resp)

        dt = dateparser.parse(
            when_text,
            settings={
                "TIMEZONE": "Europe/London",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )

        if not dt:
            msg.body("I didn’t catch that time 🤔 try 'tomorrow 3pm'")
            return str(resp)

        service = SERVICES[service_key]
        barber = BARBERS[barber_key]

        end_dt = dt + timedelta(minutes=service["duration"])

        if not is_free(dt, end_dt, barber):
            msg.body("That slot is taken 😬 try another time")
            return str(resp)

        result = create_booking(
            phone=from_number,
            service_name=service["label"],
            start_dt=dt,
            minutes=service["duration"],
            name=name,
            barber=barber,
        )

        msg.body(
            f"✅ Booked!\n"
            f"{service['label']} with {barber['name']}\n"
            f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
            f"{result.get('link', '')}"
        )

        return str(resp)

    # =========================
    # FALLBACK
    # =========================
    msg.body("Hey 👋 just tell me what you want like:\n'Book haircut with Jay tomorrow at 3pm'")
    return str(resp)


if __name__ == "__main__":
    app.run(port=10000)