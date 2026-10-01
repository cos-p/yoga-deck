"""logind suspend/resume boundary for fresh runtime observation."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from yoga_deck.core import ResumeRequested


class ResumeMonitorError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ResumeConnection(Protocol):
    def changes(self) -> AsyncIterator[bool]: ...

    async def close(self) -> None: ...


class ResumeSession:
    def __init__(self, connection: ResumeConnection) -> None:
        self._connection = connection

    async def events(self) -> AsyncIterator[ResumeRequested]:
        async for preparing_for_sleep in self._connection.changes():
            if not preparing_for_sleep:
                yield ResumeRequested()

    async def close(self) -> None:
        with suppress(Exception):
            await self._connection.close()


class ResumeMonitorSource:
    """Connect to logind without subscribing to private machine-specific details."""

    def __init__(
        self,
        connector: Callable[[], ResumeConnection | Awaitable[ResumeConnection]] | None = None,
    ) -> None:
        self._connector = connector or DbusNextResumeConnection.connect

    async def connect(self) -> ResumeSession:
        try:
            connection = self._connector()
            if inspect.isawaitable(connection):
                connection = await connection
        except Exception as error:
            raise ResumeMonitorError("resume_monitor_unavailable") from error
        return ResumeSession(connection)


class DbusNextResumeConnection:
    """Production logind listener, imported lazily to keep diagnostics safe."""

    _SERVICE = "org.freedesktop.login1"
    _PATH = "/org/freedesktop/login1"
    _INTERFACE = "org.freedesktop.login1.Manager"

    def __init__(self, bus: Any, manager: Any, dbus: Any) -> None:
        self._bus = bus
        self._queue: Any = __import__("asyncio").Queue()
        manager.on_prepare_for_sleep(self._prepare_for_sleep)
        dbus.on_name_owner_changed(self._name_owner_changed)

    @classmethod
    async def connect(cls) -> DbusNextResumeConnection:
        try:
            from dbus_next import BusType
            from dbus_next.aio import MessageBus
        except ImportError as error:
            raise ResumeMonitorError("dbus_binding_unavailable") from error
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        introspection = await bus.introspect(cls._SERVICE, cls._PATH)
        proxy = bus.get_proxy_object(cls._SERVICE, cls._PATH, introspection)
        dbus_path = "/org/freedesktop/DBus"
        dbus_introspection = await bus.introspect("org.freedesktop.DBus", dbus_path)
        dbus_proxy = bus.get_proxy_object("org.freedesktop.DBus", dbus_path, dbus_introspection)
        return cls(
            bus,
            proxy.get_interface(cls._INTERFACE),
            dbus_proxy.get_interface("org.freedesktop.DBus"),
        )

    async def changes(self) -> AsyncIterator[bool]:
        while True:
            value = await self._queue.get()
            if value is None:
                raise ResumeMonitorError("resume_monitor_disconnected")
            yield value

    async def close(self) -> None:
        disconnect = getattr(self._bus, "disconnect", None)
        if disconnect is None:
            return
        result = disconnect()
        if inspect.isawaitable(result):
            await result

    def _prepare_for_sleep(self, preparing_for_sleep: bool) -> None:
        self._queue.put_nowait(preparing_for_sleep)

    def _name_owner_changed(self, name: str, previous: str, current: str) -> None:
        if name == self._SERVICE and previous and not current:
            self._queue.put_nowait(None)
