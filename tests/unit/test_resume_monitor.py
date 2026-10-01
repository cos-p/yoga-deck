import asyncio

import pytest

from yoga_deck.adapters.resume_monitor import (
    DbusNextResumeConnection,
    ResumeMonitorError,
    ResumeMonitorSource,
)
from yoga_deck.core import ResumeRequested


class FakeConnection:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bool | None] = asyncio.Queue()
        self.closed = False

    async def changes(self):
        while True:
            value = await self.queue.get()
            if value is None:
                raise ResumeMonitorError("resume_monitor_disconnected")
            yield value

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_resume_source_ignores_suspend_and_emits_only_resume() -> None:
    connection = FakeConnection()
    session = await ResumeMonitorSource(lambda: connection).connect()
    connection.queue.put_nowait(True)
    connection.queue.put_nowait(False)

    assert await anext(session.events()) == ResumeRequested()
    await session.close()
    assert connection.closed


@pytest.mark.asyncio
async def test_service_owner_loss_ends_production_resume_stream() -> None:
    class Manager:
        def on_prepare_for_sleep(self, callback):
            self.callback = callback

    class Dbus:
        def on_name_owner_changed(self, callback):
            self.callback = callback

    dbus = Dbus()
    connection = DbusNextResumeConnection(object(), Manager(), dbus)
    changes = connection.changes()
    dbus.callback("org.freedesktop.login1", ":1.20", "")

    with pytest.raises(ResumeMonitorError) as error:
        await anext(changes)

    assert error.value.code == "resume_monitor_disconnected"
