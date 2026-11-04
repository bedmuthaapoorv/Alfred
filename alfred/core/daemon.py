"""The always-on daemon: wires sensors -> bus -> handlers and runs until stopped."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Sequence

from alfred import __version__
from alfred.config import Config
from alfred.core.bus import EventBus
from alfred.core.events import Event
from alfred.core.router import ModelRouter
from alfred.core.store import Store
from alfred.sensors.base import Sensor
from alfred.sensors.heartbeat import HeartbeatSensor

log = logging.getLogger(__name__)


class Daemon:
    def __init__(self, config: Config, store: Store, sensors: Sequence[Sensor] | None = None) -> None:
        self.config = config
        self.store = store
        self.bus = EventBus()
        self.router = ModelRouter(config.models)
        self.sensors: list[Sensor] = (
            list(sensors) if sensors is not None else [HeartbeatSensor(config.heartbeat.interval_seconds)]
        )
        self._stop = asyncio.Event()
        # Every event is recorded first: the log is the memory and the
        # training signal for everything that comes later.
        self.bus.subscribe("*", self._record, name="store.record")
        self.bus.subscribe("heartbeat.tick", self._on_heartbeat, name="daemon.heartbeat")

    async def _record(self, event: Event) -> None:
        self.store.append_event(event)

    async def _on_heartbeat(self, event: Event) -> None:
        self.store.set_state("last_heartbeat", event.received_at.isoformat())
        log.info("heartbeat #%s%s", event.payload.get("beat"), " (paused)" if self.store.is_paused() else "")

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.store.set_state("daemon", {"pid": os.getpid(), "version": __version__, "running": True})
        await self.bus.publish(Event(source="daemon", kind="started", payload={"version": __version__}))

        dispatcher = asyncio.create_task(self.bus.run(), name="bus")
        sensor_tasks = [asyncio.create_task(self._run_sensor(s), name=f"sensor:{s.name}") for s in self.sensors]
        log.info("Alfred %s up: sensors=%s models=%s", __version__, [s.name for s in self.sensors], self.router.table())
        try:
            await self._stop.wait()
        finally:
            for t in sensor_tasks:
                t.cancel()
            await asyncio.gather(*sensor_tasks, return_exceptions=True)
            await self.bus.publish(Event(source="daemon", kind="stopped"))
            await self.bus.drain()
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
            self.store.set_state("daemon", {"pid": None, "version": __version__, "running": False})
            log.info("Alfred stopped")

    async def _run_sensor(self, sensor: Sensor) -> None:
        """Run a sensor, restarting it with backoff if it crashes."""
        delay = 1.0
        while True:
            try:
                await sensor.run(self.bus.publish)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("sensor %s crashed; restarting in %.0fs", sensor.name, delay)
                await self.bus.publish(Event(source="daemon", kind="sensor.crashed", payload={"sensor": sensor.name, "error": repr(e)}))
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300.0)


def serve(config: Config) -> None:
    """Blocking entry point used by `alfred serve`."""

    async def _main() -> None:
        with Store(config.core.db_path) as store:
            daemon = Daemon(config, store)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, daemon.stop)
            await daemon.run()

    asyncio.run(_main())
