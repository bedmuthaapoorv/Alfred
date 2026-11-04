import pytest

from alfred.core.store import Store
from alfred.google.gmail import GmailSensor, normalize_message
from tests.fakes import FakeGmail, gmail_meta


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "a.db") as s:
        yield s


class Sink:
    def __init__(self):
        self.events = []

    async def __call__(self, e):
        self.events.append(e)


async def test_first_poll_backfills_then_follows_history(store):
    g = FakeGmail()
    for m in ("m1", "m2", "m3"):
        g.messages[m] = gmail_meta(m)
    g.inbox_query_ids = ["m2", "m1"]  # API returns newest first
    sink = Sink()
    sensor = GmailSensor(g, store, 60)

    assert await sensor.poll(sink) == 2
    assert [e.payload["message_id"] for e in sink.events] == ["m1", "m2"]
    assert store.get_state("gmail.history_id") == "100"

    g.pending = ["m3", "m2"]  # m2 already seen
    assert await sensor.poll(sink) == 1
    assert sink.events[-1].topic == "gmail.message.received"
    assert sink.events[-1].payload["message_id"] == "m3"


async def test_skips_sent_spam_and_alfreds_own_mail(store):
    g = FakeGmail()
    g.messages = {
        "a": gmail_meta("a", labels=("INBOX", "SENT")),
        "b": gmail_meta("b", labels=("SPAM",)),
        "c": gmail_meta("c", extra_headers=[("X-Alfred", "1")]),
        "d": gmail_meta("d"),
    }
    store.set_state("gmail.history_id", "1")
    g.pending = ["a", "b", "c", "d"]
    sink = Sink()
    assert await GmailSensor(g, store, 60).poll(sink) == 1
    assert sink.events[0].payload["message_id"] == "d"


async def test_expired_history_resyncs(store):
    g = FakeGmail()
    g.messages["m9"] = gmail_meta("m9")
    g.inbox_query_ids = ["m9"]
    store.set_state("gmail.history_id", "1")
    g.expired = True
    sink = Sink()
    assert await GmailSensor(g, store, 60).poll(sink) == 1
    assert store.get_state("gmail.history_id") == "100"


def test_normalize_message():
    p = normalize_message(gmail_meta("x", sender='"Sam Lee" <Sam@Example.com>', extra_headers=[("List-Unsubscribe", "<mailto:u>")]))
    assert p["from_name"] == "Sam Lee"
    assert p["from_email"] == "sam@example.com"
    assert p["is_bulk"] is True
    assert p["date"].startswith("2026-11-04T08:00")
