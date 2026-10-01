"""Hyprland socket2 boundary for privacy-safe monitor topology changes."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from yoga_deck.adapters.hyprland_instance import resolve_instance_signature
from yoga_deck.core import MonitorTopologyChanged

_PROC_ROOT = Path("/proc")

_TOPOLOGY_EVENTS = frozenset(
    {"monitoradded", "monitorremoved", "monitoraddedv2", "monitorremovedv2"}
)


class HyprlandMonitorEventError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class MonitorEventConnection(Protocol):
    def events(self) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


class MonitorEventSession:
    def __init__(self, connection: MonitorEventConnection) -> None:
        self._connection = connection

    async def events(self) -> AsyncIterator[MonitorTopologyChanged]:
        async for raw in self._connection.events():
            event = normalize_monitor_topology_event(raw)
            if event is not None:
                yield event

    async def close(self) -> None:
        with suppress(Exception):
            await self._connection.close()


class HyprlandMonitorEventSource:
    """Connect to the compositor event socket without retaining event payloads."""

    def __init__(
        self,
        connector: Callable[[], MonitorEventConnection | Awaitable[MonitorEventConnection]]
        | None = None,
    ) -> None:
        self._connector = connector or Socket2Connection.connect

    async def connect(self) -> MonitorEventSession:
        try:
            connection = self._connector()
            if inspect.isawaitable(connection):
                connection = await connection
        except Exception as error:
            raise HyprlandMonitorEventError("monitor_topology_unavailable") from error
        return MonitorEventSession(connection)


class Socket2Connection:
    """Line-oriented Hyprland socket2 connection with stable disconnect errors."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    @classmethod
    async def connect(cls) -> Socket2Connection:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        # Resolved per connection so the reconnect loop follows a restarted compositor.
        instance = resolve_instance_signature(proc_root=_PROC_ROOT)
        if not runtime_dir or not instance:
            raise HyprlandMonitorEventError("hyprland_socket_unavailable")
        socket_path = Path(runtime_dir) / "hypr" / instance / ".socket2.sock"
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)
        except (OSError, ValueError) as error:
            raise HyprlandMonitorEventError("hyprland_socket_unavailable") from error
        return cls(reader, writer)

    async def events(self) -> AsyncIterator[str]:
        while True:
            line = await self._reader.readline()
            if not line:
                raise HyprlandMonitorEventError("monitor_event_disconnected")
            yield line.decode("utf-8", "replace").rstrip("\n")

    async def close(self) -> None:
        self._writer.close()
        with suppress(Exception):
            await self._writer.wait_closed()


def normalize_monitor_topology_event(raw: str) -> MonitorTopologyChanged | None:
    name, separator, _ = raw.partition(">>")
    if not separator or name not in _TOPOLOGY_EVENTS:
        return None
    return MonitorTopologyChanged()
