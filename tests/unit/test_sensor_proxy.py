import asyncio

import pytest

from yoga_deck.adapters.sensor_proxy import (
    DbusNextSensorConnection,
    SensorProxyError,
    SensorProxySource,
    normalize_orientation,
)
from yoga_deck.core import Orientation, OrientationChanged


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("normal", Orientation.NORMAL),
        ("right-up", Orientation.RIGHT),
        ("bottom-up", Orientation.INVERTED),
        ("left-up", Orientation.LEFT),
        ("undefined", None),
        ("future-value", None),
    ],
)
def test_sensor_proxy_values_are_normalized(raw, expected) -> None:
    assert normalize_orientation(raw) == OrientationChanged(expected)


class FakeConnection:
    def __init__(self, current="normal") -> None:
        self.current = current
        self.claimed = False
        self.released = False
        self.closed = False
        self.queue = asyncio.Queue()

    async def claim_accelerometer(self):
        self.claimed = True

    async def release_accelerometer(self):
        self.released = True

    async def get_orientation(self):
        return self.current

    def close(self):
        self.closed = True

    async def changes(self):
        while True:
            yield await self.queue.get()


@pytest.mark.asyncio
async def test_session_claims_reads_changes_and_releases_sensor() -> None:
    connection = FakeConnection("left-up")
    source = SensorProxySource(lambda: connection)
    session = await source.connect()
    connection.queue.put_nowait("right-up")

    event = await anext(session.events())
    await session.close()

    assert session.current_event == OrientationChanged(Orientation.LEFT)
    assert event == OrientationChanged(Orientation.RIGHT)
    assert connection.claimed and connection.released and connection.closed


@pytest.mark.asyncio
async def test_service_owner_loss_ends_production_change_stream() -> None:
    class Signals:
        def on_properties_changed(self, callback):
            self.callback = callback

    class Dbus:
        def on_name_owner_changed(self, callback):
            self.callback = callback

    dbus = Dbus()
    connection = DbusNextSensorConnection(object(), object(), Signals(), dbus)
    changes = connection.changes()
    dbus.callback("net.hadess.SensorProxy", ":1.20", "")

    with pytest.raises(SensorProxyError) as error:
        await anext(changes)

    assert error.value.code == "orientation_sensor_disconnected"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["claim", "read"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_partial_sensor_setup_always_releases_and_closes(stage, cancel):
    entered = asyncio.Event()

    class Connection(FakeConnection):
        async def fail(self):
            entered.set()
            if cancel:
                await asyncio.Future()
            raise OSError("private bus details")

        async def claim_accelerometer(self):
            await super().claim_accelerometer()
            if stage == "claim":
                await self.fail()

        async def get_orientation(self):
            if stage == "read":
                await self.fail()
            return "normal"

    connection = Connection()
    task = asyncio.create_task(SensorProxySource(lambda: connection).connect())
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else SensorProxyError):
        await task
    assert connection.released and connection.closed


@pytest.mark.asyncio
async def test_release_failure_cannot_leak_sensor_connection():
    class Connection(FakeConnection):
        async def release_accelerometer(self):
            raise OSError("lost bus")

    connection = Connection()
    session = await SensorProxySource(lambda: connection).connect()
    await session.close()
    await session.close()
    assert connection.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_introspection_failure_or_cancellation_disconnects_bus(monkeypatch, cancel):
    entered = asyncio.Event()

    class Bus:
        disconnected = False

        async def connect(self):
            return self

        async def introspect(self, *args):
            entered.set()
            if cancel:
                await asyncio.Future()
            raise OSError("private service details")

        def disconnect(self):
            self.disconnected = True

    bus = Bus()
    monkeypatch.setattr("dbus_next.aio.MessageBus", lambda **kwargs: bus)
    task = asyncio.create_task(DbusNextSensorConnection.connect())
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await task
    assert bus.disconnected
