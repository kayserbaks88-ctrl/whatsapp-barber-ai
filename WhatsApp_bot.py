import os
from zoneinfo import ZoneInfo

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from agent_helper import run_receptionist_agent

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "TrimTech AI")

# Simple memory per WhatsApp number
SESSIONS: dict[str, dict] = {}


def get_session(phone: str) -> dict:
    if phone not in SESSIONS:
        SESSIONS[phone] = {
            "history": [],
            "profile_name": None,
        }
    return SESSIONS[phone]


@app.route("/health", methods=["GET"])
def health():
    return {"ok": True, "service": BUSINESS_NAME}, 200


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming_msg = (request.form.get("Body") or "").strip()
    from_number = (request.form.get("From") or "").replace("whatsapp:", "").strip()
    profile_name = (request.form.get("ProfileName") or "").strip()

    session = get_session(from_number)
    if profile_name:
        session["profile_name"] = profile_name

    if not incoming_msg:
        twiml = MessagingResponse()
        twiml.message("Hey 👋 send me a message and I’ll help with your booking.")
        return str(twiml)

    reply = run_receptionist_agent(
        user_message=incoming_msg,
        phone=from_number,
        profile_name=session.get("profile_name"),
        session=session,
        business_name=BUSINESS_NAME,
        timezone_name=str(TIMEZONE),
    )

    # Keep a small rolling history
    session["history"].append({"role": "user", "content": incoming_msg})
    session["history"].append({"role": "assistant", "content": reply})
    session["history"] = session["history"][-20:]

    twiml = MessagingResponse()
    twiml.message(reply)
    return str(twiml)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))