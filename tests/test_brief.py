from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from alfred.agent.brief import BriefBuilder
from alfred.config import ModelsConfig
from alfred.core.daemon import deliver_brief
from alfred.core.events import Event
from alfred.core.router import ModelRouter
from alfred.core.store import Store
from alfred.llm import LLM
from alfred.notify.email import EmailNotifier
from tests.fakes import FakeAnthropic, FakeCalendar, FakeGmail, cal_event

UTC = ZoneInfo("UTC")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "a.db") as s:
        yield s


def seed(store):
    for i, (label, summary) in enumerate([("ask", "Sam asks to move call"), ("ignore", "Sale 50% off"), ("remember", "Receipt from Shop")]):
        e = Event(source="gmail", kind="message.received", payload={"from_name": f"P{i}", "subject": summary})
        store.append_event(e)
        store.record_triage(e, label=label, urgency=2 if label == "ask" else 0, summary=summary, reason="r", model="m")


async def test_template_brief(store):
    seed(store)
    cal = FakeCalendar()
    cal.between = [cal_event("e", "2026-11-04T10:00:00Z", "2026-11-04T10:30:00Z", summary="Board prep", me="needsAction")]
    b = await BriefBuilder(store, cal, None, UTC).build()
    assert b.model == "template"
    assert "NEEDS YOU" in b.text and "Sam asks to move call" in b.text
    assert "10:00-10:30  Board prep" in b.text
    assert "Sale 50% off" not in b.text
    assert "3 items seen" in b.text and "1 ignored" in b.text
    assert "2 need you" in b.subject


async def test_llm_brief_and_fallback(store):
    seed(store)
    client = FakeAnthropic(["Good morning. One reply owed."])
    llm = LLM(ModelRouter(ModelsConfig()), store, max_daily_usd=5, tz=UTC, client=client)
    b = await BriefBuilder(store, FakeCalendar(), llm, UTC).build()
    assert b.model == "claude-sonnet-5-5" and b.text.startswith("Good morning. One reply owed.")
    sent_user = client.calls[0]["messages"][0]["content"]
    assert "Sale 50% off" not in sent_user  # ignored items never reach the model
    assert "<untrusted_content" in sent_user

    broken = LLM(ModelRouter(ModelsConfig()), store, max_daily_usd=0, tz=UTC, client=FakeAnthropic([]))
    assert (await BriefBuilder(store, None, broken, UTC).build()).model == "template"


async def test_deliver_sets_window(store):
    seed(store)
    g = FakeGmail()
    assert await deliver_brief(BriefBuilder(store, None, None, UTC), EmailNotifier(g, store, "me@example.com"), store)
    assert store.get_state("brief.last_sent_at")
    b = await BriefBuilder(store, None, None, UTC).build()
    assert "0 items seen" in b.text  # nothing new since the last brief
