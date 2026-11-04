from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alfred.core.store import Store
from alfred.google.calendar import CalendarSensor, normalize_event
from tests.fakes import FakeCalendar, cal_event

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
UTC = ZoneInfo("UTC")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "a.db") as s:
        yield s


class Sink(list):
    async def __call__(self, e):
        self.append(e)


async def test_changes_after_first_poll_and_dedupe(store):
    cal = FakeCalendar()
    cal.updated = [cal_event("e1", "2026-11-05T10:00:00Z", "2026-11-05T11:00:00Z", me="needsAction")]
    sensor = CalendarSensor(cal, store, 60, 15, UTC)
    sink = Sink()

    await sensor.poll(sink, NOW)  # first run only sets the cursor
    assert sink == []
    await sensor.poll(sink, NOW + timedelta(minutes=2))
    assert [e.topic for e in sink] == ["calendar.event.changed"]
    assert sink[0].payload["my_response"] == "needsAction"
    await sensor.poll(sink, NOW + timedelta(minutes=4))  # same version: no repeat
    assert len(sink) == 1

    cal.updated = [dict(cal.updated[0], updated="u2", status="cancelled")]
    await sensor.poll(sink, NOW + timedelta(minutes=6))
    assert sink[-1].topic == "calendar.event.cancelled"


async def test_upcoming_announced_once_and_skips_declined(store):
    cal = FakeCalendar()
    cal.between = [
        cal_event("soon", "2026-11-04T09:10:00Z", "2026-11-04T09:40:00Z"),
        cal_event("nope", "2026-11-04T09:05:00Z", "2026-11-04T09:30:00Z", me="declined"),
        {"id": "allday", "summary": "Holiday", "start": {"date": "2026-11-04"}, "end": {"date": "2026-11-05"}},
    ]
    sensor = CalendarSensor(cal, store, 60, 15, UTC)
    sink = Sink()
    await sensor.poll(sink, NOW)
    await sensor.poll(sink, NOW + timedelta(minutes=1))
    assert [e.payload["event_id"] for e in sink if e.kind == "event.upcoming"] == ["soon"]


def test_normalize_all_day():
    n = normalize_event({"id": "x", "start": {"date": "2026-11-04"}, "end": {"date": "2026-11-05"}}, ZoneInfo("Asia/Kolkata"))
    assert n["all_day"] is True
    assert n["start"].startswith("2026-11-04T00:00:00+05:30")
