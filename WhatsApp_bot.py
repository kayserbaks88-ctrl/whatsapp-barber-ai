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


# =========================
# TIME PARSER (FIXED)
# =========================
def parse_when_text(text: str):
    if not text:
        return None

    dt = dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(TIMEZONE),
        },
    )

    if not dt and any(x in text.lower() for x in ["am", "pm"]):
        dt = dateparser.parse(
            f"today {text}",
            settings={
                "TIMEZONE": str(TIMEZONE),
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.now(TIMEZONE),
            },
        )

    return dt


# =========================
# SERVICE DETECTION
# =========================
def detect_service(text):
    t = text.lower()

    if "kid" in t:
        return "kids cut"
    if "fade" in t:
        return "skin fade"
    if "beard" in t:
        return "beard trim"
    if "hair" in t or t.strip() == "cut":
        return "haircut"

    return None


# =========================
# MAIN ROUTE
# =========================
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    session = SESSIONS.get(from_number, {})
    data = llm_extract(text)

    # =========================
    # HUMAN CHAT
    # =========================
    if text.lower() in ["hi", "hello", "hey"]:
        msg.body("Hey 👋 What can I book for you today? ✂️")
        return str(resp)

    if "thank" in text.lower():
        msg.body("You're welcome 😊")
        return str(resp)

    # =========================
    # GET USER BOOKINGS
    # =========================
    bookings = list_bookings(from_number)

    # =========================
    # CANCEL
    # =========================
    if data.get("intent") == "cancel":
        if not bookings:
            msg.body("You’ve got no bookings 👍")
            return str(resp)

        cancel_booking(bookings[0]["id"])
        SESSIONS.pop(from_number, None)

        msg.body("Done 👍 cancelled.")
        return str(resp)

    # =========================
    # CHANGE SERVICE (SMART)
    # =========================
    if data.get("intent") == "change_service_smart":
        if not bookings:
            msg.body("No booking found 👍")
            return str(resp)

        new_service = data.get("service")

        if new_service not in SERVICES:
            msg.body("What would you like instead? ✂️")
            return str(resp)

        booking = bookings[0]

        start = booking["start"]
        barber = BARBERS[booking["barber"]]

        cancel_booking(booking["id"])

        result = create_booking(
            phone=from_number,
            service_name=SERVICES[new_service]["label"],
            start_dt=start,
            minutes=SERVICES[new_service]["minutes"],
            name=name,
            barber=barber,
        )

        msg.body(f"Done 👌 changed to {SERVICES[new_service]['label']}")
        return str(resp)

    # =========================
    # ADD SERVICE
    # =========================
    if "add" in text.lower():
        if not bookings:
            msg.body("No booking found 👍")
            return str(resp)

        booking = bookings[0]
        extra = detect_service(text)

        if not extra:
            msg.body("What would you like to add? ✂️")
            return str(resp)

        start = booking["start"]
        barber = BARBERS[booking["barber"]]

        total_minutes = booking["minutes"] + SERVICES[extra]["minutes"]

        cancel_booking(booking["id"])

        result = create_booking(
            phone=from_number,
            service_name=f"{booking['service']} + {SERVICES[extra]['label']}",
            start_dt=start,
            minutes=total_minutes,
            name=name,
            barber=barber,
        )

        msg.body("Nice upgrade 👌 added successfully")
        return str(resp)

    # =========================
    # RESCHEDULE
    # =========================
    if data.get("intent") == "reschedule":
        if not bookings:
            msg.body("No booking to change 👍")
            return str(resp)

        session["reschedule"] = bookings[0]["id"]
        SESSIONS[from_number] = session

        msg.body("What time would you like? ⏰")
        return str(resp)

    if session.get("reschedule"):
        dt = parse_when_text(text)

        if not dt:
            msg.body("Try 'tomorrow 3pm'")
            return str(resp)

        link = reschedule_booking(session["reschedule"], dt)

        SESSIONS.pop(from_number, None)

        msg.body(f"Updated 👌\n{dt.strftime('%A %I:%M%p')}")
        return str(resp)

    # =========================
    # BOOKING FLOW
    # =========================
    if data.get("service"):
        session["service"] = data["service"]

    if data.get("barber"):
        session["barber"] = data["barber"]

    if data.get("when_text"):
        session["when"] = data["when_text"]

    if "service" not in session:
        msg.body("What would you like? ✂️")
        return str(resp)

    if "barber" not in session:
        msg.body("Jay or Mike?")
        return str(resp)

    if "when" not in session:
        msg.body("When?")
        return str(resp)

    dt = parse_when_text(session["when"])

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]

    if not is_free(dt, dt + timedelta(minutes=service["minutes"]), barber):
        msg.body("That slot is taken 😅")
        return str(resp)

    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        start_dt=dt,
        minutes=service["minutes"],
        name=name,
        barber=barber,
    )

    SESSIONS.pop(from_number, None)

    msg.body(
        f"Nice one {name} 👌\n"
        f"{service['label']} with {barber['name']}\n"
        f"{dt.strftime('%A %I:%M%p')}"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))