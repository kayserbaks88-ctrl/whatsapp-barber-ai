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

    # fallback for simple time-only inputs like "2pm"
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

    if "kids" in text_lower or "kid" in text_lower:
        return "kids cut"
    if "skin fade" in text_lower or "fade" in text_lower:
        return "skin fade"
    if "beard trim" in text_lower or "beard" in text_lower:
        return "beard trim"
    if "haircut" in text_lower or "hair cut" in text_lower or text_lower.strip() == "cut":
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

    # intent
    if "cancel" in text_lower:
        result["intent"] = "cancel"

    elif any(x in text_lower for x in [
        "add beard",
        "add trim",
        "also get",
        "also have",
        "add on",
        "add beard trim",
    ]):
        result["intent"] = "upgrade_service"

    elif any(x in text_lower for x in [
        "change to",
        "switch to",
        "make it",
        "instead",
        "actually",
    ]):
        result["intent"] = "change_service_smart"

    elif "change service" in text_lower or "different service" in text_lower:
        result["intent"] = "change_service"

    elif any(x in text_lower for x in [
        "reschedule",
        "change time",
        "different time",
        "another time",
        "move it",
        "move appointment",
        "move booking",
    ]):
        result["intent"] = "reschedule"

    elif any(x in text_lower for x in ["book", "appointment"]):
        result["intent"] = "book"

    # barber
    if "jay" in text_lower:
        result["barber"] = "jay"
    elif "mike" in text_lower:
        result["barber"] = "mike"

    # service
    service = detect_service(text)
    if service:
        result["service"] = service

    # time
    if parse_when_text(text):
        result["when_text"] = text

    return result


def get_booking_start(booking):
    start_raw = booking.get("start") or booking.get("start_dt")
    if not start_raw:
        return None

    try:
        return datetime.fromisoformat(start_raw)
    except Exception:
        return None


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From", "").strip()
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest").strip() or "Guest"

    resp = MessagingResponse()
    msg = resp.message()

    session = SESSIONS.get(from_number, {})
    text_lower = text.lower()

    hard = apply_hard_rules(text)
    data = llm_extract(text) or {}

    # =========================
    # SIMPLE CHAT
    # =========================
    if text_lower in ["hi", "hello", "hey"]:
        msg.body("Hey 👋 What can I book for you today? ✂️")
        return str(resp)

    if "thank" in text_lower or "cheers" in text_lower:
        msg.body("You're welcome 😊")
        return str(resp)

    if text_lower in ["bye", "see you", "see you soon"]:
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
    # QUICK TIME CAPTURE
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

    # backup catch for natural messages like "can I change please"
    if (
        any(x in text_lower for x in ["change", "move", "reschedule"])
        and not session.get("reschedule_mode")
        and not (hard.get("service") or data.get("service"))
    ):
       bookings = list_bookings(from_number)

       if bookings:
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

        result = reschedule_booking(session["reschedule_booking_id"], dt)

        SESSIONS.pop(from_number, None)

        if not result:
            msg.body("That time is taken 😅 try another")
            return str(resp)

        link = ""
        if isinstance(result, str):
            link = result
        elif isinstance(result, dict):
            link = result.get("link", "")

        link_line = f"\n\n🔗 {link}" if link else ""

        msg.body(
            f"All sorted 👌\n\n"
            f"📅 {dt.strftime('%a %d %b')}\n"
            f"⏰ {dt.strftime('%I:%M%p')}"
            f"{link_line}"
        )
        return str(resp)

    # =========================
    # CHANGE SERVICE SMART (MOVED UP)
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

        original_dt = get_booking_start(booking)
        if not original_dt:
            msg.body("I couldn’t read your current booking time 😅")
            return str(resp)

        barber_key = booking.get("barber_key")
        barber = BARBERS.get(barber_key)
        if not barber:
            msg.body("I couldn’t find the barber for that booking 😅")
            return str(resp)

        service = SERVICES[new_service]
        new_end = original_dt + timedelta(minutes=service["minutes"])

        cancelled = cancel_booking(booking["id"])
        if not cancelled:
            msg.body("I couldn’t update that booking right now 😅 try again")
            return str(resp)

        if not is_free(original_dt, new_end, barber):
            msg.body("That service change would clash 😅 try changing the time too")
            return str(resp)

        result = create_booking(
            phone=from_number,
            service_name=service["label"],
            start_dt=original_dt,
            minutes=service["minutes"],
            name=session.get("name", profile_name),
            barber=barber,
        )

        session.pop("reschedule_mode", None)
        SESSIONS.pop(from_number, None)

        link = result.get("link", "")
        link_line = f"\n\n🔗 {link}" if link else ""

        msg.body(
            f"Done 👌 I’ve changed it for you.\n\n"
            f"✂️ {service['label']} with {barber['name']}\n"
            f"📅 {original_dt.strftime('%a %d %b')}\n"
            f"⏰ {original_dt.strftime('%I:%M%p')}"
            f"{link_line}\n\n"
            f"Anything else, just message 👍"
        )
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
    # BACKUP RESCHEDULE
    # =========================
    if (
        any(x in text_lower for x in ["change", "move", "reschedule"])
        and not session.get("reschedule_mode")
        and not (hard.get("service") or data.get("service"))
    ):
        bookings = list_bookings(from_number)

        if bookings:
            session["reschedule_mode"] = True
            session["reschedule_booking_id"] = bookings[0]["id"]
            SESSIONS[from_number] = session

            msg.body("No worries 👍 what time would you like instead? ⏰")
            return str(resp)


    # =========================
    # RESCHEDULE MODE (FIXED)
    # =========================
    if session.get("reschedule_mode"):

        # 🔥 IMPORTANT FIX — detect service change during reschedule
        new_service = hard.get("service") or data.get("service")

    if new_service:
        session.pop("reschedule_mode", None)
        SESSIONS[from_number] = session
    else:
        when_text = session.get("when_text", text)
        dt = parse_when_text(when_text)

        if not dt:
            msg.body("Didn’t catch that time 🤔 try 'tomorrow 3pm'")
            return str(resp)

        result = reschedule_booking(session["reschedule_booking_id"], dt)

        SESSIONS.pop(from_number, None)

        if not result:
            msg.body("That time is taken 😅 try another")
            return str(resp)

        link = result.get("link", "") if isinstance(result, dict) else result
        link_line = f"\n\n🔗 {link}" if link else ""

        msg.body(
            f"All sorted 👌\n\n"
            f"📅 {dt.strftime('%a %d %b')}\n"
            f"⏰ {dt.strftime('%I:%M%p')}"
            f"{link_line}"
        )
        return str(resp)

    # =========================
    # BASIC CHANGE SERVICE
    # =========================
    if hard.get("intent") == "change_service":
        session.pop("service", None)
        session.pop("when_text", None)
        SESSIONS[from_number] = session

        msg.body("No problem 👍 what would you like instead? ✂️")
        return str(resp)

    # =========================
    # UPGRADE SERVICE
    # haircut -> haircut + beard trim
    # =========================
    if hard.get("intent") == "upgrade_service":
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You don’t have a booking to upgrade 👍")
            return str(resp)

        booking = bookings[0]
        extra_service_key = hard.get("service") or data.get("service")

        if not extra_service_key or extra_service_key not in SERVICES:
            msg.body("What would you like to add? ✂️")
            return str(resp)

        original_dt = get_booking_start(booking)
        if not original_dt:
            msg.body("I couldn’t read your current booking time 😅")
            return str(resp)

        barber_key = booking.get("barber_key")
        barber = BARBERS.get(barber_key)
        if not barber:
            msg.body("I couldn’t find the barber for that booking 😅")
            return str(resp)

        current_minutes = booking.get("minutes", 30)
        extra_minutes = SERVICES[extra_service_key]["minutes"]
        total_minutes = current_minutes + extra_minutes
        new_end = original_dt + timedelta(minutes=total_minutes)

        if not is_free(original_dt, new_end, barber):
            msg.body("Adding that would clash 😅 you may need a different time")
            return str(resp)

        cancelled = cancel_booking(booking["id"])
        if not cancelled:
            msg.body("I couldn’t update that booking right now 😅 try again")
            return str(resp)

        current_service_name = booking.get("service", "Appointment")
        extra_service_name = SERVICES[extra_service_key]["label"]
        combined_service_name = f"{current_service_name} + {extra_service_name}"

        result = create_booking(
            phone=from_number,
            service_name=combined_service_name,
            start_dt=original_dt,
            minutes=total_minutes,
            name=session.get("name", profile_name),
            barber=barber,
        )

        SESSIONS.pop(from_number, None)

        link = result.get("link", "")
        link_line = f"\n\n🔗 {link}" if link else ""

        msg.body(
            f"Nice upgrade 👌\n\n"
            f"✂️ {combined_service_name} with {barber['name']}\n"
            f"📅 {original_dt.strftime('%a %d %b')}\n"
            f"⏰ {original_dt.strftime('%I:%M%p')}"
            f"{link_line}"
        )
        return str(resp)

    # =========================
    # AI + HARD RULES INTO SESSION
    # =========================
    if hard.get("service"):
        session["service"] = hard["service"]
    elif data.get("service") in SERVICES:
        session["service"] = data["service"]

    barber_value = hard.get("barber") or data.get("barber")
    if barber_value and barber_value.lower() in BARBERS:
        session["barber"] = barber_value.lower()

    when_value = hard.get("when_text") or data.get("when_text")
    if when_value:
        session["when_text"] = when_value

    session["name"] = data.get("name") or session.get("name") or profile_name

    # =========================
    # BOOKING FLOW
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
    link_line = f"\n\n🔗 {link}" if link else ""

    msg.body(
        f"Nice one {customer_name} 👌 you're booked in!\n\n"
        f"✂️ {service['label']} with {barber['name']}\n"
        f"📅 {dt.strftime('%a %d %b')}\n"
        f"⏰ {dt.strftime('%I:%M%p')}"
        f"{link_line}\n\n"
        f"If you need to change or cancel, just message 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))