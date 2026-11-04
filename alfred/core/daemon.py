"""The always-on daemon: wires sensors -> bus -> handlers and runs until stopped."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass
class Components:
    """Optional Phase 1 parts. Anything None is simply not wired in."""

    triager: object | None = None  # alfred.agent.triage.Triager
    brief: object | None = None  # alfred.agent.brief.BriefBuilder
    notifier: object | None = None  # alfred.notify.email.EmailNotifier


class Daemon:
    def __init__(
        self,
        config: Config,
        store: Store,
        sensors: Sequence[Sensor] | None = None,
        components: Components | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.bus = EventBus()
        self.router = ModelRouter(config.models)
        self.sensors: list[Sensor] = (
            list(sensors) if sensors is not None else [HeartbeatSensor(config.heartbeat.interval_seconds)]
        )
        self.components = components or Components()
        self._stop = asyncio.Event()
        # Every event is recorded first: the log is the memory and the
        # training signal for everything that comes later.
        self.bus.subscribe("*", self._record, name="store.record")
        self.bus.subscribe("heartbeat.tick", self._on_heartbeat, name="daemon.heartbeat")
        if self.components.triager is not None:
            from alfred.agent.triage import TRIAGED_TOPICS

            for topic in TRIAGED_TOPICS:
                self.bus.subscribe(topic, self._on_triage, name=f"triage:{topic}")
        if self.components.brief is not None and self.components.notifier is not None:
            self.bus.subscribe("schedule.morning_brief", self._on_brief, name="brief")

    async def _record(self, event: Event) -> None:
        self.store.append_event(event)

    async def _on_heartbeat(self, event: Event) -> None:
        self.store.set_state("last_heartbeat", event.received_at.isoformat())
        log.info("heartbeat #%s%s", event.payload.get("beat"), " (paused)" if self.store.is_paused() else "")

    async def _on_triage(self, event: Event) -> None:
        decision = await self.components.triager.handle(event)  # type: ignore[union-attr]
        await self.bus.publish(Event(source="triage", kind="decided", payload={"event_id": event.id, **decision}))

    async def _on_brief(self, event: Event) -> None:
        await deliver_brief(self.components.brief, self.components.notifier, self.store)

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


async def deliver_brief(builder, notifier, store: Store) -> bool:  # type: ignore[no-untyped-def]
    now = datetime.now(timezone.utc)
    brief = await builder.build(now)
    sent = await notifier.send("brief", brief.subject, brief.text)
    if sent:
        store.set_state("brief.last_sent_at", now.isoformat())
    log.info("morning brief %s (%s)", "sent" if sent else "not sent", brief.model)
    return sent


def build(config: Config, store: Store) -> tuple[list[Sensor], Components]:
    """Assemble sensors and components from config. Missing pieces degrade, not crash."""
    from alfred.agent.brief import BriefBuilder
    from alfred.agent.triage import Triager
    from alfred.llm import LLM
    from alfred.sensors.schedule import DailySchedule

    tz = config.core.tz
    sensors: list[Sensor] = [HeartbeatSensor(config.heartbeat.interval_seconds)]
    llm = LLM(ModelRouter(config.models), store, max_daily_usd=config.budget.max_daily_usd, tz=tz)

    gmail = calendar = None
    if config.google.enabled:
        from alfred.google.auth import GoogleAuthError, load_credentials

        try:
            creds = load_credentials(config.google)
        except GoogleAuthError as e:
            log.warning("Google disabled: %s", e)
        else:
            from alfred.google.calendar import CalendarSensor, GoogleCalendarAPI
            from alfred.google.gmail import GmailSensor, GoogleGmailAPI

            gmail, calendar = GoogleGmailAPI(creds), GoogleCalendarAPI(creds)
            sensors.append(GmailSensor(gmail, store, config.google.gmail_poll_seconds))
            sensors.append(CalendarSensor(calendar, store, config.google.calendar_poll_seconds,
                                          config.google.upcoming_lead_minutes, tz))

    components = Components(triager=Triager(llm, store, config.core.profile_path))
    if config.brief.enabled and gmail is not None:
        if not config.notify.email:
            log.warning("morning brief disabled: set notify.email")
        else:
            from alfred.notify.email import EmailNotifier

            components.brief = BriefBuilder(store, calendar, llm, tz)
            components.notifier = EmailNotifier(gmail, store, config.notify.email)
            sensors.append(DailySchedule("morning_brief", config.brief.at, tz, store))
    return sensors, components


def serve(config: Config) -> None:
    """Blocking entry point used by `alfred serve`."""

    async def _main() -> None:
        with Store(config.core.db_path) as store:
            sensors, components = build(config, store)
            daemon = Daemon(config, store, sensors=sensors, components=components)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, daemon.stop)
            await daemon.run()

    asyncio.run(_main())
