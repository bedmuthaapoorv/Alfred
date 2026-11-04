"""Sensor interface. A sensor turns the outside world into Events."""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from alfred.core.events import Event

Publish = Callable[[Event], Awaitable[None]]


class Sensor(Protocol):
    name: str

    async def run(self, publish: Publish) -> None:
        """Run until cancelled, publishing events as they happen."""
        ...
