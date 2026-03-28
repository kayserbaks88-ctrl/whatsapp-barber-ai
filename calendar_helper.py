import os
from datetime import datetime, timedelta
from typing import Dict, Any, List

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
CALENDAR_IDS = {
    "jay": os.getenv("BARBER_JAY_CALENDAR_ID"),
    "mike": os.getenv("BARBER_MIKE_CALENDAR_ID"),
}

def get_service():
    creds = service_account.Credentials.from_service_account_info(
        eval(GOOGLE_SERVICE_ACCOUNT_JSON), scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

BARBERS = {
    "jay": {"name": "Jay", "calendar_id": CALENDAR_IDS["jay"]},
    "mike": {"name": "Mike", "calendar_id": CALENDAR_IDS["mike"]},
}

# =========================
# CHECK AVAILABILITY
# =========================
def is_free(start_dt: datetime, end_dt: datetime, barber: Dict) -> bool:
    service = get_service()

    events = (
        service.events()
        .list(
            calendarId=barber["calendar_id"],
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
        )
        .execute()
        .get("items", [])
    )

    return len(events) == 0


# =========================
# CREATE BOOKING
# =========================
def create_booking(
    phone: str,
    service_name: str,
    start_dt: datetime,
    minutes: int,
    name: str,
    barber: Dict,
) -> Dict[str, Any]:

    service = get_service()
    end_dt = start_dt + timedelta(minutes=minutes)

    event = {
        "summary": f"{service_name} - {name}",
        "description": f"Phone: {phone}",
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }

    created = (
        service.events()
        .insert(calendarId=barber["calendar_id"], body=event)
        .execute()
    )

    return {"id": created["id"], "link": created.get("htmlLink")}


# =========================
# LIST BOOKINGS
# =========================
def list_bookings(phone: str) -> List[Dict]:
    service = get_service()

    results = []

    for barber in BARBERS.values():
        events = (
            service.events()
            .list(
                calendarId=barber["calendar_id"],
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )

        for e in events:
            if phone in e.get("description", ""):
                results.append(
                    {
                        "id": e["id"],
                        "summary": e["summary"],
                        "start": e["start"]["dateTime"],
                        "calendar_id": barber["calendar_id"],
                    }
                )

    return results


# =========================
# CANCEL BOOKING
# =========================
def cancel_booking(event_id: str):
    service = get_service()

    for barber in BARBERS.values():
        try:
            service.events().delete(
                calendarId=barber["calendar_id"], eventId=event_id
            ).execute()
            return
        except:
            continue


# =========================
# RESCHEDULE BOOKING
# =========================
def reschedule_booking(event_id: str, new_start: datetime, minutes: int):
    service = get_service()
    new_end = new_start + timedelta(minutes=minutes)

    for barber in BARBERS.values():
        try:
            event = service.events().get(
                calendarId=barber["calendar_id"], eventId=event_id
            ).execute()

            event["start"]["dateTime"] = new_start.isoformat()
            event["end"]["dateTime"] = new_end.isoformat()

            updated = service.events().update(
                calendarId=barber["calendar_id"],
                eventId=event_id,
                body=event,
            ).execute()

            return updated.get("htmlLink")

        except:
            continue