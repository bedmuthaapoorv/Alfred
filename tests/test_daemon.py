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
