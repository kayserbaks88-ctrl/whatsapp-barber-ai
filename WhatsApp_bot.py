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


def detect_service(text: str):
    text_lower = (text or "").lower()

    if "kids" in text_lower:
        return "kids cut"
    if "skin fade" in text_lower or "fade" in text_lower:
        return "skin fade"
    if "beard" in text_lower:
        return "beard trim"
    if "haircut" in text_lower or "cut" in text_lower:
        return "haircut"

    return None


def apply_hard_rules(text: str):
    text_lower = (text or "").lower().strip()

    result = {
        "intent": None,
        "service": None,
        "barber": None,
        "when_text": None,
    }

    if "cancel" in text_lower:
        result["intent"] = "cancel"

    elif any(x in text_lower for x in ["add", "also"]):
        result["intent"] = "upgrade_service"

    elif any(x in text_lower for x in ["change to", "switch to", "make it", "instead", "actually"]):
        result["intent"] = "change_service_smart"

    elif any(x in text_lower for x in [
        "reschedule", "change time", "move", "another time"
    ]):
        result["intent"] = "reschedule"

    service = detect_service(text)
    if service:
        result["service"] = service

    if parse_when_text(text):
        result["when_text"] = text

    if "jay" in text_lower:
        result["barber"] = "jay"
    elif "mike" in text_lower:
        result["barber"] = "mike"

    return result


def get_booking_start(booking):
    try:
        return datetime.fromisoformat(booking["start"])
    except:
        return None


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From", "").strip()
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    session = SESSIONS.get(from_number, {})
    text_lower = text.lower()

    hard = apply_hard_rules(text)
    data = llm_extract(text) or {}

    # =========================
    # BASIC CHAT
    # =========================
    if text_lower in ["hi", "hello"]:
        msg.body("Hey 👋 What can I book for you?")
        return str(resp)

    if text_lower in ["menu", "reset"]:
        SESSIONS.pop(from_number, None)
        msg.body("What would you like to book? ✂️")
        return str(resp)

    # =========================
    # CANCEL
    # =========================
    if hard.get("intent") == "cancel":
        bookings = list_bookings(from_number)
        if not bookings:
            msg.body("No bookings found 👍")
            return str(resp)

        cancel_booking(bookings[0]["id"])
        msg.body("Cancelled 👍")
        return str(resp)

    # =========================
    # 🔥 CHANGE SERVICE (FIRST)
    # =========================
    if hard.get("intent") == "change_service_smart":

        bookings = list_bookings(from_number)
        if not bookings:
            msg.body("No booking to change 👍")
            return str(resp)

        booking = bookings[0]
        new_service = hard.get("service") or data.get("service")

        if not new_service:
            msg.body("What would you like instead?")
            return str(resp)

        dt = get_booking_start(booking)
        barber = BARBERS.get(booking.get("barber_key"))

        service = SERVICES[new_service]
        end_dt = dt + timedelta(minutes=service["minutes"])

        if not is_free(dt, end_dt, barber):
            msg.body("That won’t fit 😅 try different time")
            return str(resp)

        cancel_booking(booking["id"])

        result = create_booking(
            phone=from_number,
            service_name=service["label"],
            start_dt=dt,
            minutes=service["minutes"],
            name=profile_name,
            barber=barber,
        )

        SESSIONS.pop(from_number, None)

        msg.body(f"Done 👌 {service['label']} booked\n\n{result.get('link','')}")
        return str(resp)

    # =========================
    # RESCHEDULE START
    # =========================
    if hard.get("intent") == "reschedule":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("No bookings to change 👍")
            return str(resp)

        session["reschedule_mode"] = True
        session["reschedule_booking_id"] = bookings[0]["id"]
        SESSIONS[from_number] = session

        msg.body("What time would you like instead? ⏰")
        return str(resp)

    # =========================
    # RESCHEDULE MODE
    # =========================
    if session.get("reschedule_mode"):

        new_service = hard.get("service") or data.get("service")

        if new_service:
            session.pop("reschedule_mode", None)
            hard["intent"] = "change_service_smart"

        else:
            dt = parse_when_text(text)

            if not dt:
                msg.body("Try 'tomorrow 3pm'")
                return str(resp)

            reschedule_booking(session["reschedule_booking_id"], dt)
            SESSIONS.pop(from_number, None)

            msg.body(f"Updated 👌 {dt.strftime('%a %I:%M%p')}")
            return str(resp)

    # =========================
    # BOOKING FLOW
    # =========================
    if hard.get("service"):
        session["service"] = hard["service"]

    if hard.get("barber"):
        session["barber"] = hard["barber"]

    if hard.get("when_text"):
        session["when_text"] = hard["when_text"]

    if "service" not in session:
        msg.body("What would you like to book?")
        return str(resp)

    if "barber" not in session:
        msg.body("Which barber? Jay or Mike")
        return str(resp)

    if "when_text" not in session:
        msg.body("When would you like?")
        return str(resp)

    dt = parse_when_text(session["when_text"])

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]

    end_dt = dt + timedelta(minutes=service["minutes"])

    if not is_free(dt, end_dt, barber):
        msg.body("Slot taken 😅")
        return str(resp)

    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        start_dt=dt,
        minutes=service["minutes"],
        name=profile_name,
        barber=barber,
    )

    SESSIONS.pop(from_number, None)

    msg.body(f"Booked 👌\n\n{result.get('link','')}")
    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))