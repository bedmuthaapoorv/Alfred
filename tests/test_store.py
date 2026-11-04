import sqlite3

import pytest

from alfred.core.events import Event
from alfred.core.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "a.db") as s:
        yield s


def test_roundtrip_and_counts(store):
    e = Event(source="heartbeat", kind="tick", payload={"beat": 1})
    store.append_event(e)
    store.append_event(Event(source="gmail", kind="message.received"))
    got = store.recent_events(source="heartbeat")
    assert got == [e]
    assert store.count_events() == {"heartbeat.tick": 1, "gmail.message.received": 1}


def test_events_are_append_only(store):
    store.append_event(Event(source="a", kind="b"))
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute("DELETE FROM events")
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute("UPDATE events SET kind = 'x'")


def test_state_and_pause(store):
    assert store.get_state("missing", 7) == 7
    store.set_state("k", {"a": 1})
    store.set_state("k", {"a": 2})
    assert store.get_state("k") == {"a": 2}
    assert not store.is_paused()
    store.set_paused(True)
    assert store.is_paused()
