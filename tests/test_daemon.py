import asyncio

from alfred.config import Config
from alfred.core.daemon import Daemon
from alfred.core.store import Store
from alfred.sensors.heartbeat import HeartbeatSensor


class CrashOnce:
    name = "flaky"

    def __init__(self):
        self.runs = 0

    async def run(self, publish):
        self.runs += 1
        if self.runs == 1:
            raise RuntimeError("boom")
        await asyncio.Event().wait()


async def test_daemon_logs_heartbeats_and_shuts_down(tmp_path, monkeypatch):
    cfg = Config()
    store = Store(tmp_path / "a.db")
    flaky = CrashOnce()
    monkeypatch.setattr("alfred.core.daemon.asyncio.sleep", _fast_sleep)
    d = Daemon(cfg, store, sensors=[HeartbeatSensor(0.02), flaky])

    task = asyncio.create_task(d.run())
    for _ in range(200):
        await _real_sleep(0.01)
        if store.count_events().get("heartbeat.tick", 0) >= 3 and flaky.runs >= 2:
            break
    d.stop()
    await asyncio.wait_for(task, 2)

    counts = store.count_events()
    assert counts["heartbeat.tick"] >= 3
    assert counts["daemon.started"] == 1
    assert counts["daemon.stopped"] == 1
    assert counts["daemon.sensor.crashed"] == 1
    assert flaky.runs == 2  # restarted after the crash
    assert store.get_state("last_heartbeat")
    assert store.get_state("daemon")["running"] is False
    store.close()


_real_sleep = asyncio.sleep


async def _fast_sleep(delay, *a, **kw):
    # Shrink the daemon's crash-restart backoff (patches asyncio.sleep for the test only).
    await _real_sleep(min(delay, 0.01))


async def test_daemon_triages_and_sends_brief(tmp_path):
    from alfred.agent.brief import BriefBuilder
    from alfred.agent.triage import Triager
    from alfred.core.daemon import Components
    from alfred.core.events import Event
    from alfred.notify.email import EmailNotifier
    from tests.fakes import FakeGmail

    class OneShot:
        name = "oneshot"

        async def run(self, publish):
            await publish(Event(source="gmail", kind="message.received",
                                payload={"from_name": "Sam", "subject": "Lunch?", "labels": []}))
            await publish(Event(source="schedule", kind="morning_brief", payload={}))
            await asyncio.Event().wait()

    store = Store(tmp_path / "a.db")
    gmail = FakeGmail()
    comps = Components(
        triager=Triager(None, store, tmp_path / "p.md"),
        brief=BriefBuilder(store, None, None, Config().core.tz),
        notifier=EmailNotifier(gmail, store, "me@example.com"),
    )
    d = Daemon(Config(), store, sensors=[OneShot()], components=comps)
    task = asyncio.create_task(d.run())
    for _ in range(100):
        await _real_sleep(0.01)
        if gmail.sent:
            break
    d.stop()
    await asyncio.wait_for(task, 2)

    counts = store.count_events()
    assert counts["triage.decided"] == 1
    assert len(gmail.sent) == 1
    assert "Lunch?" in gmail.sent[0].get_content()
    store.close()
