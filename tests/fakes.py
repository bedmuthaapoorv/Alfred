"""Fake Google and Anthropic clients for tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from alfred.google.gmail import HistoryExpired


def gmail_meta(mid: str, subject: str = "Hi", sender: str = "Sam <sam@example.com>", labels=("INBOX",), extra_headers=()) -> dict:
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject},
               {"name": "To", "value": "me@example.com"}, {"name": "Date", "value": "Wed, 4 Nov 2026 08:00:00 +0000"}]
    headers += [{"name": k, "value": v} for k, v in extra_headers]
    return {"id": mid, "threadId": "t" + mid, "snippet": f"snippet {mid}", "labelIds": list(labels), "payload": {"headers": headers}}


class FakeGmail:
    def __init__(self) -> None:
        self.messages: dict[str, dict] = {}
        self.history_id = "100"
        self.pending: list[str] = []
        self.expired = False
        self.inbox_query_ids: list[str] = []
        self.sent: list[Any] = []

    def profile(self) -> dict:
        return {"historyId": self.history_id, "emailAddress": "me@example.com"}

    def history_since(self, history_id: str):
        if self.expired:
            raise HistoryExpired()
        ids, self.pending = self.pending, []
        self.history_id = str(int(self.history_id) + 1)
        return ids, self.history_id

    def search(self, query: str, limit: int):
        return self.inbox_query_ids[:limit]

    def message_meta(self, mid: str) -> dict:
        return self.messages[mid]

    def send(self, message) -> str:
        self.sent.append(message)
        return f"sent{len(self.sent)}"


class FakeCalendar:
    def __init__(self) -> None:
        self.between: list[dict] = []
        self.updated: list[dict] = []

    def events_between(self, start, end):
        return list(self.between)

    def events_updated_since(self, updated_min, time_min):
        return list(self.updated)


def cal_event(eid: str, start: str, end: str, summary: str = "Sync", me: str | None = "accepted", updated: str = "u1", **kw) -> dict:
    ev = {"id": eid, "summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}, "updated": updated, "status": "confirmed"}
    if me:
        ev["attendees"] = [{"email": "me@example.com", "self": True, "responseStatus": me}, {"email": "x@example.com"}]
    ev.update(kw)
    return ev


class FakeAnthropic:
    """Mimics client.messages.create; returns queued replies (dict -> JSON text, str -> text, Exception -> raise)."""

    def __init__(self, replies: list[Any], input_tokens: int = 1000, output_tokens: int = 200, stop_reason: str = "end_turn") -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                                     cache_read_input_tokens=0, cache_creation_input_tokens=0)
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text = json.dumps(reply) if isinstance(reply, dict) else reply
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], usage=self.usage,
                               stop_reason=self.stop_reason, _request_id="req_test")
