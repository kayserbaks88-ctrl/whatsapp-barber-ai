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

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
SESSIONS = {}

SERVICES = {
    "haircut": {"label": "Haircut", "minutes": 30},
    "beard trim": {"label": "Beard Trim", "minutes": 20},
    "skin fade": {"label": "Skin Fade", "minutes": 45},
    "kids cut": {"label": "Kids Cut", "minutes": 30},
}


def parse_when_text(text: str):
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(TIMEZONE),
        },
    )


def apply_hard_rules(text: str):
    text_lower = text.lower()

    result = {
        "intent": None,
        "service": None,
        "barber": None,
        "when_text": None,
    }

    # INTENTS
    if "cancel" in text_lower:
        result["intent"] = "cancel"

    # 🔥 SMART CHANGE FIRST (VERY IMPORTANT)
    elif "change to" in text_lower or "switch to" in text_lower or "instead" in text_lower:
        result["intent"] = "change_service_smart"

    # 🔥 THEN NORMAL CHANGE
    elif "change service" in text_lower or "different service" in text_lower:
        result["intent"] = "change_service"

    # 🔥 THEN RESCHEDULE
    elif any(w in text_lower for w in ["reschedule", "move"]):
        result["intent"] = "reschedule"

    # THEN BOOK
    elif any(w in text_lower for w in ["book", "appointment"]):
        result["intent"] = "book"

    # BARBER
    if "jay" in text_lower:
        result["barber"] = "jay"
    elif "mike" in text_lower:
        result["barber"] = "mike"

    # SERVICE
    if "kid" in text_lower:
        result["service"] = "kids cut"
    elif "fade" in text_lower:
        result["service"] = "skin fade"
    elif "beard" in text_lower:
        result["service"] = "beard trim"
    elif "hair" in text_lower or "cut" in text_lower:
        result["service"] = "haircut"

    # TIME
    if parse_when_text(text):
        result["when_text"] = text

    return result


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    session = SESSIONS.get(from_number, {})
    text_lower = text.lower()

    # =========================
    # HUMAN CHAT
    # =========================
    if text_lower in ["hi", "hello", "hey"]:
        msg.body("Hey 👋 What can I book for you today? ✂️")
        return str(resp)

    if "thank" in text_lower or "cheers" in text_lower:
        msg.body("You're welcome 😊")
        return str(resp)

    if "bye" in text_lower:
        msg.body("See you soon 👋")
        return str(resp)

    # =========================
    # QUICK TIME DETECTION
    # =========================
    direct_time = parse_when_text(text)
    if direct_time and ("service" in session or session.get("reschedule_mode")):
        session["when_text"] = text
        SESSIONS[from_number] = session

    # =========================
    # CANCEL
    # =========================
    if "cancel" in text_lower:
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings to cancel 👍")
            return str(resp)

        success = cancel_booking(bookings[0]["id"])

        if success:
            SESSIONS.pop(from_number, None)
            msg.body("Done 👍 your booking has been cancelled.")
        else:
            msg.body("Couldn’t cancel it 😅 try again")

        return str(resp)

    # =========================
    # RESCHEDULE START
    # =========================
    if any(w in text_lower for w in ["reschedule", "change", "move"]):
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings to change 👍")
            return str(resp)

        session["reschedule_mode"] = True
        session["reschedule_booking_id"] = bookings[0]["id"]
        SESSIONS[from_number] = session

        msg.body("No worries 👍 what time would you like instead? ⏰")
        return str(resp)

    # =========================
    # CHANGE SERVICE
    # =========================
    hard = apply_hard_rules(text)

    if hard.get("intent") == "change_service":
        session.pop("service", None)
        session.pop("barber", None)
        session.pop("when_text", None)

        SESSIONS[from_number] = session
        msg.body("No problem 👍 what would you like instead? ✂️")
        return str(resp)

    # =========================
    # RESCHEDULE TIME INPUT
    # =========================
    if session.get("reschedule_mode"):
        when_text = session.get("when_text", text)
        dt = parse_when_text(when_text)

        if not dt:
            msg.body("Didn’t catch that time 🤔 try 'tomorrow 3pm'")
            return str(resp)

        link = reschedule_booking(session["reschedule_booking_id"], dt)

        session.clear()
        SESSIONS[from_number] = session

        if not link:
            msg.body("That time is taken 😅 try another")
            return str(resp)

        msg.body(
            f"All sorted 👌\n\n📅 {dt.strftime('%a %d %b')}\n⏰ {dt.strftime('%I:%M%p')}\n\n{link}"
        )
        return str(resp)

    # =========================
    # AI + HARD RULES
    # =========================
    data = llm_extract(text)

    if hard.get("service"):
        session["service"] = hard["service"]
    elif data.get("service"):
        session["service"] = data["service"]

    if hard.get("barber") or data.get("barber"):
        session["barber"] = (hard.get("barber") or data.get("barber")).lower()

    if hard.get("when_text") or data.get("when_text"):
        session["when_text"] = hard.get("when_text") or data.get("when_text")

    session["name"] = data.get("name") or session.get("name") or profile_name

    # =========================
    # FLOW
    # =========================
    if "service" not in session:
        msg.body("What would you like to book? ✂️")
        SESSIONS[from_number] = session
        return str(resp)

    if "barber" not in session:
        msg.body("Which barber? (Jay or Mike)")
        SESSIONS[from_number] = session
        return str(resp)

    if "when_text" not in session:
        msg.body("When would you like to come in? ⏰")
        SESSIONS[from_number] = session
        return str(resp)

    dt = parse_when_text(session["when_text"])

    if not dt:
        msg.body("Try something like 'tomorrow 3pm'")
        return str(resp)

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]

    end_dt = dt + timedelta(minutes=service["minutes"])

    if not is_free(dt, end_dt, barber):
        msg.body("That slot is taken 😅 try another")
        return str(resp)

    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        start_dt=dt,
        minutes=service["minutes"],
        name=session["name"],
        barber=barber,
    )

    SESSIONS.pop(from_number, None)

    link = result.get("link", "")

    msg.body(
        f"Nice one {session['name']} 👌 you're booked in!\n\n"
        f"✂️ {service['label']} with {barber['name']}\n"
        f"📅 {dt.strftime('%a %d %b')}\n"
        f"⏰ {dt.strftime('%I:%M%p')}\n\n"
        f"{link}\n\n"
        f"If you need to change or cancel, just message 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))