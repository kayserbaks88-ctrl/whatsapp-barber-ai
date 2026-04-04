import os
from datetime import datetime, timedelta

import dateparser
from openai import OpenAI

from calendar_helper import (
    BARBERS,
    SERVICES,
    create_booking,
    is_free,
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

    customer_name = (profile_name or "").strip()

    # 👋 FIRST MESSAGE
    if not session.get("welcomed"):
        session["welcomed"] = True
        if customer_name:
            return f"Welcome back {customer_name} 👋 What can I get you booked in for today? ✂️"
        return "Hey 👋 What can I get you booked in for today? ✂️"

    # 🧠 MEMORY
    if "data" not in session:
        session["data"] = {}

    data = session["data"]
    msg = user_message.lower().strip()

    # 💬 HUMAN REPLIES (important)
    if msg in ["thanks", "thank you", "cheers", "nice one", "ok", "okay", "cool"]:
        return "You're welcome 😊 Just message anytime if you need anything 👍"

    # 🔥 SERVICE DETECTION
    if "haircut" in msg:
        data["service"] = "haircut"
    elif "beard" in msg:
        data["service"] = "beard trim"
    elif "fade" in msg:
        data["service"] = "skin fade"
    elif "kid" in msg:
        data["service"] = "kids cut"

    # 🔥 BARBER DETECTION
    if "jay" in msg:
        data["barber"] = "jay"
    elif "mike" in msg:
        data["barber"] = "mike"

    # 🔥 TIME DETECTION
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

    # 🔥 AUTO BOOKING
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
                    name=customer_name or "Customer",
                    barber=data["barber"],
                )

                # 🧹 clear memory after booking
                session["data"] = {}

                link = result.get("link", "")

                # 🧠 FRIENDLY DATE DISPLAY
                today = datetime.now(start_dt.tzinfo).date()
                booking_day = start_dt.date()

                if booking_day == today + timedelta(days=1):
                    day_text = "Tomorrow"
                elif booking_day == today:
                    day_text = "Today"
                else:
                    day_text = start_dt.strftime("%A")

                time_text = start_dt.strftime("%I:%M %p")

                return (
                    f"Nice one {customer_name or ''} 👌 you're booked in!\n\n"
                    f"📅 {day_text} {time_text}\n"
                    f"✂️ {data['service'].title()} with {data['barber'].title()}\n\n"
                    f"📲 View booking:\n{link}"
                )
            else:
                return "That time’s taken 😅 want another time?"

        except Exception as e:
            print("BOOK ERROR:", e)
            return "Something went wrong booking that — try again 👍"

    # 🔥 SMART FLOW (NO REPEATS)

    if "service" not in data:
        return "What would you like to book? ✂️"

    if "barber" not in data:
        return "Who would you like — Jay or Mike? 💈"

    if "when" not in data:
        return "What day and time works for you? 📅"

    return "Tell me what you'd like to book 👍"