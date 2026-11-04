from email.message import EmailMessage

import pytest

from alfred.core.store import Store
from alfred.notify.email import EmailNotifier, RecipientNotAllowed, check_recipients
from tests.fakes import FakeGmail


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "a.db") as s:
        yield s


async def test_sends_only_to_owner(store):
    g = FakeGmail()
    n = EmailNotifier(g, store, "me@example.com")
    assert await n.send("brief", "Hello", "body")
    msg = g.sent[0]
    assert msg["To"] == "me@example.com" and msg["X-Alfred"] == "1"
    assert store.recent_outbox()[0]["status"] == "sent"


@pytest.mark.parametrize("to,cc,bcc", [
    ("other@example.com", None, None),
    ("me@example.com", "leak@example.com", None),
    ("me@example.com", None, "leak@example.com"),
    ("me@example.com, other@example.com", None, None),
])
def test_guard_rejects_any_other_recipient(to, cc, bcc):
    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    with pytest.raises(RecipientNotAllowed):
        check_recipients(msg, "me@example.com")


async def test_paused_does_not_send(store):
    g = FakeGmail()
    store.set_paused(True)
    assert not await EmailNotifier(g, store, "me@example.com").send("brief", "s", "b")
    assert g.sent == []
    assert store.recent_outbox()[0]["status"] == "skipped_paused"
