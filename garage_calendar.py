import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

SERVICES = {
    "mot": {"label": "MOT", "minutes": 60},
    "full service": {"label": "Full Service", "minutes": 120},
    "diagnostic": {"label": "Diagnostic Check", "minutes": 45},
    "oil change": {"label": "Oil Change", "minutes": 30},
}

MECHANICS = {
    "garage": {
        "key": "garage",
        "name": "Garage Team",
        "calendar_id": os.getenv("GARAGE_CALENDAR_ID", ""),
    },
}


def _get_service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise ValueError("Missing GOOGLE_SERVICE_ACCOUNT_JSON")

    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds)


def _calendar_id_for_mechanic(mechanic: str) -> str:
    mechanic = (mechanic or "").strip().lower()

    if mechanic not in MECHANICS:
        raise ValueError(f"Unknown mechanic: {mechanic}")

    calendar_id = MECHANICS[mechanic]["calendar_id"]

    if not calendar_id:
        raise ValueError(f"Missing calendar id for mechanic: {mechanic}")

    return calendar_id


def _event_end(start_dt: datetime, minutes: int) -> datetime:
    return start_dt + timedelta(minutes=minutes)


def is_free(start_dt: datetime, end_dt: datetime, mechanic: str, ignore_event_id: str | None = None) -> bool:
    service = _get_service()
    calendar_id = calendar_id = _calendar_id_for_mechanic(mechanic)

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=start_dt.astimezone(TIMEZONE).isoformat(),
        timeMax=end_dt.astimezone(TIMEZONE).isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    items = events_result.get("items", [])
    for event in items:
        if ignore_event_id and event.get("id") == ignore_event_id:
            continue
        if event.get("status") == "cancelled":
            continue
        return False

    return True


def create_booking(phone: str, service_name: str, start_dt: datetime, minutes: int, name: str, mechanic: str) -> dict:
    service = _get_service()
    calendar_id = _calendar_id_for_mechanic(mechanic)
    end_dt = _event_end(start_dt, minutes)

    if not is_free(start_dt, end_dt, mechanic):
        raise ValueError("That slot is not available")

    service_label = SERVICES.get(service_name, {}).get("label", service_name.title())
    mechanic_name = MECHANICS[mechanic]["name"]

    event = {
        "summary": f"{service_label} - {name}",
        "description": (
            f"Customer: {name}\n"
            f"Phone: {phone}\n"
            f"Service: {service_label}\n"
            f"mechanic: {mechanic_name}"
        ),
        "start": {
            "dateTime": start_dt.astimezone(TIMEZONE).isoformat(),
            "timeZone": str(TIMEZONE),
        },
        "end": {
            "dateTime": end_dt.astimezone(TIMEZONE).isoformat(),
            "timeZone": str(TIMEZONE),
        },
        "extendedProperties": {
            "private": {
                "phone": phone,
                "mechanic": mechanic,
                "service": service_name,
                "customer_name": name,
            }
        },
    }
    print("RAW INPUT:", start_dt)
    
    print("FINAL BOOKING TIME:", start_dt)
    print("TIMEZONE:", start_dt.tzinfo)
    
    created = service.events().insert(calendarId=calendar_id, body=event).execute()

    return {
        "id": created.get("id"),
        "link": created.get("htmlLink"),
        "calendar_id": calendar_id,
        "mechanic": mechanic,
        "service": service_name,
        "customer_name": name,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
    }


def list_bookings(phone: str) -> list[dict]:
    service = _get_service()
    now = datetime.now(TIMEZONE).isoformat()
    found = []

    for mechanic_key, mechanic_data in MECHANICS.items():
        calendar_id = mechanic_data["calendar_id"]
        if not calendar_id:
            continue

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute()

        items = events_result.get("items", [])
        for event in items:
            if event.get("status") == "cancelled":
                continue

            private = ((event.get("extendedProperties") or {}).get("private") or {})
            description = event.get("description") or ""
            event_phone = private.get("phone") or ""

            if phone != event_phone and phone not in description:
                continue

            found.append(
                {
                    "id": event.get("id"),
                    "summary": event.get("summary"),
                    "start": ((event.get("start") or {}).get("dateTime") or ""),
                    "end": ((event.get("end") or {}).get("dateTime") or ""),
                    "link": event.get("htmlLink"),
                    "mechanic": private.get("mechanic", mechanic_key),
                    "service": private.get("service"),
                    "customer_name": private.get("customer_name"),
                    "calendar_id": calendar_id,
                }
            )

    found.sort(key=lambda x: x.get("start", ""))
    return found


def cancel_booking(event_id: str) -> bool:
    service = _get_service()

    for mechanic_key, mechanic_data in MECHANICS.items():
        calendar_id = mechanic_data["calendar_id"]
        if not calendar_id:
            continue

        try:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return True
        except Exception:
            continue

    return False


def reschedule_booking(event_id: str, new_start: datetime) -> dict | None:
    service = _get_service()

    for mechanic_key, mechanic_data in MECHANICS.items():
        calendar_id = mechanic_data["calendar_id"]
        if not calendar_id:
            continue

        try:
            # 🔥 GET the existing event FIRST
            event = service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

            private = (event.get("extendedProperties", {}) or {}).get("private", {})

            service_name = private.get("service", "haircut")
            mechanic = private.get("mechanic", mechanic_key)

            minutes = SERVICES.get(service_name, {}).get("minutes", 30)

            # 🔥 NEW TIMES
            start_dt = new_start.astimezone(TIMEZONE)
            end_dt = start_dt + timedelta(minutes=minutes)

            # 🔥 CHECK availability (VERY IMPORTANT)
            if not is_free(start_dt, end_dt, mechanic, ignore_event_id=event_id):
                return None

            # 🔥 UPDATE event times
            event["start"]["dateTime"] = start_dt.isoformat()
            event["end"]["dateTime"] = end_dt.isoformat()

            # 🔥 KEEP CLEAN summary + description
            service_label = SERVICES.get(service_name, {}).get("label", service_name.title())
            mechanic_name = MECHANICS.get(mechanic, {}).get("name", mechanic.title())

            event["summary"] = f"{service_label} - {private.get('customer_name', '')}"
            event["description"] = (
                f"Customer: {private.get('customer_name')}\n"
                f"Phone: {private.get('phone')}\n"
                f"Service: {service_label}\n"
                f"mechanic: {mechanic_name}"
            )

            updated = service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event
            ).execute()

            return {
                "id": updated.get("id"),
                "link": updated.get("htmlLink"),
                "calendar_id": calendar_id,
                "mechanic": mechanic,
                "service": service_name,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            }

        except Exception as e:
            continue

    return None