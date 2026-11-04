from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from alfred.core.store import Store
from alfred.sensors.schedule import DailySchedule

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "a.db") as s:
        yield s


class Sink(list):
    async def __call__(self, e):
        self.append(e)


async def test_fires_once_per_day_within_grace(store):
    s = DailySchedule("morning_brief", time(7, 30), IST, store)
    sink = Sink()
    before = datetime(2026, 11, 4, 1, 59, tzinfo=timezone.utc)  # 07:29 IST
    at = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)       # 07:30 IST
    late = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)     # 11:30 IST, past 3h grace
    assert not await s.tick(sink, before)
    assert await s.tick(sink, at)
    assert not await s.tick(sink, at)
    assert sink[0].topic == "schedule.morning_brief" and sink[0].payload == {"date": "2026-11-04"}
    s2 = DailySchedule("other", time(7, 30), IST, store)
    assert not await s2.tick(sink, late)
