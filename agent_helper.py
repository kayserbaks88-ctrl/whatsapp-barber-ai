import os
from datetime import datetime, timedelta

import dateparser
from openai import OpenAI

from calendar_helper import (
    BARBERS,
    SERVICES,
    create_booking,
    is_free,
    list_bookings,
    cancel_booking,
    reschedule_booking,
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:

    name = (profile_name or "").strip()
    msg = user_message.lower()

    # =========================
    # 👋 GREETING
    # =========================
    if not session.get("welcomed"):
        session["welcomed"] = True
        return f"Welcome back {name or ''} 👋 What can I get you booked in for today? ✂️"

    # =========================
    # 🧠 MEMORY STORE
    # =========================
    if "data" not in session:
        session["data"] = {}

    data = session["data"]

    # =========================
    # 🔥 INTENT DETECTION
    # =========================
    if any(x in msg for x in ["cancel"]):
        bookings = list_bookings(phone)
        if not bookings:
            return "You’ve got no bookings to cancel 👍"

        cancel_booking(bookings[0]["id"])
        return "All sorted 👍 your booking is cancelled."

    if any(x in msg for x in ["reschedule", "change", "move"]):
        session["reschedule"] = True
        return "No worries 👍 what time would you like instead?"

    # =========================
    # 🧠 MEMORY EXTRACTION
    # =========================
    for key in SERVICES:
        if key in msg:
            data["service"] = key

    # 🔥 extra fallback matching
    if "hair" in msg:
        data["service"] = "haircut"
    if "fade" in msg:
        data["service"] = "skin fade"
    if "beard" in msg:
        data["service"] = "beard trim"
    if "kid" in msg:
        data["service"] = "kids cut"

    for key in BARBERS:
        if key in msg:
            data["barber"] = key

    parsed = dateparser.parse(
        user_message,
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )

    if parsed:
        data["when"] = parsed.isoformat()

    # =========================
    # 🔄 RESCHEDULE FLOW
    # =========================
    if session.get("reschedule") and "when" in data:
        bookings = list_bookings(phone)

        if not bookings:
            return "I couldn’t find a booking to change 😅"

        event_id = bookings[0]["id"]
        new_start = datetime.fromisoformat(data["when"])

        reschedule_booking(event_id, new_start)

        session["reschedule"] = False
        session["data"] = {}

        return f"Done 👍 moved your booking to {new_start.strftime('%A %I:%M %p')}"

    # =========================
    # 🔥 AUTO BOOKING
    # =========================
    if all(k in data for k in ["service", "barber", "when"]):
        try:
            start_dt = datetime.fromisoformat(data["when"])
            minutes = SERVICES[data["service"]]["minutes"]

            if is_free(start_dt, start_dt + timedelta(minutes=minutes), data["barber"]):

                result = create_booking(
                    phone=phone,
                    service_name=data["service"],
                    start_dt=start_dt,
                    minutes=minutes,
                    name=name or "Customer",
                    barber=data["barber"],
                )

                session["data"] = {}

                return (
                    f"Nice one {name or ''} 👌 you're booked in!\n"
                    f"📅 {start_dt.strftime('%A %I:%M %p')}\n"
                    f"✂️ {data['service'].title()} with {data['barber'].title()}\n"
                    f"{result.get('link','')}"
                )

            else:
                return "That time’s taken 😅 want another time?"

        except Exception as e:
            return f"Something went wrong 😅 try again"

    # =========================
    # 🤖 SMART REPLY (NO REPEATS)
    # =========================
    missing = []

    if "service" not in data:
        missing.append("service")
    if "barber" not in data:
        missing.append("barber")
    if "when" not in data:
        missing.append("time")

    if missing == ["time"]:
        return "What time works for you? 📅"

    if missing == ["service"]:
        return "What would you like done? ✂️"

    if missing == ["barber"]:
        return "Which barber would you like? 👍"

    # fallback AI
    return "Just let me know what you'd like 👍"