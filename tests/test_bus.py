import asyncio

from alfred.core.bus import EventBus
from alfred.core.events import Event


async def test_pattern_matching_and_isolation():
    bus = EventBus()
    seen: dict[str, list[str]] = {"all": [], "gmail": [], "ok": []}

    async def all_(e):
        seen["all"].append(e.topic)

    async def gmail(e):
        seen["gmail"].append(e.topic)

    async def boom(e):
        raise RuntimeError("handler bug")

    async def ok(e):
        seen["ok"].append(e.topic)

    bus.subscribe("*", all_)
    bus.subscribe("gmail.*", gmail)
    bus.subscribe("*", boom)
    bus.subscribe("*", ok)

    task = asyncio.create_task(bus.run())
    await bus.publish(Event(source="gmail", kind="message.received"))
    await bus.publish(Event(source="heartbeat", kind="tick"))
    await bus.drain()
    task.cancel()

    assert seen["all"] == ["gmail.message.received", "heartbeat.tick"]
    assert seen["gmail"] == ["gmail.message.received"]
    assert seen["ok"] == seen["all"]  # a failing handler doesn't block later ones
