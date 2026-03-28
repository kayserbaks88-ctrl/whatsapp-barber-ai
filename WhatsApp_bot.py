import os
import random
from datetime import timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import is_free, create_booking, BARBERS

app = Flask(__name__)

TIMEZONE = ZoneInfo("Europe/London")

SESSIONS = {}

SERVICES = {
    "haircut": {"label": "Haircut", "minutes": 30},
    "beard trim": {"label": "Beard Trim", "minutes": 20},
    "skin fade": {"label": "Skin Fade", "minutes": 45},
}

# =========================
# HUMAN RESPONSES
# =========================
def ask_service():
    return random.choice([
        "Nice 👌 what are you looking to get done?",
        "What can I sort you out with?",
        "Cool 👍 what would you like today?"
    ])

def ask_barber():
    return random.choice([
        "Got you 👍 any preference? Jay or Mike?",
        "Who would you like — Jay or Mike?",
        "Want Jay or Mike for this one?"
    ])

def ask_time():
    return random.choice([
        "What time suits you?",
        "When would you like to come in?",
        "What time works best for you?"
    ])

def slot_taken():
    return random.choice([
        "Ah that time’s gone 😅 try another one?",
        "That slot’s taken unfortunately — got another time?",
        "Someone grabbed that one 😬 what else works?"
    ])

def get_session(phone):
    if phone not in SESSIONS:
        SESSIONS[phone] = {}
    return SESSIONS[phone]

# =========================
# FALLBACK EXTRACTION (VERY IMPORTANT)
# =========================
def extract_fallback(text):
    text_lower = text.lower()

    data = {}

    if "haircut" in text_lower:
        data["service"] = "haircut"
    elif "beard" in text_lower:
        data["service"] = "beard trim"
    elif "fade" in text_lower:
        data["service"] = "skin fade"

    if "jay" in text_lower:
        data["barber"] = "jay"
    elif "mike" in text_lower:
        data["barber"] = "mike"

    if any(x in text_lower for x in ["tomorrow", "today", "am", "pm", ":"]):
        data["when_text"] = text

    return data

# =========================
# MAIN ROUTE
# =========================
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From")
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest")

    resp = MessagingResponse()
    msg = resp.message()

    text_lower = text.lower()
    session = get_session(from_number)

    # =========================
    # HUMAN SMALL TALK
    # =========================
    if text_lower in ["hi", "hello", "hey"]:
        msg.body("Hey 👋 what can I book for you?")
        return str(resp)

    if text_lower in ["thanks", "thank you", "cheers"]:
        msg.body("You’re welcome 😊 just message me anytime.")
        return str(resp)

    if text_lower in ["bye", "later", "see you"]:
        msg.body("See you soon 👋")
        return str(resp)

    # =========================
    # AI + FALLBACK EXTRACTION
    # =========================
    try:
        data = llm_extract(text)
        if not isinstance(data, dict):
            data = {}
    except:
        data = {}

    fallback = extract_fallback(text)

    service_val = data.get("service") or fallback.get("service")
    barber_val = data.get("barber") or fallback.get("barber")
    when_val = data.get("when_text") or fallback.get("when_text")

    # =========================
    # SAVE INTO SESSION
    # =========================
    if service_val:
        session["service"] = service_val

    if barber_val:
        session["barber"] = barber_val

    if when_val:
        session["when_text"] = when_val

    session["name"] = session.get("name", profile_name)

    # =========================
    # ASK MISSING INFO (HUMAN STYLE)
    # =========================
    if "service" not in session:
        msg.body(ask_service())
        return str(resp)

    if session["service"] not in SERVICES:
        msg.body("I can do haircut, skin fade or beard trim 👍")
        return str(resp)

    if "barber" not in session or session["barber"] not in BARBERS:
        msg.body(ask_barber())
        return str(resp)

    if "when_text" not in session:
        msg.body(ask_time())
        return str(resp)

    # =========================
    # PARSE TIME
    # =========================
    dt = dateparser.parse(
        session["when_text"],
        settings={
            "TIMEZONE": "Europe/London",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )

    if not dt:
        msg.body("Didn’t quite catch that time 🤔 try like 'tomorrow 3pm'")
        return str(resp)

    service = SERVICES[session["service"]]
    barber = BARBERS[session["barber"]]

    end_dt = dt + timedelta(minutes=service["minutes"])

    # =========================
    # CHECK SLOT
    # =========================
    if not is_free(dt, end_dt, barber):
        msg.body(slot_taken())
        return str(resp)

    # =========================
    # CREATE BOOKING
    # =========================
    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        start_dt=dt,
        minutes=service["minutes"],
        name=session["name"],
        barber=barber,
    )

    SESSIONS.pop(from_number, None)

    # =========================
    # HUMAN CONFIRMATION
    # =========================
    msg.body(
        f"Nice one 👌 you're booked in!\n\n"
        f"{service['label']} with {barber['name']}\n"
        f"{dt.strftime('%a %d %b at %I:%M%p')}\n\n"
        f"{result.get('link', '')}\n\n"
        f"Anything else just message 👍"
    )

    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))