import os
from datetime import datetime
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

# =========================
# CONFIG
# =========================
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))

SCOPES = ["https://www.googleapis.com/auth/calendar"]

service_account_info = eval(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

creds = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=SCOPES,
)

service = build("calendar", "v3", credentials=creds)

# =========================
# BARBERS
# =========================
BARBERS = {
    "mike": {"calendar_id": os.getenv("BARBER_MIKE_CALENDAR_ID")},
    "jay": {"calendar_id": os.getenv("BARBER_JAY_CALENDAR_ID")},
}

# =========================
# CREATE BOOKING
# =========================
def create_booking(
    calendar_id: str,
    customer_name: str,
    customer_phone: str,
    service_name: str,
    start_dt: datetime,
    end_dt: datetime,
    barber_name: str,
):
    event = {
        "summary": f"{service_name} - {customer_name}",
        "description": f"Customer: {customer_name} | Phone: {customer_phone}",
        "start": {
            "dateTime": start_dt.astimezone(TIMEZONE).isoformat(),
            "timeZone": str(TIMEZONE),
        },
        "end": {
            "dateTime": end_dt.astimezone(TIMEZONE).isoformat(),
            "timeZone": str(TIMEZONE),
        },
    }

    created = service.events().insert(
        calendarId=calendar_id,
        body=event
    ).execute()

    return {
        "id": created.get("id"),
        "link": created.get("htmlLink"),
    }

# =========================
# LIST BOOKINGS
# =========================
def list_upcoming(phone: str):
    events = []

    now = datetime.now(TIMEZONE).isoformat()

    for barber in BARBERS.values():
        calendar_id = barber["calendar_id"]

        result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for event in result.get("items", []):
            if phone in str(event.get("description", "")):
                events.append(event)

    return events

# =========================
# CANCEL BOOKING
# =========================
def list_upcoming(phone: str):
    events = service.events().list(
        calendarId=os.getenv("GOOGLE_CALENDAR_ID"),
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


def cancel_booking(event_id: str):
    try:
        service.events().delete(
            calendarId=os.getenv("GOOGLE_CALENDAR_ID"),
            eventId=event_id
        ).execute()
        return True
    except:
        return False