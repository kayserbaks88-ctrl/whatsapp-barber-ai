import os
from datetime import timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

creds = service_account.Credentials.from_service_account_info(
    eval(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")),
    scopes=SCOPES
)

service = build("calendar", "v3", credentials=creds)

BARBERS = {
    "jay": {
        "name": "Jay",
        "calendar_id": os.getenv("BARBER_JAY_CALENDAR_ID"),
    },
    "mike": {
        "name": "Mike",
        "calendar_id": os.getenv("BARBER_MIKE_CALENDAR_ID"),
    },
}

def is_free(start_dt, end_dt, barber):
    events = service.events().list(
        calendarId=barber["calendar_id"],
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,
    ).execute().get("items", [])

    return len(events) == 0


def create_booking(phone, service_name, start_dt, minutes, name, barber):
    end_dt = start_dt + timedelta(minutes=minutes)

    event = {
        "summary": service_name,
        "description": f"{name} ({phone})",
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }

    created = service.events().insert(
        calendarId=barber["calendar_id"],
        body=event
    ).execute()

    return {
        "id": created["id"],
        "link": created.get("htmlLink", "")
    }