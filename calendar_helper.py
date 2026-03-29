import os
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
CALENDAR_IDS = {
    "jay": os.getenv("BARBER_JAY_CALENDAR_ID"),
    "mike": os.getenv("BARBER_MIKE_CALENDAR_ID"),
}

BARBERS = {
    "jay": {"key": "jay", "name": "Jay", "calendar_id": CALENDAR_IDS["jay"]},
    "mike": {"key": "mike", "name": "Mike", "calendar_id": CALENDAR_IDS["mike"]},
}


def get_service():
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_SERVICE_ACCOUNT_JSON),
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


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
        "description": f"Phone: {phone}\nBarber: {barber['name']}",
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
        "extendedProperties": {
            "private": {
                "phone": phone,
                "barber": barber["key"],
                "service": service_name,
                "customer_name": name,
            }
        },
    }

    created = (
        service.events()
        .insert(calendarId=barber["calendar_id"], body=event)
        .execute()
    )

    return {
        "id": created["id"],
        "link": created.get("htmlLink"),
        "calendar_id": barber["calendar_id"],
    }


def list_bookings(phone: str) -> List[Dict]:
    service = get_service()
    now = datetime.utcnow().isoformat() + "Z"

    results = []

    for barber in BARBERS.values():
        events = (
            service.events()
            .list(
                calendarId=barber["calendar_id"],
                timeMin=now,
                maxResults=20,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )

        for e in events:
            private = e.get("extendedProperties", {}).get("private", {})
            description = e.get("description", "")

            if private.get("phone") == phone or phone in description:
                start_value = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date")
                end_value = e.get("end", {}).get("dateTime") or e.get("end", {}).get("date")

                results.append(
                    {
                        "id": e["id"],
                        "summary": e.get("summary", ""),
                        "start": start_value,
                        "end": end_value,
                        "calendar_id": barber["calendar_id"],
                        "barber_name": barber["name"],
                    }
                )

    results.sort(key=lambda x: x.get("start", ""))
    return results


def cancel_booking(event_id: str) -> bool:
    service = get_service()

    for barber in BARBERS.values():
        try:
            service.events().delete(
                calendarId=barber["calendar_id"],
                eventId=event_id,
            ).execute()
            return True
        except Exception:
            continue

    return False


def _get_event_duration_minutes(event: Dict) -> int:
    try:
        start_str = event["start"]["dateTime"]
        end_str = event["end"]["dateTime"]
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)
        return int((end_dt - start_dt).total_seconds() // 60)
    except Exception:
        return 30


def reschedule_booking(event_id: str, new_start: datetime) -> Optional[str]:
    service = get_service()

    for barber in BARBERS.values():
        try:
            event = service.events().get(
                calendarId=barber["calendar_id"],
                eventId=event_id,
            ).execute()

            minutes = _get_event_duration_minutes(event)
            new_end = new_start + timedelta(minutes=minutes)

            clash = (
                service.events()
                .list(
                    calendarId=barber["calendar_id"],
                    timeMin=new_start.isoformat(),
                    timeMax=new_end.isoformat(),
                    singleEvents=True,
                )
                .execute()
                .get("items", [])
            )

            clash = [e for e in clash if e.get("id") != event_id]
            if clash:
                return None

            event["start"]["dateTime"] = new_start.isoformat()
            event["end"]["dateTime"] = new_end.isoformat()

            updated = service.events().update(
                calendarId=barber["calendar_id"],
                eventId=event_id,
                body=event,
            ).execute()

            return updated.get("htmlLink")

        except Exception:
            continue

    return None