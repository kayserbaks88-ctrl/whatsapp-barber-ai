from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
from datetime import timedelta

SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_service():
    creds = service_account.Credentials.from_service_account_info(
        eval(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def list_bookings(phone):
    service = get_service()

    events = service.events().list(
        calendarId=os.environ["BARBER_JAY_CALENDAR_ID"],  # any calendar works if shared
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])

    results = []

    for e in events:
        if e.get("description") and phone in e["description"]:
            results.append({
                "id": e["id"],
                "start": e["start"]["dateTime"],
                "summary": e["summary"]
            })

    return results


def cancel_booking(event_id):
    service = get_service()
    service.events().delete(
        calendarId=os.environ["BARBER_JAY_CALENDAR_ID"],
        eventId=event_id
    ).execute()


def reschedule_booking(event_id, new_start, minutes):
    service = get_service()

    event = service.events().get(
        calendarId=os.environ["BARBER_JAY_CALENDAR_ID"],
        eventId=event_id
    ).execute()

    event["start"]["dateTime"] = new_start.isoformat()
    event["end"]["dateTime"] = (new_start + timedelta(minutes=minutes)).isoformat()

    updated = service.events().update(
        calendarId=os.environ["BARBER_JAY_CALENDAR_ID"],
        eventId=event_id,
        body=event
    ).execute()

    return updated.get("htmlLink")