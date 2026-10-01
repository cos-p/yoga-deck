"""Continuous async tablet-switch sessions for the posture runtime."""

from __future__ import annotations

import asyncio
import fcntl
from collections.abc import AsyncIterator

from evdev import ecodes

from yoga_deck.adapters.tablet_switch import (
    EvdevBackend,
    InputBackend,
    InputDevice,
    TabletSwitchError,
    find_tablet_switch,
)
from yoga_deck.core import TabletModeChanged


class EvdevSwitchSession:
    def __init__(self, device: InputDevice, current_enabled: bool | None) -> None:
        self._device = device
        self.current_enabled = current_enabled

    async def events(self) -> AsyncIterator[TabletModeChanged]:
        read_loop = getattr(self._device, "async_read_loop", None)
        if read_loop is None:
            raise TabletSwitchError("tablet_switch_async_unavailable")
        async for event in read_loop():
            if event.type == ecodes.EV_SW and event.code == ecodes.SW_TABLET_MODE:
                yield TabletModeChanged(bool(event.value))

    async def close(self) -> None:
        self._device.close()


class ContinuousTabletSwitchSource:
    def __init__(self, backend: InputBackend | None = None) -> None:
        self._backend = backend or EvdevBackend()

    async def connect(self) -> EvdevSwitchSession:
        device = await asyncio.to_thread(find_tablet_switch, self._backend)
        return EvdevSwitchSession(device, _current_tablet_mode(device))


def _current_tablet_mode(device: InputDevice) -> bool | None:
    """Read EV_SW state; unknown is safer than guessing laptop mode."""

    file_descriptor = getattr(device, "fd", None)
    if not isinstance(file_descriptor, int):
        return None
    size = 8
    # Linux _IOR('E', 0x1b, size), the EVIOCGSW ioctl.
    request = (2 << 30) | (size << 16) | (ord("E") << 8) | 0x1B
    state = bytearray(size)
    try:
        fcntl.ioctl(file_descriptor, request, state, True)
    except OSError:
        return None
    bit = ecodes.SW_TABLET_MODE
    return bool(state[bit // 8] & (1 << (bit % 8)))
