"""Async SensorProxy boundary for normalized screen orientation events."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from yoga_deck.core import Orientation, OrientationChanged

_ORIENTATIONS = {
    "normal": Orientation.NORMAL,
    "right-up": Orientation.RIGHT,
    "bottom-up": Orientation.INVERTED,
    "left-up": Orientation.LEFT,
}


class SensorProxyError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SensorConnection(Protocol):
    async def claim_accelerometer(self) -> None: ...

    async def release_accelerometer(self) -> None: ...

    async def get_orientation(self) -> str: ...

    def changes(self) -> AsyncIterator[str]: ...

    def close(self) -> None: ...


class OrientationSession:
    def __init__(self, connection: SensorConnection, current: str) -> None:
        self._connection = connection
        self.current_event = normalize_orientation(current)
        self._closed = False

    async def events(self) -> AsyncIterator[OrientationChanged]:
        async for raw in self._connection.changes():
            yield normalize_orientation(raw)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await _release_and_close(self._connection)


class SensorProxySource:
    def __init__(
        self,
        connector: Callable[[], SensorConnection | Awaitable[SensorConnection]] | None = None,
    ) -> None:
        self._connector = connector or DbusNextSensorConnection.connect

    async def connect(self) -> OrientationSession:
        connection = None
        try:
            connection = self._connector()
            if inspect.isawaitable(connection):
                pending = connection
                connection = None
                connection = await pending
            await connection.claim_accelerometer()
            current = await connection.get_orientation()
        except BaseException as error:
            if connection is not None:
                await _release_and_close(connection)
            if isinstance(error, Exception):
                raise SensorProxyError("orientation_sensor_unavailable") from error
            raise
        return OrientationSession(connection, current)


class DbusNextSensorConnection:
    """Production dbus-next connection, imported lazily for safe diagnostics."""

    _SERVICE = "net.hadess.SensorProxy"
    _PATH = "/net/hadess/SensorProxy"
    _INTERFACE = "net.hadess.SensorProxy"

    def __init__(self, bus: Any, sensor: Any, properties: Any, dbus: Any) -> None:
        self._bus = bus
        self._sensor = sensor
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._properties = properties
        self._dbus = dbus
        self._closed = False
        properties.on_properties_changed(self._properties_changed)
        dbus.on_name_owner_changed(self._name_owner_changed)

    @classmethod
    async def connect(cls) -> DbusNextSensorConnection:
        try:
            from dbus_next import BusType
            from dbus_next.aio import MessageBus
        except ImportError as error:
            raise SensorProxyError("dbus_binding_unavailable") from error
        bus = MessageBus(bus_type=BusType.SYSTEM)
        try:
            await bus.connect()
            introspection = await bus.introspect(cls._SERVICE, cls._PATH)
            proxy = bus.get_proxy_object(cls._SERVICE, cls._PATH, introspection)
            dbus_path = "/org/freedesktop/DBus"
            dbus_introspection = await bus.introspect("org.freedesktop.DBus", dbus_path)
            dbus_proxy = bus.get_proxy_object("org.freedesktop.DBus", dbus_path, dbus_introspection)
            return cls(
                bus,
                proxy.get_interface(cls._INTERFACE),
                proxy.get_interface("org.freedesktop.DBus.Properties"),
                dbus_proxy.get_interface("org.freedesktop.DBus"),
            )
        except BaseException:
            bus.disconnect()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._properties.off_properties_changed(self._properties_changed)
            self._dbus.off_name_owner_changed(self._name_owner_changed)
        finally:
            self._bus.disconnect()
        # Wake a remaining consumer without retaining queued observations.
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(None)

    async def claim_accelerometer(self) -> None:
        await self._sensor.call_claim_accelerometer()

    async def release_accelerometer(self) -> None:
        await self._sensor.call_release_accelerometer()

    async def get_orientation(self) -> str:
        return await self._sensor.get_accelerometer_orientation()

    async def changes(self) -> AsyncIterator[str]:
        while True:
            value = await self._queue.get()
            if value is None:
                raise SensorProxyError("orientation_sensor_disconnected")
            yield value

    def _properties_changed(
        self,
        interface_name: str,
        changed: dict[str, Any],
        invalidated: list[str],
    ) -> None:
        del invalidated
        if (
            self._closed
            or interface_name != self._INTERFACE
            or "AccelerometerOrientation" not in changed
        ):
            return
        value = changed["AccelerometerOrientation"].value
        if isinstance(value, str):
            self._queue.put_nowait(value)

    def _name_owner_changed(self, name: str, previous: str, current: str) -> None:
        if not self._closed and name == self._SERVICE and previous and not current:
            self._queue.put_nowait(None)


async def _release_and_close(connection: SensorConnection) -> None:
    try:
        with suppress(Exception):
            await asyncio.wait_for(connection.release_accelerometer(), timeout=1.0)
    finally:
        connection.close()


def normalize_orientation(raw: str) -> OrientationChanged:
    return OrientationChanged(_ORIENTATIONS.get(raw))
