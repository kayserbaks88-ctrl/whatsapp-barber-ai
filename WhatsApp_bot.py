import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import is_free, create_booking, BARBERS

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

SERVICES = {
    "haircut": {"label": "Haircut", "minutes": 30},
    "skin fade": {"label": "Skin Fade", "minutes": 45},
    "beard trim": {"label": "Beard Trim", "minutes": 20},
}

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    text = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "")
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    text_lower = text.lower()

    # =========================
    # HUMAN REPLIES (FIRST)
    # =========================
    if any(word in text_lower for word in ["hi", "hello", "hey"]):
        msg.body("Hey 👋 what can I book for you?")
        return str(resp)

    if any(word in text_lower for word in ["thanks", "thank you", "cheers"]):
        msg.body("You’re welcome 😊 just message me anytime to book.")
        return str(resp)

    if any(word in text_lower for word in ["ok", "okay", "cool", "alright"]):
        msg.body("Perfect 👌 just let me know what you’d like to book.")
        return str(resp)

    if any(word in text_lower for word in ["bye", "later", "see you"]):
        msg.body("See you soon 👋")
        return str(resp)

    # =========================
    # AI BOOKING LOGIC
    # =========================
    data = llm_extract(text)

    if data.get("intent") == "book":
        service_key = data.get("service")
        barber_key = data.get("barber")
        when_text = data.get("when")

        if not service_key or service_key not in SERVICES:
            msg.body("Got you 👍 what service would you like?")
            return str(resp)

        if not barber_key or barber_key not in BARBERS:
            msg.body("Nice 👌 which barber would you like? (Jay or Mike)")
            return str(resp)

        dt = dateparser.parse(
            when_text,
            settings={
                "TIMEZONE": str(TIMEZONE),
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )

        if not dt:
            msg.body("Got you 👍 what time works best?")
            return str(resp)

        service = SERVICES[service_key]
        barber = BARBERS[barber_key]

        end_dt = dt + timedelta(minutes=service["minutes"])

        if not is_free(dt, end_dt, barber):
            msg.body("That time is taken 😕 want another time?")
            return str(resp)

        result = create_booking(
            phone=from_number,
            service_name=service["label"],
            start_dt=dt,
            minutes=service["minutes"],
            name=profile_name,
            barber=barber,
        )

        msg.body(
            f"✅ Booked!\n"
            f"{service['label']} with {barber['name']}\n"
            f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
            f"📅 {result.get('link')}"
        )
        return str(resp)

    # =========================
    # FINAL FALLBACK (LAST)
    # =========================
    msg.body(
        "Hey 👋 just tell me what you want like:\n"
        "'Book haircut with Jay tomorrow at 3pm'"
    )
    return str(resp)


if __name__ == "__main__":
    app.run()