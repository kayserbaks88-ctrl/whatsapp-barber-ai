import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from calendar_helper import get_available_slots, create_booking

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "TrimTech AI")

# =========================
# SERVICES
# =========================
SERVICES = {
    "haircut": {"label": "Haircut", "duration": 30, "price": 18},
    "skin fade": {"label": "Skin Fade", "duration": 45, "price": 22},
    "beard trim": {"label": "Beard Trim", "duration": 20, "price": 12},
    "shape up": {"label": "Shape Up", "duration": 20, "price": 10},
    "kids cut": {"label": "Kids Cut", "duration": 30, "price": 15},
}

SERVICE_ALIASES = {
    "1": "haircut",
    "2": "skin fade",
    "3": "beard trim",
    "4": "shape up",
    "5": "kids cut",
    "haircut": "haircut",
    "cut": "haircut",
    "trim": "haircut",
    "skin fade": "skin fade",
    "fade": "skin fade",
    "beard": "beard trim",
    "beard trim": "beard trim",
    "shape up": "shape up",
    "line up": "shape up",
    "kids": "kids cut",
    "kids cut": "kids cut",
}

# =========================
# BARBERS
# Put real calendar IDs in .env
# =========================
BARBERS = {
    "mike": {
        "name": "Mike",
        "calendar_id": os.getenv("BARBER_MIKE_CALENDAR_ID", ""),
        "working_days": [0, 1, 2, 3, 4, 5],  # Mon-Sat
        "start_hour": 9,
        "end_hour": 18,
        "services": ["haircut", "skin fade", "beard trim", "shape up", "kids cut"],
    },
    "jay": {
        "name": "Jay",
        "calendar_id": os.getenv("BARBER_JAY_CALENDAR_ID", ""),
        "working_days": [0, 1, 2, 3, 4, 5],  # Mon-Sat
        "start_hour": 10,
        "end_hour": 19,
        "services": ["haircut", "skin fade", "beard trim", "shape up"],
    },
}

BARBER_ALIASES = {
    "1": "mike",
    "2": "jay",
    "3": "any",
    "mike": "mike",
    "jay": "jay",
    "any": "any",
    "any barber": "any",
    "first available": "any",
    "whoever": "any",
}

SESSIONS: dict[str, dict] = {}


# =========================
# HELPERS
# =========================
def clean_text(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def get_session(phone: str) -> dict:
    if phone not in SESSIONS:
        SESSIONS[phone] = {
            "state": "idle",
            "service_key": None,
            "barber_key": None,
            "offered_slots": [],
            "selected_slot": None,
            "customer_name": None,
        }
    return SESSIONS[phone]


def reset_session(phone: str):
    SESSIONS[phone] = {
        "state": "idle",
        "service_key": None,
        "barber_key": None,
        "offered_slots": [],
        "selected_slot": None,
        "customer_name": None,
    }


def service_menu() -> str:
    lines = [f"💈 Welcome to {BUSINESS_NAME}", "", "Choose a service:"]
    for i, (key, item) in enumerate(SERVICES.items(), start=1):
        lines.append(f"{i}. {item['label']} - £{item['price']}")
    lines.append("")
    lines.append("Reply with the number or service name.")
    return "\n".join(lines)


def barber_menu(service_key: str) -> str:
    lines = ["✂️ Choose your barber:"]
    available = []

    idx = 1
    for barber_key, barber in BARBERS.items():
        if service_key in barber["services"] and barber["calendar_id"]:
            lines.append(f"{idx}. {barber['name']}")
            available.append(barber_key)
            idx += 1

    lines.append(f"{idx}. First available")
    lines.append("")
    lines.append("Reply with the number or name.")
    return "\n".join(lines)


def parse_service(user_text: str) -> str | None:
    return SERVICE_ALIASES.get(clean_text(user_text))


def get_barber_options_for_service(service_key: str) -> list[str]:
    result = []
    for barber_key, barber in BARBERS.items():
        if service_key in barber["services"] and barber["calendar_id"]:
            result.append(barber_key)
    return result


def parse_barber(user_text: str, service_key: str) -> str | None:
    text = clean_text(user_text)

    available_barbers = get_barber_options_for_service(service_key)
    numbered_map = {}
    num = 1
    for barber_key in available_barbers:
        numbered_map[str(num)] = barber_key
        num += 1
    numbered_map[str(num)] = "any"

    if text in numbered_map:
        return numbered_map[text]

    mapped = BARBER_ALIASES.get(text)
    if mapped == "any":
        return "any"
    if mapped in available_barbers:
        return mapped

    return None


def format_slots(slot_items: list[dict]) -> str:
    lines = ["📅 Here are the next available slots:"]
    for i, item in enumerate(slot_items, start=1):
        slot_dt = item["slot"]
        barber_name = item["barber_name"]
        lines.append(
            f"{i}. {slot_dt.strftime('%a %d %b at %I:%M %p')} - {barber_name}"
        )
    lines.append("")
    lines.append("Reply with the slot number.")
    return "\n".join(lines)


def build_slots_for_selected_barber(service_key: str, barber_key: str) -> list[dict]:
    service = SERVICES[service_key]
    barber = BARBERS[barber_key]

    slots = get_available_slots(
        calendar_id=barber["calendar_id"],
        duration_minutes=service["duration"],
        days_ahead=7,
        start_hour=barber["start_hour"],
        end_hour=barber["end_hour"],
        slot_step_minutes=15,
        working_days=barber["working_days"],
        limit=5,
    )

    return [
        {
            "slot": slot,
            "barber_key": barber_key,
            "barber_name": barber["name"],
        }
        for slot in slots
    ]


def build_slots_for_any_barber(service_key: str) -> list[dict]:
    service = SERVICES[service_key]
    all_slots: list[dict] = []

    for barber_key in get_barber_options_for_service(service_key):
        barber = BARBERS[barber_key]
        slots = get_available_slots(
            calendar_id=barber["calendar_id"],
            duration_minutes=service["duration"],
            days_ahead=7,
            start_hour=barber["start_hour"],
            end_hour=barber["end_hour"],
            slot_step_minutes=15,
            working_days=barber["working_days"],
            limit=5,
        )
        for slot in slots:
            all_slots.append(
                {
                    "slot": slot,
                    "barber_key": barber_key,
                    "barber_name": barber["name"],
                }
            )

    all_slots.sort(key=lambda x: x["slot"])
    return all_slots[:5]


def parse_slot_choice(user_text: str, offered_slots: list[dict]) -> dict | None:
    text = clean_text(user_text)
    if not text.isdigit():
        return None

    idx = int(text) - 1
    if 0 <= idx < len(offered_slots):
        return offered_slots[idx]
    return None


def is_cancel_text(text: str) -> bool:
    return clean_text(text) in {"cancel", "stop", "menu", "restart"}


# =========================
# WEBHOOK
# =========================
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
        session["state"] = "awaiting_service"
        return str(resp)

    if is_cancel_text(text):
        reset_session(from_number)
        session = get_session(from_number)
        session["state"] = "awaiting_service"
        msg.body("✅ Booking flow reset.\n\n" + service_menu())
        return str(resp)

    # -------------------------
    # Start flow from idle
    # -------------------------
    if session["state"] == "idle":
        service_key = parse_service(incoming_msg)
        if service_key:
            session["service_key"] = service_key
            session["state"] = "awaiting_barber"
            msg.body(
                f"✅ Service selected: {SERVICES[service_key]['label']}\n\n"
                + barber_menu(service_key)
            )
            return str(resp)

        session["state"] = "awaiting_service"
        msg.body(service_menu())
        return str(resp)

    # -------------------------
    # Awaiting service
    # -------------------------
    if session["state"] == "awaiting_service":
        service_key = parse_service(incoming_msg)
        if not service_key:
            msg.body(
                "Sorry, I didn’t catch that service.\n\n"
                + service_menu()
            )
            return str(resp)

        session["service_key"] = service_key
        session["state"] = "awaiting_barber"

        msg.body(
            f"✅ Service selected: {SERVICES[service_key]['label']}\n\n"
            + barber_menu(service_key)
        )
        return str(resp)

    # -------------------------
    # Awaiting barber
    # -------------------------
    if session["state"] == "awaiting_barber":
        service_key = session["service_key"]
        barber_choice = parse_barber(incoming_msg, service_key)

        if not barber_choice:
            msg.body(
                "Sorry, I didn’t catch the barber choice.\n\n"
                + barber_menu(service_key)
            )
            return str(resp)

        session["barber_key"] = barber_choice

        if barber_choice == "any":
            offered_slots = build_slots_for_any_barber(service_key)
        else:
            offered_slots = build_slots_for_selected_barber(service_key, barber_choice)

        if not offered_slots:
            msg.body(
                "😕 No available slots found for that selection in the next 7 days.\n"
                "Reply 'menu' to start again."
            )
            return str(resp)

        session["offered_slots"] = offered_slots
        session["state"] = "awaiting_slot"

        msg.body(format_slots(offered_slots))
        return str(resp)

    # -------------------------
    # Awaiting slot
    # -------------------------
    if session["state"] == "awaiting_slot":
        selected = parse_slot_choice(incoming_msg, session["offered_slots"])

        if not selected:
            msg.body(
                "Please reply with a valid slot number.\n\n"
                + format_slots(session["offered_slots"])
            )
            return str(resp)

        session["selected_slot"] = selected
        session["state"] = "awaiting_name"

        msg.body(
            f"✅ Slot selected: {selected['slot'].strftime('%a %d %b at %I:%M %p')} "
            f"with {selected['barber_name']}\n\n"
            "Please reply with your name."
        )
        return str(resp)

    # -------------------------
    # Awaiting name
    # -------------------------
    if session["state"] == "awaiting_name":
        customer_name = incoming_msg.strip()
        if len(customer_name) < 2:
            msg.body("Please enter a valid name.")
            return str(resp)

        selected = session["selected_slot"]
        service_key = session["service_key"]
        service = SERVICES[service_key]

        start_dt = selected["slot"]
        end_dt = start_dt + timedelta(minutes=service["duration"])

        barber_key = selected["barber_key"]
        barber = BARBERS[barber_key]

        created = create_booking(
            calendar_id=barber["calendar_id"],
            customer_name=customer_name,
            customer_phone=from_number,
            service_name=service["label"],
            start_dt=start_dt,
            end_dt=end_dt,
            barber_name=barber["name"],
        )

        reset_session(from_number)

        event_link = created.get("htmlLink", "")
        confirmation = (
            f"✅ Booking confirmed!\n\n"
            f"Name: {customer_name}\n"
            f"Service: {service['label']}\n"
            f"Barber: {barber['name']}\n"
            f"Time: {start_dt.strftime('%a %d %b at %I:%M %p')}\n"
        )

        if event_link:
            confirmation += f"\nCalendar link:\n{event_link}\n"

        confirmation += "\nReply 'menu' to book another appointment."

        msg.body(confirmation)
        return str(resp)

    # fallback
    reset_session(from_number)
    session = get_session(from_number)
    session["state"] = "awaiting_service"
    msg.body(service_menu())
    return str(resp)


if __name__ == "__main__":
    app.run(debug=True)