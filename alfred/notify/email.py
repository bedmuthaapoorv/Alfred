"""Email to the owner, and only the owner.

Google's `gmail.send` scope can't be limited to one recipient, so the limit is
enforced here. `send()` has no recipient parameter, and `check_recipients`
re-validates the finished message before it leaves.
"""

from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage
from email.utils import getaddresses

from alfred.core.store import Store
from alfred.google.gmail import GmailAPI

log = logging.getLogger(__name__)


class RecipientNotAllowed(RuntimeError):
    pass


def check_recipients(msg: EmailMessage, owner: str) -> None:
    fields = [msg.get_all(h, []) for h in ("To", "Cc", "Bcc")]
    addrs = [a.lower() for _, a in getaddresses([v for f in fields for v in f])]
    if addrs != [owner.lower()]:
        raise RecipientNotAllowed(f"Alfred may only email {owner}; message addressed to {addrs}")


class EmailNotifier:
    def __init__(self, gmail: GmailAPI, store: Store, owner: str) -> None:
        if not owner:
            raise ValueError("notify.email is not set")
        self.gmail = gmail
        self.store = store
        self.owner = owner

    def build(self, subject: str, text: str, html: str | None = None) -> EmailMessage:
        msg = EmailMessage()
        msg["To"] = self.owner
        msg["From"] = self.owner
        msg["Subject"] = subject
        msg["X-Alfred"] = "1"  # lets the Gmail sensor and filters recognise Alfred's own mail
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        return msg

    async def send(self, kind: str, subject: str, text: str, html: str | None = None) -> bool:
        if self.store.is_paused():
            log.info("paused: not sending %s %r", kind, subject)
            self.store.record_outbox(kind=kind, recipient=self.owner, subject=subject, status="skipped_paused")
            return False
        msg = self.build(subject, text, html)
        check_recipients(msg, self.owner)
        try:
            gmail_id = await asyncio.to_thread(self.gmail.send, msg)
        except Exception as e:
            self.store.record_outbox(kind=kind, recipient=self.owner, subject=subject, status="failed", detail=repr(e)[:500])
            raise
        self.store.record_outbox(kind=kind, recipient=self.owner, subject=subject, status="sent", detail=gmail_id)
        return True
