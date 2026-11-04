"""Daily routines: fires `schedule.<name>` once a day at a local time.

If Alfred was down at the scheduled time it still fires on startup, as long as
it is within `grace` of the slot. The morning brief should arrive late rather
than not at all, but not at 6pm.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from alfred.core.events import Event
from alfred.core.store import Store
from alfred.sensors.base import Publish


class DailySchedule:
    def __init__(self, routine: str, at: time, tz: ZoneInfo, store: Store,
                 grace: timedelta = timedelta(hours=3), check_seconds: float = 30.0) -> None:
        self.routine = routine
        self.name = f"schedule:{routine}"
        self.at = at
        self.tz = tz
        self.store = store
        self.grace = grace
        self.check_seconds = check_seconds

    def due(self, now: datetime) -> str | None:
        """Return today's local date string if the routine should fire now."""
        local = now.astimezone(self.tz)
        slot = datetime.combine(local.date(), self.at, tzinfo=self.tz)
        today = local.date().isoformat()
        if self.store.get_state(f"schedule.{self.routine}.last") == today:
            return None
        if slot <= local <= slot + self.grace:
            return today
        return None

    async def tick(self, publish: Publish, now: datetime | None = None) -> bool:
        today = self.due(now or datetime.now(timezone.utc))
        if today is None:
            return False
        # Mark first: a crashing handler must not re-fire every 30s all morning.
        self.store.set_state(f"schedule.{self.routine}.last", today)
        await publish(Event(source="schedule", kind=self.routine, payload={"date": today}))
        return True

    async def run(self, publish: Publish) -> None:
        while True:
            await self.tick(publish)
            await asyncio.sleep(self.check_seconds)
