import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from llm_helper import llm_extract
from calendar_helper import (
    BARBERS,
    cancel_booking,
    create_booking,
    list_bookings,
    reschedule_booking,
    update_booking_service,
    is_free,   # 👈 ADD THIS LINE
)

app = Flask(__name__)

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
SESSIONS: dict[str, dict] = {}

SERVICES = {
    "haircut": {"label": "Haircut", "minutes": 30},
    "beard trim": {"label": "Beard Trim", "minutes": 20},
    "skin fade": {"label": "Skin Fade", "minutes": 45},
    "kids cut": {"label": "Kids Cut", "minutes": 30},
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def get_session(phone: str) -> dict:
    if phone not in SESSIONS:
        SESSIONS[phone] = {}
    return SESSIONS[phone]


def reset_session(phone: str) -> None:
    SESSIONS.pop(phone, None)


def detect_service(text: str) -> str | None:
    t = (text or "").lower().strip()

    if "kids cut" in t or "kid cut" in t or "kids" in t or "kid" in t:
        return "kids cut"
    if "skin fade" in t or "fade" in t:
        return "skin fade"
    if "beard trim" in t or "beard" in t or "trim" in t:
        return "beard trim"
    if "haircut" in t or "hair cut" in t or re.search(r"\bcut\b", t):
        return "haircut"
    return None


def detect_barber(text: str) -> str | None:
    t = (text or "").lower()
    if "jay" in t:
        return "jay"
    if "mike" in t:
        return "mike"
    return None


def has_time_only(text: str) -> bool:
    t = (text or "").lower().strip()
    return bool(
        re.search(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", t)
        or re.fullmatch(r"\d{1,2}(:\d{2})?", t)
    )


def parse_time_only(text: str, base_dt: datetime | None = None) -> datetime | None:
    now = datetime.now(TIMEZONE)
    base = base_dt or now
    t = (text or "").strip().lower()

    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if not m:
        if re.fullmatch(r"\d{1,2}(:\d{2})?", t):
            parts = t.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            if hour > 23 or minute > 59:
                return None
            candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate = candidate + timedelta(days=1)
            return candidate
        return None

    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridian = m.group(3)

    if hour < 1 or hour > 12 or minute > 59:
        return None

    if meridian == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12

    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def parse_when_text(text: str, base_dt: datetime | None = None) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None

    now = datetime.now(TIMEZONE)
    base = base_dt or now
    lower = text.lower()

    if lower.startswith("tomorrow"):
        time_part = lower.replace("tomorrow", "", 1).strip()
        day_base = (now + timedelta(days=1)).replace(second=0, microsecond=0)
        if time_part:
            parsed_time = parse_time_only(time_part, day_base)
            if parsed_time:
                return parsed_time
        return day_base.replace(hour=9, minute=0)

    for day_name, day_num in WEEKDAYS.items():
        if day_name in lower:
            days_ahead = (day_num - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = (now + timedelta(days=days_ahead)).replace(second=0, microsecond=0)
            time_part = lower.replace(day_name, "").strip(" ,")
            if time_part:
                parsed_time = parse_time_only(time_part, target)
                if parsed_time:
                    return parsed_time
            return target.replace(hour=9, minute=0)

    if has_time_only(lower):
        return parse_time_only(lower, base)

    parsed = dateparser.parse(
        text,
        settings={
            "TIMEZONE": str(TIMEZONE),
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now,
            "STRICT_PARSING": False,
        },
    )
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed


def format_booking_line(index: int, booking: dict) -> str:
    start = booking["start_dt"].astimezone(TIMEZONE)
    return (
        f"{index}. {booking['service_label']} with {booking['barber_name']} — "
        f"{start.strftime('%a %d %b %I:%M%p')}"
    )


def render_bookings(bookings: list[dict]) -> str:
    lines = [format_booking_line(i + 1, b) for i, b in enumerate(bookings)]
    return "\n".join(lines)


def choose_booking_prompt(bookings: list[dict], action_text: str) -> str:
    return f"{action_text}\n\n{render_bookings(bookings)}\n\nReply with the number 👍"


def first_booking_or_prompt(
    msg,
    phone: str,
    bookings: list[dict],
    session: dict,
    action_key: str,
    action_text: str,
) -> tuple[dict | None, str | None]:
    if not bookings:
        msg.body("You’ve got no bookings 👍")
        return None, str(msg.response)

    if len(bookings) == 1:
        return bookings[0], None

    session["pending_action"] = action_key
    SESSIONS[phone] = session
    msg.body(choose_booking_prompt(bookings, action_text))
    return None, str(msg.response)


def parse_selection(text: str, bookings: list[dict]) -> int | None:
    t = (text or "").strip()
    if not t.isdigit():
        return None
    idx = int(t) - 1
    if idx < 0 or idx >= len(bookings):
        return None
    return idx


def available_suggestions(bookings: list[dict], barber_key: str, minutes: int, desired: datetime) -> list[datetime]:
    busy = [
        b for b in bookings
        if b["barber_key"] == barber_key
    ]
    suggestions: list[datetime] = []
    for offset in [30, 60, 90, 120]:
        candidate = desired + timedelta(minutes=offset)
        candidate_end = candidate + timedelta(minutes=minutes)
        clash = False
        for b in busy:
            if candidate < b["end_dt"] and candidate_end > b["start_dt"]:
                clash = True
                break
        if not clash:
            suggestions.append(candidate)
        if len(suggestions) == 2:
            break
    return suggestions


def merge_extraction(text: str, data: dict, session: dict) -> dict:
    service = detect_service(text) or data.get("service")
    barber = detect_barber(text) or data.get("barber")
    when_text = data.get("when_text")
    if not when_text and parse_when_text(text, session.get("date_base")):
        when_text = text

    intent = data.get("intent") or "unknown"
    lower = text.lower()

    if "cancel" in lower:
        intent = "cancel"
    elif any(x in lower for x in ["add beard", "add trim", "add beard trim", "also get", "add on"]):
        intent = "add_service"
    elif any(x in lower for x in ["change to", "switch to", "make it", "instead"]):
        intent = "change_service_smart"
    elif "change service" in lower or "different service" in lower:
        intent = "change_service"
    elif any(x in lower for x in ["reschedule", "move booking", "move appointment", "change time", "another time", "different time"]):
        intent = "reschedule"
    elif any(x in lower for x in ["view", "show booking", "my booking", "my bookings", "what have i got booked"]):
        intent = "view"

    return {
        "intent": intent,
        "service": service.lower().strip() if isinstance(service, str) else None,
        "barber": barber.lower().strip() if isinstance(barber, str) else None,
        "when_text": when_text.strip() if isinstance(when_text, str) else None,
        "name": data.get("name"),
    }


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    from_number = request.values.get("From", "").strip()
    text = request.values.get("Body", "").strip()
    profile_name = request.values.get("ProfileName", "Guest").strip() or "Guest"

    resp = MessagingResponse()
    msg = resp.message()

    session = get_session(from_number)
    lower = text.lower()

    if lower in ["hi", "hello", "hey"]:
        msg.body("Hey 👋 What can I get you booked in for?")
        return str(resp)

    if any(x in lower for x in ["thanks", "thank you", "cheers"]):
        msg.body("You're welcome 😊")
        return str(resp)

    if lower in ["menu", "start", "reset"]:
        reset_session(from_number)
        msg.body("No problem 👍 What would you like to book? ✂️")
        return str(resp)

    bookings = list_bookings(from_number)
    data = merge_extraction(text, llm_extract(text) or {}, session)

    # handle numeric selection for pending multi-booking actions
    pending_action = session.get("pending_action")
    if pending_action and text.strip().isdigit():
        idx = parse_selection(text, bookings)
        if idx is None:
            msg.body("Reply with a valid booking number 👍")
            return str(resp)

        selected = bookings[idx]
        session.pop("pending_action", None)
        session["selected_booking_id"] = selected["id"]
        session["selected_booking"] = selected
        SESSIONS[from_number] = session

        if pending_action == "cancel_select":
            success = cancel_booking(selected["id"])
            reset_session(from_number)
            msg.body("Done 👍 your booking has been cancelled." if success else "Couldn’t cancel it properly 😅 try again")
            return str(resp)

        if pending_action == "reschedule_select":
            session["reschedule_mode"] = True
            session["reschedule_booking_id"] = selected["id"]
            SESSIONS[from_number] = session
            msg.body("No worries 👍 what time would you like instead? ⏰")
            return str(resp)

        if pending_action == "change_service_select":
            if session.get("target_service") in SERVICES:
                booking = selected
                new_service_key = session["target_service"]
                service_def = SERVICES[new_service_key]
                result = update_booking_service(
                    event_id=booking["id"],
                    new_service_name=service_def["label"],
                    new_service_key=new_service_key,
                    new_minutes=service_def["minutes"],
                )
                reset_session(from_number)
                if not result:
                    msg.body("I couldn’t change that booking just now 😅")
                else:
                    msg.body(
                        f"Done 👌 I’ve changed it for you.\n\n"
                        f"✂️ {service_def['label']} with {booking['barber_name']}\n"
                        f"📅 {booking['start_dt'].strftime('%a %d %b')}\n"
                        f"⏰ {booking['start_dt'].strftime('%I:%M%p')}"
                    )
                return str(resp)

        if pending_action == "add_service_select":
            if session.get("target_service") in SERVICES:
                booking = selected
                extra_key = session["target_service"]
                extra_minutes = SERVICES[extra_key]["minutes"]
                total_minutes = booking["minutes"] + extra_minutes
                combined_name = f"{booking['service_label']} + {SERVICES[extra_key]['label']}"
                result = update_booking_service(
                    event_id=booking["id"],
                    new_service_name=combined_name,
                    new_service_key=f"{booking['service_key']} + {extra_key}",
                    new_minutes=total_minutes,
                )
                reset_session(from_number)
                if not result:
                    msg.body("I couldn’t update that booking just now 😅")
                else:
                    msg.body(
                        f"Nice upgrade 👌\n\n"
                        f"✂️ {combined_name} with {booking['barber_name']}\n"
                        f"📅 {booking['start_dt'].strftime('%a %d %b')}\n"
                        f"⏰ {booking['start_dt'].strftime('%I:%M%p')}"
                    )
                return str(resp)

    # follow-up time capture
    if parse_when_text(text, session.get("date_base")) and (
        "service" in session or session.get("reschedule_mode")
    ):
        session["when"] = text
        SESSIONS[from_number] = session

    # cancel
    if data["intent"] == "cancel":
        if not bookings:
            msg.body("You’ve got no bookings to cancel 👍")
            return str(resp)
        if len(bookings) == 1:
            success = cancel_booking(bookings[0]["id"])
            reset_session(from_number)
            msg.body("Done 👍 your booking has been cancelled." if success else "Couldn’t cancel it properly 😅 try again")
            return str(resp)
        session["pending_action"] = "cancel_select"
        SESSIONS[from_number] = session
        msg.body(choose_booking_prompt(bookings, "Which booking would you like to cancel?"))
        return str(resp)

    # view
    if data["intent"] == "view":
        if not bookings:
            msg.body("You’ve got no bookings at the moment 👍")
            return str(resp)
        msg.body(f"Here’s what I’ve got for you 👌\n\n{render_bookings(bookings)}")
        return str(resp)

    # smart service change
    if data["intent"] == "change_service_smart":
        if not bookings:
            msg.body("You don’t have a booking to change 👍")
            return str(resp)

        new_service_key = data["service"]
        if not new_service_key or new_service_key not in SERVICES:
            msg.body("What would you like to change it to? ✂️")
            return str(resp)

        if len(bookings) > 1:
            session["pending_action"] = "change_service_select"
            session["target_service"] = new_service_key
            SESSIONS[from_number] = session
            msg.body(choose_booking_prompt(bookings, "Which booking would you like to change?"))
            return str(resp)

        booking = bookings[0]
        service_def = SERVICES[new_service_key]
        result = update_booking_service(
            event_id=booking["id"],
            new_service_name=service_def["label"],
            new_service_key=new_service_key,
            new_minutes=service_def["minutes"],
        )

        reset_session(from_number)
        if not result:
            msg.body("I couldn’t change that booking just now 😅")
        else:
            msg.body(
                f"Done 👌 I’ve changed it for you.\n\n"
                f"✂️ {service_def['label']} with {booking['barber_name']}\n"
                f"📅 {booking['start_dt'].strftime('%a %d %b')}\n"
                f"⏰ {booking['start_dt'].strftime('%I:%M%p')}"
            )
        return str(resp)

    # basic service change
    if data["intent"] == "change_service":
        if not bookings:
            msg.body("You don’t have a booking to change 👍")
            return str(resp)
        session["change_service_mode"] = True
        SESSIONS[from_number] = session
        msg.body("No problem 👍 what would you like instead? ✂️")
        return str(resp)

    if session.get("change_service_mode"):
        new_service = detect_service(text) or data.get("service")
        if not new_service or new_service not in SERVICES:
            msg.body("What would you like to change it to? ✂️")
            return str(resp)

        if len(bookings) > 1:
            session["pending_action"] = "change_service_select"
            session["target_service"] = new_service
            session.pop("change_service_mode", None)
            SESSIONS[from_number] = session
            msg.body(choose_booking_prompt(bookings, "Which booking would you like to change?"))
            return str(resp)

        booking = bookings[0]
        service_def = SERVICES[new_service]
        result = update_booking_service(
            event_id=booking["id"],
            new_service_name=service_def["label"],
            new_service_key=new_service,
            new_minutes=service_def["minutes"],
        )
        reset_session(from_number)
        if not result:
            msg.body("I couldn’t change that booking just now 😅")
        else:
            msg.body(
                f"Done 👌 I’ve changed it for you.\n\n"
                f"✂️ {service_def['label']} with {booking['barber_name']}\n"
                f"📅 {booking['start_dt'].strftime('%a %d %b')}\n"
                f"⏰ {booking['start_dt'].strftime('%I:%M%p')}"
            )
        return str(resp)

    # add service
    if data["intent"] == "add_service":
        if not bookings:
            msg.body("You don’t have a booking to upgrade 👍")
            return str(resp)

        extra_key = data["service"]
        if not extra_key or extra_key not in SERVICES:
            msg.body("What would you like to add? ✂️")
            return str(resp)

        if len(bookings) > 1:
            session["pending_action"] = "add_service_select"
            session["target_service"] = extra_key
            SESSIONS[from_number] = session
            msg.body(choose_booking_prompt(bookings, "Which booking would you like to add that to?"))
            return str(resp)

        booking = bookings[0]
        total_minutes = booking["minutes"] + SERVICES[extra_key]["minutes"]
        combined_name = f"{booking['service_label']} + {SERVICES[extra_key]['label']}"

        result = update_booking_service(
            event_id=booking["id"],
            new_service_name=combined_name,
            new_service_key=f"{booking['service_key']} + {extra_key}",
            new_minutes=total_minutes,
        )

        reset_session(from_number)
        if not result:
            msg.body("I couldn’t update that booking just now 😅")
        else:
            msg.body(
                f"Nice upgrade 👌\n\n"
                f"✂️ {combined_name} with {booking['barber_name']}\n"
                f"📅 {booking['start_dt'].strftime('%a %d %b')}\n"
                f"⏰ {booking['start_dt'].strftime('%I:%M%p')}"
            )
        return str(resp)

    # reschedule
    if data["intent"] == "reschedule":
        if not bookings:
            msg.body("You’ve got no bookings to change 👍")
            return str(resp)

        target_when = data["when_text"] or session.get("when")
        if len(bookings) > 1 and not session.get("selected_booking_id"):
            session["pending_action"] = "reschedule_select"
            if target_when:
                session["when"] = target_when
            SESSIONS[from_number] = session
            msg.body(choose_booking_prompt(bookings, "Which booking would you like to move?"))
            return str(resp)

        booking = bookings[0]
        session["reschedule_mode"] = True
        session["reschedule_booking_id"] = booking["id"]
        if target_when:
            session["when"] = target_when
        SESSIONS[from_number] = session

        if "when" not in session:
            msg.body("No worries 👍 what time would you like instead? ⏰")
            return str(resp)

    if session.get("reschedule_mode"):
        when_text = session.get("when") or text
        base_dt = None
        selected_booking = next((b for b in bookings if b["id"] == session.get("reschedule_booking_id")), None)
        if selected_booking:
            base_dt = selected_booking["start_dt"]

        dt = parse_when_text(when_text, base_dt)
        if not dt:
            msg.body("Try something like 'tomorrow 3pm' 👍")
            return str(resp)

        result = reschedule_booking(session["reschedule_booking_id"], dt)
        reset_session(from_number)

        if not result:
            selected = selected_booking or (bookings[0] if bookings else None)
            if selected:
                suggestions = available_suggestions(bookings, selected["barber_key"], selected["minutes"], dt)
                if suggestions:
                    msg.body(
                        "That slot is taken 😅 I can do:\n\n"
                        f"1. {suggestions[0].strftime('%a %d %b %I:%M%p')}\n"
                        f"2. {suggestions[1].strftime('%a %d %b %I:%M%p') if len(suggestions) > 1 else ''}".strip()
                    )
                    return str(resp)
            msg.body("That time is taken 😅 try another")
            return str(resp)

        link = result if isinstance(result, str) else result.get("link", "")
        link_line = f"\n\n🔗 {link}" if link else ""
        msg.body(
            f"All sorted 👌\n\n"
            f"📅 {dt.strftime('%a %d %b')}\n"
            f"⏰ {dt.strftime('%I:%M%p')}"
            f"{link_line}"
        )
        return str(resp)

    # booking capture
    service_value = detect_service(text) or data.get("service")
    barber_value = detect_barber(text) or data.get("barber")
    when_value = data.get("when_text")
    if not when_value and parse_when_text(text, session.get("date_base")):
        when_value = text

    if service_value:
        clean_service = service_value.strip().lower()
        if clean_service in SERVICES:
            session["service"] = clean_service

    if barber_value:
        clean_barber = barber_value.strip().lower()
        if clean_barber in BARBERS:
            session["barber"] = clean_barber

    if when_value:
        session["when"] = when_value

    session["name"] = data.get("name") or session.get("name") or profile_name
    SESSIONS[from_number] = session

    if "service" not in session:
        msg.body("What would you like to book? ✂️")
        return str(resp)

    if "barber" not in session:
        msg.body("Who would you like? (Jay or Mike) 💈")
        return str(resp)

    if "when" not in session:
        msg.body("When would you like to come in? ⏰")
        return str(resp)

    dt = parse_when_text(session["when"])
    if not dt:
        msg.body("Try something like 'tomorrow 3pm' 👍")
        return str(resp)

    service = SERVICES.get(session["service"])
    barber = BARBERS.get(session["barber"])

    if not service or not barber:
        reset_session(from_number)
        msg.body("Let’s try that again 👍")
        return str(resp)

    end_dt = dt + timedelta(minutes=service["minutes"])
    if not is_free(dt, end_dt, barber):
        suggestions = available_suggestions(bookings, session["barber"], service["minutes"], dt)
        if suggestions:
            msg.body(
                "That slot is taken 😅 I can do:\n\n"
                f"1. {suggestions[0].strftime('%a %d %b %I:%M%p')}\n"
                f"2. {suggestions[1].strftime('%a %d %b %I:%M%p') if len(suggestions) > 1 else ''}".strip()
            )
        else:
            msg.body("That slot is taken 😅 try another")
        return str(resp)

    result = create_booking(
        phone=from_number,
        service_name=service["label"],
        service_key=session["service"],
        start_dt=dt,
        minutes=service["minutes"],
        name=session["name"],
        barber=barber,
    )

    customer_name = session["name"]
    reset_session(from_number)

    link = result.get("link", "")
    link_line = f"\n\n🔗 {link}" if link else ""
    msg.body(
        f"Nice one {customer_name} 👌 you're booked in!\n\n"
        f"✂️ {service['label']} with {barber['name']}\n"
        f"📅 {dt.strftime('%a %d %b')}\n"
        f"⏰ {dt.strftime('%I:%M%p')}"
        f"{link_line}\n\n"
        f"If you need to change anything just message 👍"
    )
    return str(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))