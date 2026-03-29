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


def apply_hard_rules(text: str) -> dict:
    text_lower = (text or "").lower().strip()

    result = {
        "service": None,
        "barber": None,
        "intent": None,
        "when_text": None,
    }

    if any(word in text_lower for word in ["cancel", "remove"]):
        result["intent"] = "cancel"
    elif any(word in text_lower for word in ["reschedule", "change", "move"]):
        result["intent"] = "reschedule"
    elif any(word in text_lower for word in ["book", "appointment"]):
        result["intent"] = "book"

    if "jay" in text_lower:
        result["barber"] = "jay"
    elif "mike" in text_lower:
        result["barber"] = "mike"

    if "kids cut" in text_lower or "kid cut" in text_lower or "child" in text_lower:
        result["service"] = "kids cut"
    elif "skin fade" in text_lower or "fade" in text_lower:
        result["service"] = "skin fade"
    elif "beard" in text_lower or "trim" in text_lower:
        result["service"] = "beard trim"
    elif "haircut" in text_lower or "hair cut" in text_lower or "cut" in text_lower:
        result["service"] = "haircut"

    parsed = parse_when_text(text)
    if parsed:
        result["when_text"] = text

    return result


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    text_lower = text.lower()
    session = SESSIONS.get(from_number, {})

    if text_lower in ["hi", "hello", "hey", "yo"]:
        msg.body("Hey 👋 what can I book for you?")
        return str(resp)

    if any(w in text_lower for w in ["thanks", "thank you", "cheers"]):
        msg.body("You're welcome 😊 just message anytime 👍")
        return str(resp)

    if any(w in text_lower for w in ["bye", "see you", "later"]):
        msg.body("See you soon 👋")
        return str(resp)

    if text_lower in ["menu", "start", "reset"]:
        SESSIONS.pop(from_number, None)
        msg.body("No problem 👍 what would you like to book? (Haircut / Beard Trim / Skin Fade / Kids Cut)")
        return str(resp)

    # follow-up time support like "2pm"
    direct_time = parse_when_text(text)
    if direct_time and ("service" in session or session.get("reschedule_mode")):
        session["when_text"] = text
        SESSIONS[from_number] = session

    # cancel
    if "cancel" in text_lower:
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings to cancel 👍")
            return str(resp)

        cancelled = cancel_booking(bookings[0]["id"])
        if cancelled:
            SESSIONS.pop(from_number, None)
            msg.body("Done 👍 your booking has been cancelled.")
        else:
            msg.body("I couldn’t cancel that booking just now. Try again in a moment 👍")
        return str(resp)

    # reschedule start
    if "change" in text_lower or "reschedule" in text_lower or "move" in text_lower:
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings to change 👍")
            return str(resp)

        session["reschedule_mode"] = True
        session["reschedule_booking_id"] = bookings[0]["id"]
        SESSIONS[from_number] = session

        msg.body("No worries 👍 what time would you like instead?")
        return str(resp)

    # reschedule time input
    if session.get("reschedule_mode"):
        when_text = session.get("when_text", text)
        dt = parse_when_text(when_text)

        if not dt:
            msg.body("Didn’t catch that time 🤔 try again")
            return str(resp)

        link = reschedule_booking(session.get("reschedule_booking_id"), dt)

        session.clear()
        SESSIONS[from_number] = session

        if not link:
            msg.body("That new slot looks busy 😅 try another time")
            return str(resp)

        msg.body(
            f"All sorted 👌 your booking is now:\n\n"
            f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
            f"{link}\n\n"
            f"Anything else just message 👍"
        )
        return str(resp)

    hard = apply_hard_rules(text)
    data = llm_extract(text)

    if hard.get("service"):
        session["service"] = hard["service"]
    else:
        raw_service = (data.get("service") or "").lower()

        if "kid" in raw_service:
            session["service"] = "kids cut"
        elif "fade" in raw_service:
            session["service"] = "skin fade"
        elif "beard" in raw_service or "trim" in raw_service:
            session["service"] = "beard trim"
        elif "hair" in raw_service or "cut" in raw_service:
            session["service"] = "haircut"

    barber_value = hard.get("barber") or data.get("barber")
    if barber_value:
        session["barber"] = barber_value.lower()

    when_value = hard.get("when_text") or data.get("when_text")
    if when_value:
        session["when_text"] = when_value

    if data.get("name"):
        session["name"] = data["name"]
    else:
        session["name"] = session.get("name", profile_name)

    if "service" not in session:
        msg.body("What would you like to book? ✂️")
        SESSIONS[from_number] = session
        return str(resp)

    if session["service"] not in SERVICES:
        msg.body("I can do haircut, beard trim, skin fade or kids cut 👍")
        SESSIONS[from_number] = session
        return str(resp)

    if "barber" not in session or session["barber"] not in BARBERS:
        msg.body("Which barber would you like? (Jay or Mike)")
        SESSIONS[from_number] = session
        return str(resp)

    if "when_text" not in session:
        msg.body("When would you like to come in?")
        SESSIONS[from_number] = session
        return str(resp)

    dt = parse_when_text(session["when_text"])

    if not dt:
        msg.body("I didn’t catch that time 🤔 try 'tomorrow 3pm'")
        SESSIONS[from_number] = session
        return str(resp)

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]
    end_dt = dt + timedelta(minutes=service["minutes"])

    if not is_free(dt, end_dt, barber):
        msg.body("That slot is taken 😅 try another time")
        SESSIONS[from_number] = session
        return str(resp)

    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        start_dt=dt,
        minutes=service["minutes"],
        name=session["name"],
        barber=barber,
    )

    customer_name = session["name"]
    SESSIONS.pop(from_number, None)

    link_line = result.get("link", "")
    if link_line:
        link_line = f"\n\n{link_line}"

    msg.body(
        f"Nice one {customer_name} 👌 you're booked in!\n\n"
        f"{service['label']} with {barber['name']}\n"
        f"{dt.strftime('%a %d %b at %I:%M%p')}"
        f"{link_line}\n\n"
        f"If you need to change or cancel, just message 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))