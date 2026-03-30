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
    text = (text or "").strip()
    if not text:
        return None

    parsed = dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(TIMEZONE),
        },
    )

    # fallback for simple time-only inputs like "2pm" / "6pm"
    if not parsed and any(x in text.lower() for x in ["am", "pm"]):
        parsed = dateparser.parse(
            f"today {text}",
            settings={
                "TIMEZONE": str(TIMEZONE),
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.now(TIMEZONE),
            },
        )

    return parsed


def apply_hard_rules(text: str):
    text_lower = (text or "").lower()

    result = {
        "intent": None,
        "service": None,
        "barber": None,
        "when_text": None,
    }

    # intents
    if "cancel" in text_lower:
        result["intent"] = "cancel"

    elif "change to" in text_lower or "switch to" in text_lower:
        result["intent"] = "change_service_smart"

    elif "change service" in text_lower or "different service" in text_lower:
        result["intent"] = "change_service"

    elif any(w in text_lower for w in ["reschedule", "move"]):
        result["intent"] = "reschedule"

    elif any(w in text_lower for w in ["book", "appointment"]):
        result["intent"] = "book"

    # barber
    if "jay" in text_lower:
        result["barber"] = "jay"
    elif "mike" in text_lower:
        result["barber"] = "mike"

    # service
    if "kid" in text_lower:
        result["service"] = "kids cut"
    elif "fade" in text_lower:
        result["service"] = "skin fade"
    elif "beard" in text_lower or "trim" in text_lower:
        result["service"] = "beard trim"
    elif "hair" in text_lower or "cut" in text_lower:
        result["service"] = "haircut"

    # time
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

    hard = apply_hard_rules(text)
    data = llm_extract(text)

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

    if text_lower in ["menu", "start", "reset"]:
        SESSIONS.pop(from_number, None)
        msg.body(
            "No problem 👍\n\n"
            "What would you like to book?\n"
            "• Haircut\n"
            "• Beard Trim\n"
            "• Skin Fade\n"
            "• Kids Cut"
        )
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
    if hard.get("intent") == "cancel":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings to cancel 👍")
            return str(resp)

        success = cancel_booking(bookings[0]["id"])

        if success:
            SESSIONS.pop(from_number, None)
            msg.body("Done 👍 your booking has been cancelled.")
        else:
            msg.body("Couldn’t cancel it properly 😅 try again")

        return str(resp)

    # =========================
    # SMART SERVICE CHANGE
    # keep same time + same barber, only swap service
    # =========================
    if hard.get("intent") == "change_service_smart":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You don’t have a booking to change 👍")
            return str(resp)

        booking = bookings[0]

        new_service = hard.get("service") or data.get("service")
        if not new_service or new_service not in SERVICES:
            msg.body("What would you like to change it to? ✂️")
            return str(resp)

        # same time + same barber
        try:
            original_dt = datetime.fromisoformat(booking["start"])
        except Exception:
            msg.body("I couldn’t read your current booking time 😅")
            return str(resp)

        barber_key = booking.get("barber_key")
        barber = BARBERS.get(barber_key)
        if not barber:
            msg.body("I couldn’t find the barber for that booking 😅")
            return str(resp)

        service = SERVICES[new_service]
        new_end = original_dt + timedelta(minutes=service["minutes"])

        # allow keeping same event slot by cancelling old one first
        cancelled = cancel_booking(booking["id"])
        if not cancelled:
            msg.body("I couldn’t update that booking right now 😅 try again")
            return str(resp)

        if not is_free(original_dt, new_end, barber):
            msg.body("That upgrade would clash 😅 try changing the time too")
            return str(resp)

        result = create_booking(
            phone=from_number,
            service_name=service["label"],
            start_dt=original_dt,
            minutes=service["minutes"],
            name=session.get("name", profile_name),
            barber=barber,
        )

        SESSIONS.pop(from_number, None)

        link = result.get("link", "")
        if link:
            link = f"\n\n🔗 {link}"

        msg.body(
            f"Done 👌 I’ve changed it for you.\n\n"
            f"✂️ {service['label']} with {barber['name']}\n"
            f"📅 {original_dt.strftime('%a %d %b')}\n"
            f"⏰ {original_dt.strftime('%I:%M%p')}"
            f"{link}\n\n"
            f"Anything else, just message 👍"
        )
        return str(resp)

    # =========================
    # BASIC CHANGE SERVICE
    # ask which service instead
    # =========================
    if hard.get("intent") == "change_service":
        session.pop("service", None)
        session.pop("barber", None)
        session.pop("when_text", None)

        SESSIONS[from_number] = session
        msg.body("No problem 👍 what would you like instead? ✂️")
        return str(resp)

    # =========================
    # RESCHEDULE START
    # =========================
    if hard.get("intent") == "reschedule":
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
            f"All sorted 👌\n\n"
            f"📅 {dt.strftime('%a %d %b')}\n"
            f"⏰ {dt.strftime('%I:%M%p')}\n\n"
            f"{link}"
        )
        return str(resp)

    # =========================
    # AI + HARD RULES
    # =========================
    if hard.get("service"):
        session["service"] = hard["service"]
    elif data.get("service") in SERVICES:
        session["service"] = data["service"]

    barber_value = hard.get("barber") or data.get("barber")
    if barber_value and barber_value in BARBERS:
        session["barber"] = barber_value.lower()

    when_value = hard.get("when_text") or data.get("when_text")
    if when_value:
        session["when_text"] = when_value

    session["name"] = data.get("name") or session.get("name") or profile_name

    # =========================
    # FLOW
    # =========================
    if "service" not in session:
        SESSIONS[from_number] = session
        msg.body("What would you like to book? ✂️")
        return str(resp)

    if "barber" not in session:
        SESSIONS[from_number] = session
        msg.body("Which barber? (Jay or Mike) 💈")
        return str(resp)

    if "when_text" not in session:
        SESSIONS[from_number] = session
        msg.body("When would you like to come in? ⏰")
        return str(resp)

    dt = parse_when_text(session["when_text"])
    if not dt:
        SESSIONS[from_number] = session
        msg.body("Try something like 'tomorrow 3pm'")
        return str(resp)

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]
    end_dt = dt + timedelta(minutes=service["minutes"])

    if not is_free(dt, end_dt, barber):
        SESSIONS[from_number] = session
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

    customer_name = session["name"]
    SESSIONS.pop(from_number, None)

    link = result.get("link", "")
    if link:
        link = f"\n\n🔗 {link}"

    msg.body(
        f"Nice one {customer_name} 👌 you're booked in!\n\n"
        f"✂️ {service['label']} with {barber['name']}\n"
        f"📅 {dt.strftime('%a %d %b')}\n"
        f"⏰ {dt.strftime('%I:%M%p')}"
        f"{link}\n\n"
        f"If you need to change or cancel, just message 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))