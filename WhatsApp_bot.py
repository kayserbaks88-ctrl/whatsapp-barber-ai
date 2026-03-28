from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import dateparser

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
TIMEZONE = ZoneInfo("Europe/London")

SESSIONS = {}

SERVICES = {
    "haircut": {"label": "Haircut", "minutes": 30},
    "beard trim": {"label": "Beard Trim", "minutes": 20},
    "kids cut": {"label": "Kids Cut", "minutes": 20},
}


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    text_lower = text.lower()
    session = SESSIONS.get(from_number, {})

    # =========================
    # HUMAN CHAT
    # =========================
    if text_lower in ["hi", "hello", "hey", "yo"]:
        msg.body("Hey 👋 what can I book for you?")
        return str(resp)

    if any(w in text_lower for w in ["thanks", "thank you", "cheers"]):
        msg.body("You're welcome 😊 just message anytime 👍")
        return str(resp)

    # =========================
    # VIEW BOOKINGS
    # =========================
    if "booking" in text_lower:
        bookings = list_bookings(from_number)

        if not bookings:
            msg.body("You’ve got no bookings 👍")
            return str(resp)

        out = "Here’s your bookings:\n\n"
        for i, b in enumerate(bookings, 1):
            out += f"{i}. {b['summary']} at {b['start']}\n"

        out += "\nReply 'cancel 1' or 'reschedule 1 tomorrow 4pm'"
        msg.body(out)
        return str(resp)

    # =========================
    # CANCEL
    # =========================
    if text_lower.startswith("cancel"):
        bookings = list_bookings(from_number)
        parts = text_lower.split()

        if len(parts) < 2:
            msg.body("Which booking do you want to cancel?")
            return str(resp)

        idx = int(parts[1]) - 1

        if idx >= len(bookings):
            msg.body("That booking doesn’t exist 😅")
            return str(resp)

        cancel_booking(bookings[idx]["id"])
        msg.body("Done 👍 your booking is cancelled")
        return str(resp)

    # =========================
    # START RESCHEDULE
    # =========================
    
    if "change" in text_lower or "reschedule" in text_lower or "move" in text_lower:
        bookings = list_bookings(from_number)

    if not bookings:
        msg.body("You've got no bookings to change 👍")
        return str(resp)

    session["reschedule_mode"] = True
    session["reschedule_booking_id"] = bookings[0]["id"]

    SESSIONS[from_number] = session

    msg.body("No worries 👍 what time would you like instead?")
    return str(resp)
        

    # =========================
    # HANDLE RESCHEDULE
    # =========================
    if session.get("reschedule_mode"):
        dt = dateparser.parse(
            text,
            settings={
                "TIMEZONE": "Europe/London",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.now(TIMEZONE),
            },
        )

        if not dt:
            msg.body("Didn’t catch that time 🤔 try again")
            return str(resp)

        booking_id = session.get("reschedule_booking_id")

        link = reschedule_booking(booking_id, dt, 30)

        session.clear()
        SESSIONS[from_number] = session

        msg.body(
            f"All sorted 👌 moved to:\n\n"
            f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
            f"{link}"
        )
        return str(resp)

    # =========================
    # AI EXTRACTION
    # =========================
    data = llm_extract(text)

    service_text = data.get("service")

    if service_text:
        service_text = service_text.lower()

        if "kid" in service_text:
            session["service"] = "kids cut"
        elif "beard" in service_text:
            session["service"] = "beard trim"
        elif "hair" in service_text:
            session["service"] = "haircut"

    if data.get("barber"):
        session["barber"] = data["barber"]

    if data.get("when_text"):
        session["when_text"] = data["when_text"]

    if data.get("name"):
        session["name"] = data["name"]
    else:
        session["name"] = session.get("name", profile_name)

    # =========================
    # FLOW
    # =========================
    if "service" not in session:
        msg.body("What would you like to book? ✂️")
        SESSIONS[from_number] = session
        return str(resp)

    if session["service"] not in SERVICES:
        msg.body("I can do haircut, beard trim or kids cut 👍")
        return str(resp)

    if "barber" not in session:
        msg.body("Which barber would you like? (Jay or Mike)")
        SESSIONS[from_number] = session
        return str(resp)

    if "when_text" not in session:
        msg.body("When would you like to come in?")
        SESSIONS[from_number] = session
        return str(resp)

    dt = dateparser.parse(
        session["when_text"],
        settings={
            "TIMEZONE": "Europe/London",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(TIMEZONE),
        },
    )

    if not dt:
        msg.body("I didn’t catch that time 🤔 try 'tomorrow 3pm'")
        return str(resp)

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]

    end_dt = dt + timedelta(minutes=service["minutes"])

    if not is_free(dt, end_dt, barber):
        msg.body("That slot is taken 😅 try another time")
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

    msg.body(
        f"Nice one {session['name']} 👌 you're booked in!\n\n"
        f"{service['label']} with {barber['name']}\n"
        f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
        f"{result.get('link','')}\n\n"
        f"If you need to change or cancel, just message 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))