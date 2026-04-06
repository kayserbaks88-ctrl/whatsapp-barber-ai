import os
from datetime import datetime, timedelta
from typing import Any

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


# =========================
# TOOL EXECUTION
# =========================
def _execute_tool(tool_name: str, args: dict, phone: str, profile_name: str | None) -> dict:
    try:
        # =========================
        # BOOK APPOINTMENT
        # =========================
        if tool_name == "book_appointment":
            barber = args["barber"]
            service = args["service"]
            start_iso = args.get("start_iso")

            if not start_iso:
                return {"ok": False, "error": "Missing start time"}

            start_dt = datetime.fromisoformat(start_iso)
            minutes = SERVICES[service]["minutes"]

            customer_name = args.get("customer_name") or profile_name or "Customer"

            print("BOOKING:", barber, service, start_dt)

            result = create_booking(
                phone=phone,
                service_name=service,
                start_dt=start_dt,
                minutes=minutes,
                name=customer_name,
                barber=barber,
            )

            print("BOOKED:", result)

            return {
                "ok": True,
                **result
            }

        # =========================
        # CHECK AVAILABILITY
        # =========================
        if tool_name == "check_availability":
            barber = args["barber"]
            service = args["service"]
            start_iso = args.get("start_iso")

            start_dt = datetime.fromisoformat(start_iso)
            minutes = SERVICES[service]["minutes"]
            end_dt = start_dt + timedelta(minutes=minutes)

            free = is_free(start_dt, end_dt, barber)

            return {
                "ok": True,
                "free": free,
                "barber": barber,
                "service": service,
                "start_iso": start_iso,
            }

        # =========================
        # LIST BOOKINGS
        # =========================
        if tool_name == "list_bookings":
            bookings = list_bookings(phone)
            return {"ok": True, "bookings": bookings}

        # =========================
        # CANCEL
        # =========================
        if tool_name == "cancel_booking":
            event_id = args["event_id"]
            success = cancel_booking(event_id)
            return {"ok": success}

        # =========================
        # RESCHEDULE
        # =========================
        if tool_name == "reschedule_booking":
            event_id = args["event_id"]
            new_start = datetime.fromisoformat(args["start_iso"])

            updated = reschedule_booking(event_id, new_start)

            return {
                "ok": True,
                **(updated or {})
            }

        return {"ok": False, "error": "Unknown tool"}

    except Exception as e:
        print("TOOL ERROR:", str(e))
        return {
            "ok": False,
            "error": str(e)
        }


# =========================
# MAIN AGENT
# =========================
def run_receptionist_agent(
    user_message: str,
    phone: str,
    profile_name: str | None,
    session: dict,
    business_name: str,
    timezone_name: str,
) -> str:

    customer_name = (profile_name or "").strip()

    # ===== SIMPLE INTENT LOGIC (CLEAN + RELIABLE) =====

    text = user_message.lower()

    # =========================
    # BOOKING FLOW
    # =========================
    if "book" in text or "haircut" in text or "beard" in text:

        # extract barber
        barber = "mike" if "mike" in text else "jay"

        # extract service
        if "beard" in text:
            service = "beard trim"
        elif "skin" in text:
            service = "skin fade"
        elif "kid" in text:
            service = "kids cut"
        else:
            service = "haircut"

        # simple time parsing
        import dateparser

        dt = dateparser.parse(
            user_message,
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": timezone_name,
                "RETURN_AS_TIMEZONE_AWARE": True,
            },
        )

        if not dt:
            return "What time would you like to book? 😊"

        # fix missing PM (VERY IMPORTANT)
        if "pm" not in text and dt.hour < 9:
            dt = dt.replace(hour=dt.hour + 12)

        minutes = SERVICES[service]["minutes"]

        free = is_free(dt, dt.replace(minute=dt.minute + minutes), barber)

        if not free:
            return "That time isn’t available 😕 Want another time?"

        # save session for confirmation
        session["pending"] = {
            "barber": barber,
            "service": service,
            "start": dt.isoformat(),
        }

        return f"{dt.strftime('%A %I:%M %p')}, {barber.title()} is available for a {service}. Would you like to book it? ✂️"

    # =========================
    # CONFIRM BOOKING
    # =========================
    if "yes" in text and session.get("pending"):

        data = session.pop("pending")

        result = _execute_tool(
            "book_appointment",
            {
                "barber": data["barber"],
                "service": data["service"],
                "start_iso": data["start"],
                "customer_name": customer_name,
            },
            phone,
            profile_name,
        )

        if not result.get("ok"):
            return "Sorry, something went wrong booking that 😕 Try again."

        when = datetime.fromisoformat(result["start"])

        return f"""Nice one 👌 you're booked in!

📅 {when.strftime('%A %d %B')}
⏰ {when.strftime('%I:%M %p')}
💈 {result['barber'].title()} – {result['service'].title()}

See you soon ✂️"""

    # =========================
    # FALLBACK
    # =========================
    return f"Hey {customer_name or 'there'} 👋 What can I book for you today?"