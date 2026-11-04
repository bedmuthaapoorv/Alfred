"""In-process async event bus.

Sensors `publish()` events; handlers `subscribe()` to topic patterns
(fnmatch-style, e.g. "gmail.*" or "*"). A single dispatcher task delivers
events in order. A failing handler is logged and does not stop the others.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from alfred.core.events import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]


@dataclass
class _Subscription:
    pattern: str
    handler: Handler
    name: str


class EventBus:
    def __init__(self) -> None:
        # Unbounded on purpose: handlers publish follow-up events from inside
        # the dispatcher, and a bounded queue could deadlock on itself.
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subs: list[_Subscription] = []

    def subscribe(self, pattern: str, handler: Handler, name: str | None = None) -> None:
        self._subs.append(_Subscription(pattern, handler, name or getattr(handler, "__qualname__", repr(handler))))

    async def publish(self, event: Event) -> None:
        await self._queue.put(event)

    async def run(self) -> None:
        """Dispatch forever; cancel the task to stop."""
        while True:
            event = await self._queue.get()
            try:
                await self._dispatch(event)
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        """Wait until every published event has been dispatched."""
        await self._queue.join()

    async def _dispatch(self, event: Event) -> None:
        for sub in self._subs:
            if not fnmatch.fnmatchcase(event.topic, sub.pattern):
                continue
            try:
                await sub.handler(event)
            except Exception:
                log.exception("handler %s failed on %s (%s)", sub.name, event.topic, event.id)
