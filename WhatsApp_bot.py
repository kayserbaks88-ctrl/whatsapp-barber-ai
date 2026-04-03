import json
import os
from datetime import datetime, timedelta
from typing import Any

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

    # 👋 FIRST TIME GREETING
    if not session.get("welcomed"):
        session["welcomed"] = True
        if customer_name:
            return f"Welcome back {customer_name} 👋 What can I get you booked in for today? ✂️"
        return "Hey 👋 What can I get you booked in for today? ✂️"

    # 🔥 MEMORY STORE
    if "data" not in session:
        session["data"] = {}

    data = session["data"]
    msg = user_message.lower()

    # detect service
    for key in SERVICES:
        if key in msg:
            data["service"] = key

    # detect barber
    for key in BARBERS:
        if key in msg:
            data["barber"] = key

    # detect time
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

    # 🔥 AUTO BOOKING (THE MAGIC)
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

                session["data"] = {}

                link = result.get("link", "")
                return (
                    f"Nice one {customer_name or ''} 👌 you're booked in!\n"
                    f"📅 {start_dt.strftime('%A %I:%M %p')}\n"
                    f"✂️ {data['service'].title()} with {data['barber'].title()}\n"
                    f"{link}"
                )
            else:
                return "That time’s taken 😅 want another time?"

        except Exception:
            return "Something went wrong booking that — try again 👍"

    # 🤖 AI fallback (only for missing info)
    instructions = f"""
You are a friendly WhatsApp receptionist for {business_name}.

Rules:
- Be natural and human
- Keep it short
- Use light emojis
- NEVER ask for info already given
- If user already gave service, barber or time → don't ask again
- Combine messages naturally

Conversation so far:
Service: {data.get("service")}
Barber: {data.get("barber")}
Time: {data.get("when")}
"""

    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=user_message,
    )

    text = (response.output_text or "").strip()
    if text:
        return text

    return "Tell me what you'd like to book 👍"