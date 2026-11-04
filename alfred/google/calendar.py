"""Google Calendar: client, and the sensor for changes and upcoming events.

Emits:
  calendar.event.changed    an event was created/updated (invites included)
  calendar.event.cancelled  an event was cancelled/deleted
  calendar.event.upcoming   an event starts within `upcoming_lead_minutes`
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from alfred.core.events import Event
from alfred.core.store import Store
from alfred.sensors.base import Publish

log = logging.getLogger(__name__)

_ANNOUNCED_KEY = "calendar.announced"


class CalendarAPI(Protocol):
    def events_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]: ...
    def events_updated_since(self, updated_min: datetime, time_min: datetime) -> list[dict[str, Any]]: ...


class GoogleCalendarAPI:
    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._svc = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _list(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = None
        while True:
            resp = self._svc.events().list(calendarId="primary", pageToken=token, **kwargs).execute()
            items.extend(resp.get("items", []))
            token = resp.get("nextPageToken")
            if not token:
                return items

    def events_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._list(timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy="startTime")

    def events_updated_since(self, updated_min: datetime, time_min: datetime) -> list[dict[str, Any]]:
        return self._list(
            updatedMin=updated_min.isoformat(), timeMin=time_min.isoformat(), singleEvents=True, showDeleted=True
        )


def _parse_when(when: dict[str, Any], tz: ZoneInfo) -> tuple[datetime, bool]:
    if "dateTime" in when:
        return datetime.fromisoformat(when["dateTime"]), False
    d = date.fromisoformat(when["date"])
    return datetime(d.year, d.month, d.day, tzinfo=tz), True


def normalize_event(ev: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    start, all_day = _parse_when(ev.get("start", {"date": "1970-01-01"}), tz)
    end, _ = _parse_when(ev.get("end", ev.get("start", {"date": "1970-01-01"})), tz)
    attendees = ev.get("attendees", [])
    me = next((a for a in attendees if a.get("self")), None)
    return {
        "event_id": ev["id"],
        "summary": ev.get("summary", "(no title)"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": all_day,
        "location": ev.get("location", ""),
        "description": (ev.get("description") or "")[:500],
        "organizer": ev.get("organizer", {}).get("email", ""),
        "attendees": [{"email": a.get("email", ""), "response": a.get("responseStatus", "")} for a in attendees][:25],
        "my_response": me.get("responseStatus") if me else None,
        "status": ev.get("status", "confirmed"),
        "meet_link": ev.get("hangoutLink", ""),
        "html_link": ev.get("htmlLink", ""),
    }


class CalendarSensor:
    name = "calendar"

    def __init__(self, api: CalendarAPI, store: Store, poll_seconds: float, lead_minutes: int, tz: ZoneInfo) -> None:
        self.api = api
        self.store = store
        self.poll_seconds = poll_seconds
        self.lead = timedelta(minutes=lead_minutes)
        self.tz = tz

    async def run(self, publish: Publish) -> None:
        while True:
            try:
                await self.poll(publish)
                self.store.set_state("calendar.last_error", None)
            except Exception as e:
                log.warning("calendar poll failed: %s", e)
                self.store.set_state("calendar.last_error", f"{type(e).__name__}: {e}")
            await asyncio.sleep(self.poll_seconds)

    async def poll(self, publish: Publish, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        await self._changes(publish, now)
        await self._upcoming(publish, now)

    async def _changes(self, publish: Publish, now: datetime) -> None:
        cursor = self.store.get_state("calendar.updated_cursor")
        if cursor is None:
            # First run: start watching from now; the brief covers what already exists.
            self.store.set_state("calendar.updated_cursor", now.isoformat())
            return
        # Overlap by a minute so clock skew can't drop an update; dedupe on `updated`.
        since = datetime.fromisoformat(cursor) - timedelta(minutes=1)
        items = await asyncio.to_thread(self.api.events_updated_since, since, now - timedelta(days=1))
        versions: dict[str, str] = self.store.get_state("calendar.versions", {})
        for ev in items:
            version = ev.get("updated", "")
            if versions.get(ev["id"]) == version:
                continue
            versions[ev["id"]] = version
            kind = "event.cancelled" if ev.get("status") == "cancelled" else "event.changed"
            payload = {"event_id": ev["id"], "summary": ev.get("summary", "")} if kind == "event.cancelled" else normalize_event(ev, self.tz)
            await publish(Event(source="calendar", kind=kind, payload=payload))
        if len(versions) > 2000:
            versions = dict(list(versions.items())[-1000:])
        self.store.set_state("calendar.versions", versions)
        self.store.set_state("calendar.updated_cursor", now.isoformat())

    async def _upcoming(self, publish: Publish, now: datetime) -> None:
        items = await asyncio.to_thread(self.api.events_between, now, now + self.lead)
        announced: dict[str, str] = self.store.get_state(_ANNOUNCED_KEY, {})
        for ev in items:
            if ev.get("status") == "cancelled" or "dateTime" not in ev.get("start", {}):
                continue  # all-day events aren't "about to start"
            norm = normalize_event(ev, self.tz)
            if norm["my_response"] == "declined":
                continue
            key = f"{ev['id']}@{norm['start']}"
            if key in announced:
                continue
            announced[key] = now.isoformat()
            await publish(Event(source="calendar", kind="event.upcoming", payload=norm))
        cutoff = (now - timedelta(days=2)).isoformat()
        self.store.set_state(_ANNOUNCED_KEY, {k: v for k, v in announced.items() if v >= cutoff})
