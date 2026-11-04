"""Heartbeat: wakes Alfred up on a fixed interval with no outside trigger.

This is the root of proactivity. Later phases hang "anything due soon?
anything stale? any follow-ups owed?" checks off `heartbeat.tick`.
"""

from __future__ import annotations

import asyncio

from alfred.core.events import Event
from alfred.sensors.base import Publish


class HeartbeatSensor:
    name = "heartbeat"

    def __init__(self, interval_seconds: float) -> None:
        self.interval = interval_seconds

    async def run(self, publish: Publish) -> None:
        loop = asyncio.get_running_loop()
        start = loop.time()
        beat = 0
        while True:
            beat += 1
            await publish(Event(source=self.name, kind="tick", payload={"beat": beat, "interval": self.interval}))
            # Schedule against the start time so ticks don't drift.
            await asyncio.sleep(max(0.0, start + beat * self.interval - loop.time()))
