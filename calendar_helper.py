import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

creds = service_account.Credentials.from_service_account_file(
    GOOGLE_CREDENTIALS_FILE,
    scopes=SCOPES,
)

service = build("calendar", "v3", credentials=creds)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(TIMEZONE).isoformat()


def is_free(calendar_id: str, start_dt: datetime, end_dt: datetime) -> bool:
    events_result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=_to_iso(start_dt),
            timeMax=_to_iso(end_dt),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = events_result.get("items", [])
    return len(events) == 0


def get_available_slots(
    calendar_id: str,
    duration_minutes: int,
    days_ahead: int = 7,
    start_hour: int = 9,
    end_hour: int = 18,
    slot_step_minutes: int = 15,
    working_days: list[int] | None = None,
    limit: int = 5,
) -> list[datetime]:
    now = datetime.now(TIMEZONE)
    slots: list[datetime] = []

    for day_offset in range(days_ahead + 1):
        current_day = (now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        if working_days is not None and current_day.weekday() not in working_days:
            continue

        day_start = current_day.replace(hour=start_hour, minute=0)
        day_end = current_day.replace(hour=end_hour, minute=0)

        slot = day_start
        while slot + timedelta(minutes=duration_minutes) <= day_end:
            end_slot = slot + timedelta(minutes=duration_minutes)

            if slot > now and is_free(calendar_id, slot, end_slot):
                slots.append(slot)
                if len(slots) >= limit:
                    return slots

            slot += timedelta(minutes=slot_step_minutes)

    return slots


def create_booking(
    calendar_id: str,
    customer_name: str,
    customer_phone: str,
    service_name: str,
    start_dt: datetime,
    end_dt: datetime,
    barber_name: str,
) -> dict:
    event = {
        "summary": f"{service_name} - {customer_name}",
        "description": (
            f"Customer: {customer_name}\n"
            f"Phone: {customer_phone}\n"
            f"Service: {service_name}\n"
            f"Barber: {barber_name}"
        ),
        "start": {
            "dateTime": start_dt.astimezone(TIMEZONE).isoformat(),
            "timeZone": str(TIMEZONE),
        },
        "end": {
            "dateTime": end_dt.astimezone(TIMEZONE).isoformat(),
            "timeZone": str(TIMEZONE),
        },
    }

    created_event = (
        service.events()
        .insert(calendarId=calendar_id, body=event)
        .execute()
    )
    return created_event