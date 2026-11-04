"""Gmail: a thin client over the API, and the sensor that turns new mail into events.

The client is synchronous (googleapiclient); the sensor runs it in a thread.
Only metadata and the snippet are read in Phase 1, which is enough for
triage. It keeps prompts small and gives injected text less room.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Protocol

from alfred.core.events import Event
from alfred.core.store import Store
from alfred.sensors.base import Publish

log = logging.getLogger(__name__)

_HEADERS = ["From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "X-Alfred"]
_SKIP_LABELS = {"SENT", "DRAFT", "SPAM", "TRASH"}
_SEEN_KEY = "gmail.seen_ids"
_SEEN_MAX = 5000


class HistoryExpired(Exception):
    """The stored historyId is too old; resync from a query."""


class GmailAPI(Protocol):
    def profile(self) -> dict[str, Any]: ...
    def history_since(self, history_id: str) -> tuple[list[str], str]: ...
    def search(self, query: str, limit: int) -> list[str]: ...
    def message_meta(self, message_id: str) -> dict[str, Any]: ...
    def send(self, message: EmailMessage) -> str: ...


class GoogleGmailAPI:
    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._svc = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def profile(self) -> dict[str, Any]:
        return self._svc.users().getProfile(userId="me").execute()

    def history_since(self, history_id: str) -> tuple[list[str], str]:
        from googleapiclient.errors import HttpError

        ids: list[str] = []
        latest = history_id
        token = None
        try:
            while True:
                resp = (
                    self._svc.users()
                    .history()
                    .list(userId="me", startHistoryId=history_id, historyTypes=["messageAdded"],
                          labelId="INBOX", pageToken=token)
                    .execute()
                )
                for h in resp.get("history", []):
                    for added in h.get("messagesAdded", []):
                        ids.append(added["message"]["id"])
                latest = resp.get("historyId", latest)
                token = resp.get("nextPageToken")
                if not token:
                    return ids, latest
        except HttpError as e:
            if e.resp.status == 404:
                raise HistoryExpired() from e
            raise

    def search(self, query: str, limit: int) -> list[str]:
        resp = self._svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        return [m["id"] for m in resp.get("messages", [])]

    def message_meta(self, message_id: str) -> dict[str, Any]:
        return (
            self._svc.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=_HEADERS)
            .execute()
        )

    def send(self, message: EmailMessage) -> str:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return self._svc.users().messages().send(userId="me", body={"raw": raw}).execute()["id"]


def normalize_message(meta: dict[str, Any]) -> dict[str, Any]:
    """Gmail API message (metadata format) -> compact event payload."""
    headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
    sender = getaddresses([headers.get("from", "")])
    date = None
    if headers.get("date"):
        try:
            date = parsedate_to_datetime(headers["date"]).isoformat()
        except (TypeError, ValueError):
            pass
    return {
        "message_id": meta["id"],
        "thread_id": meta.get("threadId"),
        "from_name": sender[0][0] if sender else "",
        "from_email": sender[0][1].lower() if sender else "",
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": date,
        "snippet": meta.get("snippet", ""),
        "labels": meta.get("labelIds", []),
        "is_bulk": "list-unsubscribe" in headers,
        "from_alfred": "x-alfred" in headers,
    }


class GmailSensor:
    """Polls Gmail history and publishes `gmail.message.received` for new inbox mail."""

    name = "gmail"

    def __init__(self, api: GmailAPI, store: Store, poll_seconds: float, backfill_query: str = "in:inbox newer_than:1d") -> None:
        self.api = api
        self.store = store
        self.poll_seconds = poll_seconds
        self.backfill_query = backfill_query

    async def run(self, publish: Publish) -> None:
        while True:
            try:
                await self.poll(publish)
                self.store.set_state("gmail.last_error", None)
            except Exception as e:
                log.warning("gmail poll failed: %s", e)
                self.store.set_state("gmail.last_error", f"{type(e).__name__}: {e}")
            await asyncio.sleep(self.poll_seconds)

    async def poll(self, publish: Publish) -> int:
        history_id = self.store.get_state("gmail.history_id")
        if history_id is None:
            return await self._resync(publish)
        try:
            ids, latest = await asyncio.to_thread(self.api.history_since, history_id)
        except HistoryExpired:
            log.info("gmail history expired; resyncing")
            return await self._resync(publish)
        n = await self._emit(ids, publish)
        self.store.set_state("gmail.history_id", latest)
        return n

    async def _resync(self, publish: Publish) -> int:
        profile = await asyncio.to_thread(self.api.profile)
        ids = await asyncio.to_thread(self.api.search, self.backfill_query, 50)
        n = await self._emit(list(reversed(ids)), publish)  # oldest first
        self.store.set_state("gmail.history_id", profile["historyId"])
        self.store.set_state("gmail.account", profile.get("emailAddress"))
        return n

    async def _emit(self, ids: list[str], publish: Publish) -> int:
        seen: list[str] = self.store.get_state(_SEEN_KEY, [])
        seen_set = set(seen)
        count = 0
        for mid in dict.fromkeys(ids):  # dedupe, keep order
            if mid in seen_set:
                continue
            meta = await asyncio.to_thread(self.api.message_meta, mid)
            seen.append(mid)
            seen_set.add(mid)
            if _SKIP_LABELS & set(meta.get("labelIds", [])):
                continue
            payload = normalize_message(meta)
            if payload["from_alfred"]:
                continue  # Alfred's own brief landing in the inbox
            await publish(Event(source="gmail", kind="message.received", payload=payload))
            count += 1
        self.store.set_state(_SEEN_KEY, seen[-_SEEN_MAX:])
        return count
