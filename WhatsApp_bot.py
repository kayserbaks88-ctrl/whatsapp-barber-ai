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
    list_upcoming,
    cancel_booking,
)

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "TrimTech AI")

# MEMORY (simple session)
SESSIONS = {}

# SERVICES
SERVICES = {
    "haircut": {"label": "Haircut", "price": 18, "duration": 30},
    "skin fade": {"label": "Skin Fade", "price": 22, "duration": 30},
    "beard trim": {"label": "Beard Trim", "price": 12, "duration": 20},
    "shape up": {"label": "Shape Up", "price": 10, "duration": 20},
    "kids cut": {"label": "Kids Cut", "price": 15, "duration": 30},
}

BARBERS = {
    "mike": {"name": "Mike", "calendar_id": os.getenv("MIKE_CALENDAR")},
    "jay": {"name": "Jay", "calendar_id": os.getenv("JAY_CALENDAR")},
}


# ------------------------
# SESSION
# ------------------------
def get_session(phone):
    return SESSIONS.setdefault(phone, {})


def reset_session(phone):
    SESSIONS[phone] = {}


# ------------------------
# PARSE TIME
# ------------------------
def parse_time(text):
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(TIMEZONE),
        },
    )


# ------------------------
# ROUTE
# ------------------------
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").strip()
    profile_name = request.values.get("ProfileName", "").strip()

    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(from_number)
    text = incoming_msg.lower()

    # =========================
    # 🧠 LLM FIRST
    # =========================
    ai = llm_extract(text)

    intent = ai.get("intent")
    service_key = ai.get("service")
    when_text = ai.get("time")

    # =========================
    # 👋 GREETING
    # =========================
    if intent == "greeting":
        msg.body("Hey 👋 what can I book for you?")
        return str(resp)

    # =========================
    # 🙏 THANKS
    # =========================
    if intent == "thanks":
        msg.body("No worries 😊 just let me know if you need anything.")
        return str(resp)

    # =========================
    # 📋 MENU
    # =========================
    if intent == "menu":
        menu = "💈 *Welcome to TrimTech AI*\n\nChoose a service:\n"
        for i, (k, v) in enumerate(SERVICES.items(), 1):
            menu += f"{i}. {v['label']} - £{v['price']}\n"

        menu += "\nYou can also say:\n"
        menu += "• book haircut tomorrow 3pm\n"
        menu += "• skin fade friday 2pm\n"
        menu += "• cancel my booking\n"
        menu += "• reschedule my appointment"

        msg.body(menu)
        return str(resp)

    # =========================
    # ❌ CANCEL
    # =========================
    if intent == "cancel":
        bookings = list_upcoming(from_number)

        if not bookings:
            msg.body("You don’t have any bookings to cancel 👍")
            return str(resp)

        text_out = "Which booking would you like to cancel?\n"
        for i, b in enumerate(bookings, 1):
            text_out += f"{i}. {b['service']} - {b['start']}\n"

        session["awaiting_cancel"] = True
        msg.body(text_out)
        return str(resp)

    if session.get("awaiting_cancel"):
        bookings = list_upcoming(from_number)

        try:
            index = int(text) - 1
            event = bookings[index]

            cancel_booking(event["id"])

            session.clear()

            msg.body("❌ Booking cancelled. Let me know if you need anything else 👍")
            return str(resp)

        except:
            msg.body("Just reply with the number of the booking 👍")
            return str(resp)

    # =========================
    # 🔁 RESCHEDULE
    # =========================
    if intent == "reschedule":
        bookings = list_upcoming(from_number)

        if not bookings:
            msg.body("You don’t have any bookings to change 👍")
            return str(resp)

        text_out = "Which booking would you like to reschedule?\n"
        for i, b in enumerate(bookings, 1):
            text_out += f"{i}. {b['service']} - {b['start']}\n"

        session["awaiting_reschedule"] = True
        msg.body(text_out)
        return str(resp)

    if session.get("awaiting_reschedule"):
        bookings = list_upcoming(from_number)

        try:
            index = int(text) - 1
            session["reschedule_event"] = bookings[index]
            session["awaiting_reschedule_time"] = True
            session.pop("awaiting_reschedule")

            msg.body("Nice 👍 what new time works for you?")
            return str(resp)

        except:
            msg.body("Reply with the number 👍")
            return str(resp)

    if session.get("awaiting_reschedule_time"):
        dt = parse_time(incoming_msg)

        if not dt:
            msg.body("Got you — what time works best? (e.g. tomorrow 3pm)")
            return str(resp)

        event = session["reschedule_event"]

        cancel_booking(event["id"])

        create_booking(
            phone=from_number,
            service_name=event["service"],
            start_dt=dt,
            minutes=30,
            name=profile_name or "Guest",
            barber=session.get("barber") or list(BARBERS.values())[0]  # ✅ ADD HERE
        )

        session.clear()

        msg.body("🔁 Done! Your booking has been updated 👍")
        return str(resp)

    # =========================
    # 📅 BOOKING FLOW (AI)
    # =========================
    if intent == "book" and service_key:
        service = SERVICES.get(service_key)

        if not service:
            msg.body("Hmm I didn’t catch that service 🤔 try again")
            return str(resp)

        session["service"] = service_key

        msg.body(f"Nice 👌 booking a *{service['label']}*.\nAny barber preference?")
        return str(resp)

    # BARBER
    if "service" in session and "barber" not in session:
        if text in BARBERS:
            session["barber"] = text
            msg.body("Perfect 👍 what time works for you?")
            return str(resp)

    # TIME
    if "service" in session and "barber" in session and "time" not in session:
        dt = parse_time(incoming_msg)

        if not dt:
            msg.body("Got you — what time works best? (e.g. tomorrow 3pm)")
            return str(resp)

        session["time"] = dt
        msg.body("Nice 👌 what name should I put for the booking?")
        return str(resp)

    # NAME + CONFIRM
    if "service" in session and "time" in session:
        name = incoming_msg

        service_key = session["service"]
        barber_key = session["barber"]
        dt = session["time"]

        service = SERVICES[service_key]
        barber = BARBERS[barber_key]

        result = create_booking(
            phone=from_number,
            service_name=service["label"],
            start_dt=dt,
            minutes=service["duration"],
            name=name,
        )

        session.clear()

        msg.body(
            f"""✅ You're booked in!

✂️ {service['label']} with {barber['name']}
📅 {dt.strftime('%a %d %b at %I:%M %p')}

📍 {result.get("link")}

Need to change anything? Just tell me 👍"""
        )
        return str(resp)

    # =========================
    # 🤖 FALLBACK
    # =========================
    msg.body("Hey 👋 just tell me what you'd like to book 👍")
    return str(resp)


# =========================
# RUN
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)