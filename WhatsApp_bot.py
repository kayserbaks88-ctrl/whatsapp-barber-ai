import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import (
    create_booking,
    list_upcoming,
    cancel_booking,
)

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "TrimTech AI")

SERVICES = {
    "haircut": {"label": "Haircut", "duration": 30, "price": 18},
    "skin fade": {"label": "Skin Fade", "duration": 45, "price": 22},
    "beard trim": {"label": "Beard Trim", "duration": 20, "price": 12},
    "shape up": {"label": "Shape Up", "duration": 20, "price": 10},
    "kids cut": {"label": "Kids Cut", "duration": 30, "price": 15},
}

BARBERS = {
    "mike": {
        "name": "Mike",
        "calendar_id": os.getenv("BARBER_MIKE_CALENDAR_ID", "").strip(),
    },
    "jay": {
        "name": "Jay",
        "calendar_id": os.getenv("BARBER_JAY_CALENDAR_ID", "").strip(),
    },
}

SESSIONS: dict[str, dict] = {}


def get_session(phone: str) -> dict:
    if phone not in SESSIONS:
        SESSIONS[phone] = {
            "state": None,
            "service": None,
            "barber": None,
            "time_text": None,
            "time_dt": None,
            "customer_name": None,
            "cancel_candidates": [],
            "reschedule_candidates": [],
            "selected_event": None,
        }
    return SESSIONS[phone]


def reset_session(phone: str) -> None:
    SESSIONS[phone] = {
        "state": None,
        "service": None,
        "barber": None,
        "time_text": None,
        "time_dt": None,
        "customer_name": None,
        "cancel_candidates": [],
        "reschedule_candidates": [],
        "selected_event": None,
    }


def clean_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def parse_time(text: str) -> datetime | None:
    if not text:
        return None
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )


def service_menu() -> str:
    lines = [f"💈 Welcome to {BUSINESS_NAME}", "", "Choose a service:"]
    for i, item in enumerate(SERVICES.values(), start=1):
        lines.append(f"{i}. {item['label']} - £{item['price']}")
    lines.append("")
    lines.append("You can also say things like:")
    lines.append("• book haircut tomorrow 3pm")
    lines.append("• skin fade friday 2pm")
    lines.append("• cancel my booking")
    lines.append("• reschedule my appointment")
    return "\n".join(lines)


def barber_menu() -> str:
    return (
        "✂️ Choose your barber:\n"
        "1. Mike\n"
        "2. Jay\n"
        "3. First available\n\n"
        "Reply with the number or name."
    )


def find_service(text: str) -> str | None:
    t = clean_text(text)

    alias_map = {
        "1": "haircut",
        "2": "skin fade",
        "3": "beard trim",
        "4": "shape up",
        "5": "kids cut",
        "haircut": "haircut",
        "cut": "haircut",
        "skin fade": "skin fade",
        "fade": "skin fade",
        "beard": "beard trim",
        "beard trim": "beard trim",
        "shape up": "shape up",
        "line up": "shape up",
        "kids cut": "kids cut",
        "kids": "kids cut",
    }

    if t in alias_map:
        return alias_map[t]

    for key in SERVICES.keys():
        if key in t:
            return key

    return None


def find_barber(text: str) -> str | None:
    t = clean_text(text)

    if t in {"1", "mike"}:
        return "mike"
    if t in {"2", "jay"}:
        return "jay"
    if t in {"3", "first available", "any", "any barber"}:
        return "first_available"

    if "mike" in t:
        return "mike"
    if "jay" in t:
        return "jay"
    if "first" in t or "any" in t:
        return "first_available"

    return None


def is_smalltalk(text: str) -> bool:
    return clean_text(text) in {
        "hi", "hello", "hey",
        "thanks", "thank you", "cheers",
        "ok", "okay", "cool", "nice",
        "see you", "bye",
    }


def smalltalk_reply(text: str) -> str:
    t = clean_text(text)
    if t in {"thanks", "thank you", "cheers"}:
        return "😊 You’re welcome! Let me know if you want to book, change or cancel an appointment."
    if t in {"hi", "hello", "hey"}:
        return "😊 Hey! What can I book for you?"
    if t in {"see you", "bye"}:
        return "👋 See you soon!"
    return "😊 No worries! Let me know if you want to book, change or cancel an appointment."


def format_event_time(event: dict) -> str:
    start_str = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    if not start_str:
        return "Unknown time"
    try:
        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(TIMEZONE)
        return dt.strftime("%a %d %b at %I:%M %p")
    except Exception:
        return start_str


def format_bookings_list(events: list[dict], title: str, footer: str) -> str:
    lines = [title, ""]
    for i, event in enumerate(events, start=1):
        lines.append(f"{i}. {format_event_time(event)}")
    lines.append("")
    lines.append(footer)
    return "\n".join(lines)


def pick_calendar_and_barber(barber_key: str) -> tuple[str, str]:
    if barber_key == "first_available":
        if BARBERS["mike"]["calendar_id"]:
            return BARBERS["mike"]["calendar_id"], "Mike"
        return BARBERS["jay"]["calendar_id"], "Jay"

    barber = BARBERS[barber_key]
    return barber["calendar_id"], barber["name"]


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").strip()

    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(from_number)
    text = clean_text(incoming_msg)

    if not incoming_msg:
        msg.body(service_menu())
        return str(resp)

    if text == "menu":
        reset_session(from_number)
        msg.body(service_menu())
        return str(resp)

    if is_smalltalk(incoming_msg):
        msg.body(smalltalk_reply(incoming_msg))
        return str(resp)

    try:
        ai = llm_extract(incoming_msg)
    except Exception:
        ai = {"intent": "unknown"}

    intent = (ai.get("intent") or "unknown").lower()
    ai_service = ai.get("service")
    ai_time = ai.get("time")
    ai_name = ai.get("name")

    service = ai_service or find_service(incoming_msg)
    barber = find_barber(incoming_msg)

    # Pending cancel selection
    if session["state"] == "awaiting_cancel_choice":
        events = session["cancel_candidates"]
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(events):
                event = events[idx]
                cancel_booking(event["id"])
                reset_session(from_number)
                msg.body(
                    f"✅ Booking cancelled.\n\n"
                    f"Cancelled: {format_event_time(event)}\n\n"
                    f"Anything else I can help with? 😊"
                )
                return str(resp)

        msg.body("Please reply with a valid booking number to cancel.")
        return str(resp)

    # Pending reschedule selection
    if session["state"] == "awaiting_reschedule_choice":
        events = session["reschedule_candidates"]
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(events):
                session["selected_event"] = events[idx]
                session["state"] = "awaiting_new_time"
                msg.body("🔁 What new time would you like? For example: tomorrow 3pm")
                return str(resp)

        msg.body("Please reply with a valid booking number to reschedule.")
        return str(resp)

    # Pending new time for reschedule
    if session["state"] == "awaiting_new_time":
        new_dt = parse_time(ai_time or incoming_msg)
        if not new_dt:
            msg.body("I couldn’t understand that time. Try something like: tomorrow 3pm")
            return str(resp)

        event = session["selected_event"]
        summary = (event.get("summary") or "").lower()
        service_key = find_service(summary) or "haircut"
        duration = SERVICES[service_key]["duration"]

        cancel_booking(event["id"])

        # keep same barber if possible
        old_barber = "mike"
        if "jay" in (event.get("description") or "").lower():
            old_barber = "jay"

        calendar_id, barber_name = pick_calendar_and_barber(old_barber)

        result = create_booking(
            calendar_id=calendar_id,
            customer_name=session["customer_name"] or "Guest",
            customer_phone=from_number,
            service_name=SERVICES[service_key]["label"],
            start_dt=new_dt,
            end_dt=new_dt + timedelta(minutes=duration),
            barber_name=barber_name,
        )

        reset_session(from_number)
        msg.body(
            f"✅ Booking rescheduled!\n\n"
            f"💈 Service: {SERVICES[service_key]['label']}\n"
            f"✂️ Barber: {barber_name}\n"
            f"🕒 Time: {new_dt.strftime('%a %d %b at %I:%M %p')}\n\n"
            f"📅 {result.get('link')}\n\n"
            f"You can say:\n"
            f"• reschedule\n"
            f"• cancel\n"
            f"• menu"
        )
        return str(resp)

    # Pending barber
    if session["state"] == "awaiting_barber":
        chosen_barber = barber
        if not chosen_barber:
            msg.body(barber_menu())
            return str(resp)

        session["barber"] = chosen_barber

        if session["time_text"]:
            dt = parse_time(session["time_text"])
            if not dt:
                session["state"] = "awaiting_time"
                msg.body("What time works for you?")
                return str(resp)

            session["time_dt"] = dt
            session["state"] = "awaiting_name"
            msg.body("Please reply with your name.")
            return str(resp)

        session["state"] = "awaiting_time"
        msg.body("📅 What time would you like?")
        return str(resp)

    # Pending time
    if session["state"] == "awaiting_time":
        dt = parse_time(ai_time or incoming_msg)
        if not dt:
            msg.body("I couldn’t understand that time. Try something like: tomorrow 3pm")
            return str(resp)

        session["time_dt"] = dt
        session["state"] = "awaiting_name"
        msg.body("Please reply with your name.")
        return str(resp)

    # Pending name
    if session["state"] == "awaiting_name":
        name = incoming_msg.strip()
        if len(name) < 2:
            msg.body("Please send a valid name.")
            return str(resp)

        session["customer_name"] = name

        service_key = session["service"]
        barber_key = session["barber"] or "first_available"
        dt = session["time_dt"]

        if not service_key or not dt:
            reset_session(from_number)
            msg.body("Something went wrong. Type MENU and try again.")
            return str(resp)

        duration = SERVICES[service_key]["duration"]
        calendar_id, barber_name = pick_calendar_and_barber(barber_key)

        result = create_booking(
            calendar_id=calendar_id,
            customer_name=name,
            customer_phone=from_number,
            service_name=SERVICES[service_key]["label"],
            start_dt=dt,
            end_dt=dt + timedelta(minutes=duration),
            barber_name=barber_name,
        )

        reset_session(from_number)

        msg.body(
            f"✅ Booking confirmed!\n\n"
            f"👤 Name: {name}\n"
            f"💈 Service: {SERVICES[service_key]['label']}\n"
            f"✂️ Barber: {barber_name}\n"
            f"🕒 Time: {dt.strftime('%a %d %b at %I:%M %p')}\n\n"
            f"📅 Calendar link:\n{result.get('link')}\n\n"
            f"What would you like to do next? 👇\n\n"
            f"• Type reschedule to change time\n"
            f"• Type cancel to cancel booking\n"
            f"• Type menu to book another\n\n"
            f"😊 Thanks for booking with us!"
        )
        return str(resp)

    # AI-first cancel
    if intent == "cancel" or "cancel" in text:
        bookings = list_upcoming(from_number)

        if not bookings:
            msg.body("You don’t have any upcoming bookings to cancel 😊")
            return str(resp)

        session["cancel_candidates"] = bookings
        session["state"] = "awaiting_cancel_choice"
        msg.body(
            format_bookings_list(
                bookings,
                "Here are your bookings:",
                "Reply with the number to cancel.",
            )
        )
        return str(resp)

    # AI-first reschedule
    if intent == "reschedule" or "change my booking" in text or "change appointment" in text:
        bookings = list_upcoming(from_number)

        if not bookings:
            msg.body("You don’t have any upcoming bookings to reschedule 😊")
            return str(resp)

        session["reschedule_candidates"] = bookings
        session["state"] = "awaiting_reschedule_choice"
        msg.body(
            format_bookings_list(
                bookings,
                "Which booking would you like to reschedule?",
                "Reply with the number.",
            )
        )
        return str(resp)

    # Full AI booking
    if intent == "book" or service:
        if service:
            session["service"] = service
        else:
            msg.body("Sure 👍 What service would you like?")
            return str(resp)

        if not session["barber"] and barber:
            session["barber"] = barber

        if ai_time:
            session["time_text"] = ai_time

        if not session["barber"]:
            session["state"] = "awaiting_barber"
            msg.body(
                f"✅ Service selected: {SERVICES[session['service']]['label']}\n\n"
                + barber_menu()
            )
            return str(resp)

        if not session["time_text"] and not session["time_dt"]:
            session["state"] = "awaiting_time"
            msg.body(f"Nice 👍 {SERVICES[session['service']]['label']}. What time works for you?")
            return str(resp)

        dt = session["time_dt"] or parse_time(session["time_text"])
        if not dt:
            session["state"] = "awaiting_time"
            msg.body("I couldn’t understand the time. Try something like: tomorrow 3pm")
            return str(resp)

        session["time_dt"] = dt
        session["state"] = "awaiting_name"
        if ai_name:
            incoming_name = ai_name.strip()
            if len(incoming_name) >= 2:
                session["customer_name"] = incoming_name
                # fall through into booking by reusing name path
                name = incoming_name

                service_key = session["service"]
                barber_key = session["barber"] or "first_available"
                duration = SERVICES[service_key]["duration"]
                calendar_id, barber_name = pick_calendar_and_barber(barber_key)

                result = create_booking(
                    calendar_id=calendar_id,
                    customer_name=name,
                    customer_phone=from_number,
                    service_name=SERVICES[service_key]["label"],
                    start_dt=dt,
                    end_dt=dt + timedelta(minutes=duration),
                    barber_name=barber_name,
                )

                reset_session(from_number)

                msg.body(
                    f"✅ Booking confirmed!\n\n"
                    f"👤 Name: {name}\n"
                    f"💈 Service: {SERVICES[service_key]['label']}\n"
                    f"✂️ Barber: {barber_name}\n"
                    f"🕒 Time: {dt.strftime('%a %d %b at %I:%M %p')}\n\n"
                    f"📅 Calendar link:\n{result.get('link')}\n\n"
                    f"What would you like to do next? 👇\n\n"
                    f"• Type reschedule to change time\n"
                    f"• Type cancel to cancel booking\n"
                    f"• Type menu to book another"
                )
                return str(resp)

        msg.body("Please reply with your name.")
        return str(resp)

    msg.body("I can help you book, reschedule or cancel appointments 😊")
    return str(resp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)