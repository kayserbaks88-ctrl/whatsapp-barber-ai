import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import dateparser


def parse_when(text: str, timezone_name: str):
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)

    text_lower = text.lower()

    # 🔥 FORCE tomorrow logic
    if "tomorrow" in text_lower:
        base = now + timedelta(days=1)
    elif "today" in text_lower:
        base = now
    else:
        base = now

    parsed = dateparser.parse(
        text,
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": base,
            "PREFER_DATES_FROM": "future",
        },
    )

    return parsed



from calendar_helper import (
    BARBERS,
    SERVICES,
    cancel_booking,
    create_booking,
    is_free,
    list_bookings,
    reschedule_booking,
)


YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "please do",
    "book it", "confirm", "go ahead", "do it", "sounds good"
}
NO_WORDS = {
    "no", "nope", "nah", "stop", "cancel that", "don't", "do not"
}
THANKS_WORDS = {
    "thanks", "thank you", "cheers", "nice one", "perfect", "great"
}


def _parse_datetime(text: str, timezone_name: str):
    return dateparser.parse(
        text,
        settings={
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(),
            "PREFER_DAY_OF_MONTH": "current",
        },
    )


def _friendly_service(msg: str) -> str | None:
    msg = msg.lower()

    if "skin fade" in msg or "fade" in msg:
        return "skin fade"
    if "beard" in msg or "trim beard" in msg:
        return "beard trim"
    if "kids" in msg or "kid" in msg or "child" in msg:
        return "kids cut"
    if "haircut" in msg or "hair cut" in msg:
        return "haircut"

    return None


def _friendly_barber(msg: str) -> str | None:
    msg = msg.lower()
    if "jay" in msg:
        return "jay"
    if "mike" in msg:
        return "mike"
    return None


def _day_time_text(start_dt: datetime) -> tuple[str, str]:
    today = datetime.now(start_dt.tzinfo).date()
    booking_day = start_dt.date()

    if booking_day == today:
        day_text = "Today"
    elif booking_day == today + timedelta(days=1):
        day_text = "Tomorrow"
    else:
        day_text = start_dt.strftime("%A")

    time_text = start_dt.strftime("%I:%M %p")
    return day_text, time_text


def _service_label(service_key: str) -> str:
    return SERVICES[service_key]["label"]


def _barber_label(barber_key: str) -> str:
    return BARBERS[barber_key]["name"]


def _clear_booking_state(session: dict):
    session["data"] = {}
    session["pending_booking"] = None


def _clear_action_state(session: dict):
    session["pending_cancel"] = None
    session["pending_reschedule"] = None


def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:
    msg = (user_message or "").strip()
    msg_lower = msg.lower()
    customer_name = (profile_name or "").strip()

    session.setdefault("data", {})
    session.setdefault("pending_booking", None)
    session.setdefault("pending_cancel", None)
    session.setdefault("pending_reschedule", None)

    data = session["data"]

    # First greeting
    if not session.get("welcomed"):
        session["welcomed"] = True
        if customer_name:
            return f"Welcome back {customer_name} 👋 What can I get you booked in for today? ✂️"
        return f"Hey 👋 Welcome to {business_name}. What can I get you booked in for today? ✂️"

    # Small talk / thanks
    if msg_lower in THANKS_WORDS:
        return "You're welcome 😊 Just message anytime if you need anything 👍"

    if msg_lower in {"hi", "hey", "hello", "yo"}:
        if customer_name:
            return f"Hey {customer_name} 👋 What can I get you booked in for today? ✂️"
        return "Hey 👋 What can I get you booked in for today? ✂️"

    # Yes / No handling for pending actions
    if msg_lower in YES_WORDS:
        if session.get("pending_booking"):
            pending = session["pending_booking"]
            try:
                start_dt = datetime.fromisoformat(pending["when"])
                service = pending["service"]
                barber = pending["barber"]
                minutes = SERVICES[service]["minutes"]

                if not is_free(start_dt, start_dt + timedelta(minutes=minutes), barber):
                    _clear_booking_state(session)
                    return "That slot’s just gone 😅 want another time?"

                result = create_booking(
                    phone=phone,
                    service_name=service,
                    start_dt=start_dt,
                    minutes=minutes,
                    name=customer_name or "Customer",
                    barber=barber,
                )

                _clear_booking_state(session)

                day_text, time_text = _day_time_text(start_dt)
                name_part = f"{customer_name} " if customer_name else ""

                return (
                    f"Nice one {name_part}👌 you're booked in!\n\n"
                    f"📅 {day_text} {time_text}\n"
                    f"✂️ {_service_label(service)} with {_barber_label(barber)}\n\n"
                    f"📲 View booking:\n{result.get('link', '')}"
                )
            except Exception:
                _clear_booking_state(session)
                return "Something went wrong booking that 😅 send it again and I’ll sort it."

        if session.get("pending_cancel"):
            pending = session["pending_cancel"]
            ok = cancel_booking(pending["id"])
            _clear_action_state(session)
            if ok:
                return "All sorted 👍 your booking has been cancelled."
            return "I couldn’t cancel that one just now 😅 try again in a sec."

        if session.get("pending_reschedule"):
            pending = session["pending_reschedule"]
            try:
                new_start = datetime.fromisoformat(pending["new_when"])
                result = reschedule_booking(pending["id"], new_start)
                _clear_action_state(session)
                session["data"] = {}

                if result:
                    day_text, time_text = _day_time_text(new_start)
                    return f"Done 👍 I’ve moved your booking to {day_text} {time_text}."
                return "I couldn’t move that booking just now 😅"
            except Exception:
                _clear_action_state(session)
                session["data"] = {}
                return "I couldn’t move that booking just now 😅 try another time."

    if msg_lower in YES_WORDS:
        if session.get("pending_booking"):
            pending = session["pending_booking"]

            # 🚨 IMPORTANT: CLEAR FIRST (prevents loop)
            session["pending_booking"] = None
            session["data"] = {}

            try:
                start_dt = datetime.fromisoformat(pending["when"])
                service = pending["service"]
                barber = pending["barber"]
                minutes = SERVICES[service]["minutes"]

                if not is_free(start_dt, start_dt + timedelta(minutes=minutes), barber):
                    return "That slot’s just gone 😅 want another time?"

                result = create_booking(
                    phone=phone,
                    service_name=service,
                    start_dt=start_dt,
                    minutes=minutes,
                    name=customer_name or "Customer",
                    barber=barber,
                )

                day_text, time_text = _day_time_text(start_dt)
                name_part = f"{customer_name} " if customer_name else ""

                return (
                    f"Nice one {name_part}👌 you're booked in!\n\n"
                    f"📅 {day_text} {time_text}\n"
                    f"✂️ {_service_label(service)} with {_barber_label(barber)}\n\n"
                    f"📲 View booking:\n{result.get('link', '')}"
                )

            except Exception:
                return "Something went wrong booking that 😅 try again 👍"

    # Cancel intent
    if "cancel" in msg_lower:
        bookings = list_bookings(phone)
        if not bookings:
            return "You don’t have any upcoming bookings to cancel 👍"

        booking = bookings[0]
        start_dt = datetime.fromisoformat(booking["start"])
        day_text, time_text = _day_time_text(start_dt)

        session["pending_cancel"] = booking
        return (
            f"Got it 👍 Shall I cancel your {_service_label(booking['service'])} "
            f"with {_barber_label(booking['barber'])} on {day_text} {time_text}?"
        )

    # Reschedule / move intent
    if any(x in msg_lower for x in ["reschedule", "move", "change booking", "change appointment", "change time"]):
        bookings = list_bookings(phone)
        if not bookings:
            return "You don’t have any upcoming bookings to move 👍"

        booking = bookings[0]
        session["pending_reschedule"] = {"id": booking["id"]}
        return "No worries 👍 what new day and time would you like?"

    # If already in reschedule flow and user sends time
    if session.get("pending_reschedule") and "id" in session["pending_reschedule"]:
        parsed = _parse_datetime(msg, timezone_name)
        if parsed:
            session["pending_reschedule"]["new_when"] = parsed.isoformat()

            day_text, time_text = _day_time_text(parsed)
            return f"Perfect 👌 Shall I move it to {day_text} {time_text}?"
        return "What new day and time would you like? 📅"

    # Change service intent
    if any(x in msg_lower for x in ["change service", "switch service", "different service"]):
        return "No problem 👍 tell me the service you want instead, plus barber/time if those need changing too."

    # Extract memory from normal booking messages
    service = _friendly_service(msg)
    barber = _friendly_barber(msg)
    parsed = _parse_datetime(msg, timezone_name)

    if service:
        data["service"] = service
    if barber:
        data["barber"] = barber
    if parsed:
        data["when"] = parsed.isoformat()

    # If all booking details are ready, confirm first
    if all(k in data for k in ["service", "barber", "when"]):
        start_dt = datetime.fromisoformat(data["when"])
        day_text, time_text = _day_time_text(start_dt)

        session["pending_booking"] = {
            "service": data["service"],
            "barber": data["barber"],
            "when": data["when"],
        }

        return (
            f"Perfect 👌 Just to confirm — shall I book your "
            f"{_service_label(data['service'])} with {_barber_label(data['barber'])} "
            f"for {day_text} {time_text}?"
        )

    # Ask only for missing info
    missing_service = "service" not in data
    missing_barber = "barber" not in data
    missing_when = "when" not in data

    if not missing_service and not missing_barber and missing_when:
        return f"Nice one 👍 What day and time would you like your {_service_label(data['service'])} with {_barber_label(data['barber'])}? 📅"

    if not missing_service and missing_barber and not missing_when:
        return f"Got it 👍 who would you like for your {_service_label(data['service'])} — Jay or Mike?"

    if missing_service and not missing_barber and not missing_when:
        day_text, time_text = _day_time_text(datetime.fromisoformat(data["when"]))
        return f"Perfect 👍 what service would you like with {_barber_label(data['barber'])} on {day_text} {time_text}?"

    if not missing_service and missing_barber and missing_when:
        return f"Nice one 👍 who would you like for your {_service_label(data['service'])} — Jay or Mike? Also what day and time works for you? 📅"

    if missing_service and not missing_barber and missing_when:
        return f"Got you 👍 what would you like booked in with {_barber_label(data['barber'])}, and what day and time works for you? 📅"

    if missing_service and missing_barber and not missing_when:
        day_text, time_text = _day_time_text(datetime.fromisoformat(data["when"]))
        return f"Got it 👍 what service would you like, and would you prefer Jay or Mike, for {day_text} {time_text}?"

    return "What would you like to book? ✂️"