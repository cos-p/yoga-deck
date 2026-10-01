import asyncio

import pytest

from yoga_deck.adapters.hyprland_monitor_events import (
    HyprlandMonitorEventError,
    HyprlandMonitorEventSource,
    Socket2Connection,
    normalize_monitor_topology_event,
)
from yoga_deck.core import MonitorTopologyChanged


@pytest.mark.parametrize(
    "raw",
    [
        "monitoradded>>DP-1",
        "monitorremoved>>DP-1",
        "monitoraddedv2>>7,DP-1,private monitor description",
        "monitorremovedv2>>7,DP-1,private monitor description",
    ],
)
def test_monitor_events_are_normalized_without_monitor_identity(raw: str) -> None:
    assert normalize_monitor_topology_event(raw) == MonitorTopologyChanged()


@pytest.mark.parametrize("raw", ["workspace>>1", "monitoradded_extra>>DP-1", "not an event"])
def test_unrelated_or_malformed_socket_events_are_ignored(raw: str) -> None:
    assert normalize_monitor_topology_event(raw) is None


class FakeConnection:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    async def events(self):
        while True:
            value = await self.queue.get()
            if value is None:
                raise HyprlandMonitorEventError("monitor_event_disconnected")
            yield value

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_monitor_source_filters_socket_payload_and_closes_connection() -> None:
    connection = FakeConnection()
    session = await HyprlandMonitorEventSource(lambda: connection).connect()
    connection.queue.put_nowait("workspace>>1")
    connection.queue.put_nowait("monitoraddedv2>>7,DP-1,private monitor description")

    assert await anext(session.events()) == MonitorTopologyChanged()
    await session.close()
    assert connection.closed


@pytest.mark.asyncio
async def test_socket_disconnect_is_stable_and_redacted() -> None:
    class Reader:
        async def readline(self) -> bytes:
            return b""

    class Writer:
        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    events = Socket2Connection(Reader(), Writer()).events()
    with pytest.raises(HyprlandMonitorEventError) as error:
        await anext(events)

    assert error.value.code == "monitor_event_disconnected"
    assert "private" not in str(error.value)
