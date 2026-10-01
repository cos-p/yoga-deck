"""Read-only, opt-in semantic pen-action source for the measured far button."""

from __future__ import annotations

import asyncio
import os
import time
import tomllib
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any, Protocol

import evdev
from evdev import ecodes

from yoga_deck.adapters.input_access import InputAccessDenied, event_nodes
from yoga_deck.core import PenAction, PenActionRequested

DEBOUNCE_MS = 50.0


class PenActionSourceError(RuntimeError):
    """Stable source failure without a device name or path."""


class PenActionPermissionError(PenActionSourceError, InputAccessDenied):
    """The device may exist, but this user cannot open any candidate node."""


class PenActionMappingError(PenActionSourceError):
    """Stable user-mapping failure without exposing configuration content."""


@dataclass(frozen=True, slots=True)
class PenActionMapping:
    """One user-owned far-button choice; ``None`` means safely unbound."""

    far_button_action: PenAction | None = None


class FarButtonDebouncer:
    """Convert complete BTN_STYLUS cycles to sequenced semantic actions."""

    def __init__(self, mapping: PenActionMapping, *, debounce_ms: float = DEBOUNCE_MS) -> None:
        if debounce_ms < 0:
            raise ValueError("pen_action_debounce_invalid")
        self._mapping = mapping
        self._debounce_ms = debounce_ms
        self._down = False
        self._last_completed_at: float | None = None
        self._source_sequence = 0

    def observe(self, value: int, elapsed_ms: float) -> PenActionRequested | None:
        """Accept only one completed primary-barrel cycle outside the debounce window."""
        if value:
            if self._down:
                return None
            self._down = True
            return None
        if not self._down:
            return None
        self._down = False
        if (
            self._last_completed_at is not None
            and elapsed_ms - self._last_completed_at < self._debounce_ms
        ):
            return None
        self._last_completed_at = elapsed_ms
        action = self._mapping.far_button_action
        if action is None:
            return None
        self._source_sequence += 1
        return PenActionRequested(action=action, source_sequence=self._source_sequence)


class InputDevice(Protocol):
    name: str

    def capabilities(self, verbose: bool = False, absinfo: bool = True) -> dict[int, list[int]]: ...

    def async_read_loop(self) -> AsyncIterator[Any]: ...

    def close(self) -> None: ...


class InputBackend(Protocol):
    def list_devices(self) -> Iterable[str]: ...

    def open_device(self, path: str) -> InputDevice: ...


class EvdevBackend:
    def list_devices(self) -> Iterable[str]:
        return event_nodes()

    def open_device(self, path: str) -> InputDevice:
        return evdev.InputDevice(path)


def default_pen_action_mapping_path(environment: Mapping[str, str] | None = None) -> Path:
    environment = environment or os.environ
    config_home = environment.get("XDG_CONFIG_HOME")
    root = Path(config_home) if config_home else Path.home() / ".config"
    return root / "yoga-deck" / "pen-actions.toml"


def load_pen_action_mapping(path: Path | None = None) -> PenActionMapping:
    """Load the narrow user mapping; an absent file intentionally leaves the button off."""
    selected_path = path or default_pen_action_mapping_path()
    try:
        payload = tomllib.loads(selected_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return PenActionMapping()
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PenActionMappingError("pen_action_mapping_invalid") from error
    raw_action = payload.get("far_button_action")
    if raw_action is None:
        return PenActionMapping()
    if not isinstance(raw_action, str):
        raise PenActionMappingError("pen_action_mapping_invalid")
    try:
        action = PenAction(raw_action)
    except ValueError as error:
        raise PenActionMappingError("pen_action_mapping_invalid") from error
    return PenActionMapping(action)


def find_far_button_pen(backend: InputBackend) -> InputDevice:
    """Discover the internal Wacom pen by capabilities, never an event number."""
    denied = False
    for path in backend.list_devices():
        try:
            device = backend.open_device(path)
        except PermissionError:
            denied = True
            continue
        except OSError:
            continue
        try:
            capabilities = device.capabilities(verbose=False, absinfo=False)
        except OSError:
            device.close()
            continue
        keys = capabilities.get(ecodes.EV_KEY, [])
        is_pen = (
            "wacom" in device.name.casefold()
            and device.name.casefold().endswith((" pen", "-pen"))
            and ecodes.BTN_TOOL_PEN in keys
            and ecodes.BTN_STYLUS in keys
        )
        if is_pen:
            return device
        device.close()
    if denied:
        # Some node refused this user, so absence is unproven; say what can be fixed.
        raise PenActionPermissionError("pen_action_permission_denied")
    raise PenActionSourceError("pen_action_source_unavailable")


class EvdevPenActionSession:
    def __init__(
        self,
        device: InputDevice,
        mapping: PenActionMapping,
        *,
        clock: Callable[[], float] = time.monotonic,
        next_sequence: Callable[[], int] | None = None,
    ) -> None:
        self._next_sequence = next_sequence
        self._device = device
        self._debouncer = FarButtonDebouncer(mapping)
        self._clock = clock

    async def events(self) -> AsyncIterator[PenActionRequested]:
        async for event in self._device.async_read_loop():
            if event.type != ecodes.EV_KEY or event.code != ecodes.BTN_STYLUS:
                continue
            normalized = self._debouncer.observe(event.value, self._clock() * 1000)
            if normalized is not None:
                if self._next_sequence is not None:
                    normalized = PenActionRequested(normalized.action, self._next_sequence())
                yield normalized

    async def close(self) -> None:
        self._device.close()


class ContinuousPenActionSource:
    """Reconnectable source whose default mapping is deliberately inactive."""

    def __init__(
        self,
        mapping: PenActionMapping | None = None,
        *,
        backend: InputBackend | None = None,
        mapping_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mapping = mapping
        self._backend = backend or EvdevBackend()
        self._mapping_path = mapping_path
        self._sequences = count(1)
        self._clock = clock

    async def connect(self) -> EvdevPenActionSession:
        mapping = self._mapping or load_pen_action_mapping(self._mapping_path)
        device = await asyncio.to_thread(find_far_button_pen, self._backend)
        return EvdevPenActionSession(
            device, mapping, clock=self._clock, next_sequence=self._sequences.__next__
        )
