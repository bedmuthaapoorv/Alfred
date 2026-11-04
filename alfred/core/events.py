"""The Event: the single shape every sensor produces."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Event:
    source: str  # which sensor produced it, e.g. "heartbeat", "gmail"
    kind: str  # what happened, e.g. "tick", "message.received"
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    received_at: datetime = field(default_factory=utcnow)

    @property
    def topic(self) -> str:
        """`source.kind`, the string subscribers match against."""
        return f"{self.source}.{self.kind}"
