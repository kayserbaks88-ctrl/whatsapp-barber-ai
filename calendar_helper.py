import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

SCOPES = ["https://www.googleapis.com/auth/calendar"]

service_account_info = eval(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

creds = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=SCOPES,
)

service = build("calendar", "v3", credentials=creds)

# ======================
# BARBERS CONFIG
# ======================
BARBERS = {
    "mike": {
        "name": "Mike",
        "calendar_id": os.getenv("MIKE_CALENDAR_ID"),
    },
    "jay": {
        "name": "Jay",
        "calendar_id": os.getenv("JAY_CALENDAR_ID"),
    },
}


# ==============================
# CHECK AVAILABILITY
# ==============================
def is_free(start_dt, end_dt):
    events = service.events().list(
        calendarId=list(BARBERS.values())[0]["calendar_id"],
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,
    ).execute().get("items", [])

    return len(events) == 0


# ==============================
# CREATE BOOKING
# ==============================
def create_booking(phone, service_name, start_dt, minutes=30, name="Guest", barber=None):
    end_dt = start_dt + timedelta(minutes=minutes)

    calendar_id = barber["calendar_id"]

    event = {
        "summary": service_name,
        "description": f"Customer: {name} | Phone: {phone}",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": str(TIMEZONE)},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": str(TIMEZONE)},
    }

    created_event = service.events().insert(
        calendarId=calendar_id,
        body=event
    ).execute()

    return {
        "id": created_event["id"],
        "link": created_event.get("htmlLink")
    }


# ==============================
# LIST BOOKINGS
# ==============================
def list_upcoming(phone: str):
    events = service.events().list(
        calendarId=list(BARBERS.values())[0]["calendar_id"],
        maxResults=10,
        singleEvents=True,
        orderBy="startTime"
    ).execute().get("items", [])

    results = []

    for e in events:
        if e.get("description") and phone in e["description"]:
            results.append({
                "id": e["id"],
                "start": e["start"]["dateTime"],
                "service": e["summary"],
            })

    return results


# ==============================
# CANCEL BOOKING
# ==============================
def cancel_booking(event_id: str):
    for barber in BARBERS.values():
        try:
            service.events().delete(
                calendarId=barber["calendar_id"],
                eventId=event_id
            ).execute()
            return True
        except:
            continue

    return False