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
        parsed = dateparser.parse(f"today {text}", settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(TIMEZONE),
        })

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

    elif any(x in text_lower for x in ["change to", "switch to", "instead", "make it", "actually"]):
        result["intent"] = "change_service_smart"

    elif "change service" in text_lower:
        result["intent"] = "change_service"

    elif any(x in text_lower for x in [
        "reschedule", "move", "change time", "change it",
        "another time", "different time", "later", "earlier"
    ]):
        result["intent"] = "reschedule"

    elif any(x in text_lower for x in ["add", "also"]):
        result["intent"] = "upgrade_service"

    elif any(w in text_lower for w in ["book", "appointment"]):
        result["intent"] = "book"

    # barber
    if "jay" in text_lower:
        result["barber"] = "jay"
    elif "mike" in text_lower:
        result["barber"] = "mike"

    # service (strong match)
    if "kids" in text_lower:
        result["service"] = "kids cut"
    elif "skin fade" in text_lower or "fade" in text_lower:
        result["service"] = "skin fade"
    elif "beard" in text_lower:
        result["service"] = "beard trim"
    elif "haircut" in text_lower or "hair cut" in text_lower:
        result["service"] = "haircut"

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

    if text_lower in ["menu", "reset"]:
        SESSIONS.pop(from_number, None)
        msg.body("What would you like to book? ✂️")
        return str(resp)

    # =========================
    # 🔥 NATURAL RESCHEDULE
    # =========================
    if any(x in text_lower for x in ["change", "move", "reschedule"]) and not session.get("reschedule_mode"):
        bookings = list_bookings(from_number)
        if bookings:
            session["reschedule_mode"] = True
            session["reschedule_booking_id"] = bookings[0]["id"]
            SESSIONS[from_number] = session
            msg.body("No worries 👍 what time would you like instead? ⏰")
            return str(resp)

    # =========================
    # CANCEL
    # =========================
    if hard.get("intent") == "cancel":
        bookings = list_bookings(from_number)
        if not bookings:
            msg.body("No bookings to cancel 👍")
            return str(resp)

        cancel_booking(bookings[0]["id"])
        msg.body("Done 👍 booking cancelled.")
        return str(resp)

    # =========================
    # 🔥 UPGRADE SERVICE
    # =========================
    if hard.get("intent") == "upgrade_service":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("No booking to upgrade 👍")
            return str(resp)

        booking = bookings[0]
        new_service = hard.get("service") or data.get("service")

        if not new_service or new_service not in SERVICES:
            msg.body("What would you like to add? ✂️")
            return str(resp)

        dt = datetime.fromisoformat(booking["start"])
        barber = BARBERS.get(booking.get("barber_key"))

        current_minutes = booking.get("minutes", 30)
        extra_minutes = SERVICES[new_service]["minutes"]
        total = current_minutes + extra_minutes

        end_dt = dt + timedelta(minutes=total)

        if not is_free(dt, end_dt, barber):
            msg.body("That would clash 😅 want to move time?")
            return str(resp)

        cancel_booking(booking["id"])

        result = create_booking(
            phone=from_number,
            service_name=f"{booking['service']} + {SERVICES[new_service]['label']}",
            start_dt=dt,
            minutes=total,
            name=profile_name,
            barber=barber,
        )

        msg.body(f"Upgraded 👌\n\n{result.get('link','')}")
        return str(resp)

    # =========================
    # 🔥 CHANGE SERVICE
    # =========================
    if hard.get("intent") == "change_service_smart":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("No booking to change 👍")
            return str(resp)

        booking = bookings[0]
        new_service = hard.get("service") or data.get("service")

        dt = datetime.fromisoformat(booking["start"])
        barber = BARBERS.get(booking.get("barber_key"))

        cancel_booking(booking["id"])

        result = create_booking(
            phone=from_number,
            service_name=SERVICES[new_service]["label"],
            start_dt=dt,
            minutes=SERVICES[new_service]["minutes"],
            name=profile_name,
            barber=barber,
        )

        msg.body(f"Updated 👌\n\n{result.get('link','')}")
        return str(resp)

    # =========================
    # RESCHEDULE TIME
    # =========================
    if session.get("reschedule_mode"):
        dt = parse_when_text(text)

        if not dt:
            msg.body("Try 'tomorrow 3pm'")
            return str(resp)

        link = reschedule_booking(session["reschedule_booking_id"], dt)
        SESSIONS.pop(from_number, None)

        msg.body(f"Done 👌\n\n{link}")
        return str(resp)

    # =========================
    # BOOK FLOW
    # =========================
    service = hard.get("service") or data.get("service")
    barber_key = hard.get("barber") or data.get("barber")
    when_text = hard.get("when_text") or data.get("when_text")

    if not service:
        msg.body("What would you like to book? ✂️")
        return str(resp)

    if not barber_key:
        msg.body("Which barber? (Jay or Mike)")
        return str(resp)

    if not when_text:
        msg.body("When would you like? ⏰")
        return str(resp)

    dt = parse_when_text(when_text)

    service_data = SERVICES[service]
    barber = BARBERS[barber_key]
    end_dt = dt + timedelta(minutes=service_data["minutes"])

    if not is_free(dt, end_dt, barber):
        msg.body("That time is taken 😅")
        return str(resp)

    result = create_booking(
        phone=from_number,
        service_name=service_data["label"],
        start_dt=dt,
        minutes=service_data["minutes"],
        name=profile_name,
        barber=barber,
    )

    msg.body(f"Booked 👌\n\n{result.get('link','')}")
    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))