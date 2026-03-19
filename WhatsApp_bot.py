import os
import dateparser
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)   # ✅ MUST BE GLOBAL (NOT inside function)

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    return "OK"


from llm_helper import llm_extract
from calendar_helper import is_free, create_booking


# --- SIMPLE MEMORY ---
SESSIONS = {}

def whatsapp():
    incoming = request.values.get("Body", "").strip()
    number = request.values.get("From")

    resp = MessagingResponse()
    reply = resp.message()

    user = SESSIONS.get(number, {})

    # --- THANK YOU HANDLING ---
    if "thank" in incoming.lower():
        reply.body("You're welcome! 💈 See you soon 👊🏾")
        return str(resp)

    # --- AI INTENT ---
    data = llm_extract(incoming)
    intent = data.get("intent")

    # --- START BOOKING ---
    if intent == "book":
        user["service"] = data.get("service", "haircut")
        user["time"] = data.get("time")

        if not user["time"]:
            reply.body("What time would you like? ⏰")
            SESSIONS[number] = user
            return str(resp)

        user["stage"] = "awaiting_name"
        reply.body("Nice 👌 what name should I book it under?")
        SESSIONS[number] = user
        return str(resp)

    # --- CAPTURE NAME ---
    if user.get("stage") == "awaiting_name":
        user["name"] = incoming

        time = user.get("time")

        if not is_free(time):
            reply.body("That time is taken 😬 got another time?")
            return str(resp)

        create_booking(
            name=user["name"],
            time=time,
            duration=30
        )

        reply.body(
            f"✅ All set {user['name']} 👌\n"
            f"You're booked in for {time.strftime('%A %H:%M')} 💈"
        )

        SESSIONS.pop(number, None)
        return str(resp)

    # --- TIME FOLLOW-UP ---
    if "tomorrow" in incoming.lower() or ":" in incoming:
        parsed = llm_extract(incoming).get("time")

        if parsed:
            user["time"] = parsed
            user["stage"] = "awaiting_name"
            reply.body("Perfect 👌 what name should I book it under?")
            SESSIONS[number] = user
            return str(resp)

    # --- DEFAULT AI RESPONSE ---
    reply.body("Hi 👋 What can I help you with? 💈")
    return str(resp)
