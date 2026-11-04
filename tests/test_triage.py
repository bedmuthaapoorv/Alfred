from zoneinfo import ZoneInfo

import pytest

from alfred.agent.triage import Triager, render, rule_based
from alfred.config import ModelsConfig
from alfred.core.events import Event
from alfred.core.router import ModelRouter
from alfred.core.store import Store
from alfred.google.gmail import normalize_message
from alfred.llm import LLM
from tests.fakes import FakeAnthropic, gmail_meta


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "a.db") as s:
        yield s


def mail_event(**kw):
    return Event(source="gmail", kind="message.received", payload=normalize_message(gmail_meta("m1", **kw)))


async def test_model_decision_recorded(store, tmp_path):
    (tmp_path / "profile.md").write_text("Owner is a founder; investors are VIPs.")
    client = FakeAnthropic([{"label": "ask", "urgency": 2, "summary": "Sam asks to move Friday's call", "reason": "direct question"}])
    llm = LLM(ModelRouter(ModelsConfig()), store, max_daily_usd=1, tz=ZoneInfo("UTC"), client=client)
    e = mail_event(subject="Move Friday?")
    store.append_event(e)
    d = await Triager(llm, store, tmp_path / "profile.md").handle(e)
    assert d["label"] == "ask"
    assert "investors are VIPs" in client.calls[0]["system"]
    assert "<untrusted_content" in client.calls[0]["messages"][0]["content"]
    rows = store.recent_triage()
    assert rows[0]["label"] == "ask" and rows[0]["model"] == "claude-haiku-5-5"


async def test_falls_back_to_rules_over_budget(store, tmp_path):
    client = FakeAnthropic([])
    llm = LLM(ModelRouter(ModelsConfig()), store, max_daily_usd=0, tz=ZoneInfo("UTC"), client=client)
    e = mail_event()
    store.append_event(e)
    d = await Triager(llm, store, tmp_path / "none.md").handle(e)
    assert d["label"] == "remember"  # rules never 'ignore'
    assert client.calls == []
    assert store.recent_triage()[0]["model"] == "rules"


def test_rules_flag_unanswered_invites():
    e = Event(source="calendar", kind="event.changed", payload={"summary": "Board", "my_response": "needsAction"})
    assert rule_based(e)["label"] == "ask"


def test_render_includes_snippet_inside_untrusted():
    text = render(mail_event())
    start = text.index("<untrusted_content")
    assert text.index("snippet m1") > start


def test_correction_and_accuracy(store):
    e = mail_event()
    store.append_event(e)
    store.record_triage(e, label="remember", urgency=0, summary="s", reason="r", model="m")
    assert store.correct_triage(e.id[:6], "ignore") == e.id
    assert store.triage_accuracy() == {"reviewed": 1, "agree": 0, "accuracy": 0.0}
    assert store.triage_since(__import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc), exclude_labels=("ignore",)) == []
