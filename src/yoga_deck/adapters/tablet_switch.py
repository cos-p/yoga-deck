"""Read-only evdev adapter for the authoritative tablet-mode safety switch."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from math import ceil, degrees
from pathlib import Path
from statistics import median
from threading import Event as ThreadEvent
from threading import Lock, Thread
from typing import Protocol

import evdev
from evdev import ecodes

from yoga_deck.adapters.input_access import InputAccessDenied, event_nodes
from yoga_deck.core import TabletModeChanged
from yoga_deck.diagnostics.journal import EventRecord, JournalError


class TabletSwitchError(JournalError):
    """Stable capture failure without device names or paths."""


class TabletSwitchPermissionError(TabletSwitchError, InputAccessDenied):
    """The device may exist, but this user cannot open any candidate node."""


@dataclass(frozen=True, slots=True)
class SwitchObservation:
    enabled: bool
    t_ms: int
    monotonic_timestamp_ns: int
    delivery_latency_ms: float | None
    hinge_degrees: float | None


class HingeAngleReader:
    """Read the semantic hinge angle without relying on an IIO device number."""

    def __init__(self, sys_root: Path = Path("/sys")) -> None:
        self._sys_root = sys_root

    def read_degrees(self) -> float | None:
        try:
            for device in (self._sys_root / "bus/iio/devices").glob("iio:device*"):
                if (device / "name").read_text().strip().casefold() != "hinge":
                    continue
                for label in device.glob("in_angl*_label"):
                    if label.read_text().strip().casefold() != "hinge":
                        continue
                    stem = label.name.removesuffix("_label")
                    raw = float((device / f"{stem}_raw").read_text())
                    scale = float((device / "in_angl_scale").read_text())
                    offset_path = device / "in_angl_offset"
                    offset = float(offset_path.read_text()) if offset_path.exists() else 0.0
                    return degrees((raw + offset) * scale)
        except (OSError, ValueError):
            return None
        return None


class HingeAngleTracker:
    """Retain brief non-zero HID-IIO hinge samples while capture is active."""

    def __init__(
        self,
        reader: Callable[[], float | None] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.02,
    ) -> None:
        self._reader = reader or HingeAngleReader().read_degrees
        self._clock = clock
        self._poll_interval = poll_interval
        self._stop = ThreadEvent()
        self._lock = Lock()
        self._samples: deque[tuple[float, float]] = deque(maxlen=100)
        self._thread: Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = Thread(target=self._poll, name="yoga-deck-hinge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def read_near(self, observed_at: float, window: float = 0.3) -> float | None:
        with self._lock:
            candidates = tuple(self._samples)
        nearby = [sample for sample in candidates if abs(sample[0] - observed_at) <= window]
        if not nearby:
            return None
        return min(nearby, key=lambda sample: abs(sample[0] - observed_at))[1]

    def _poll(self) -> None:
        while not self._stop.wait(self._poll_interval):
            angle = self._reader()
            if angle is not None and angle > 0:
                with self._lock:
                    self._samples.append((self._clock(), angle))


class InputEvent(Protocol):
    type: int
    code: int
    value: int

    def timestamp(self) -> float: ...


class InputDevice(Protocol):
    name: str

    def capabilities(self, verbose: bool = False, absinfo: bool = True) -> dict[int, list[int]]: ...

    def read_loop(self) -> Iterator[InputEvent]: ...

    def close(self) -> None: ...


class InputBackend(Protocol):
    def list_devices(self) -> Iterable[str]: ...

    def open_device(self, path: str) -> InputDevice: ...


class EvdevBackend:
    def list_devices(self) -> Iterable[str]:
        return event_nodes()

    def open_device(self, path: str) -> InputDevice:
        return evdev.InputDevice(path)


def find_tablet_switch(backend: InputBackend) -> InputDevice:
    """Find the switch by stable semantic identity and capabilities."""

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
        switch_codes = capabilities.get(ecodes.EV_SW, [])
        if (
            "thinkpad extra buttons" in device.name.casefold()
            and ecodes.SW_TABLET_MODE in switch_codes
        ):
            return device
        device.close()
    if denied:
        # Some node refused this user, so absence is unproven; say what can be fixed.
        raise TabletSwitchPermissionError("tablet_switch_permission_denied")
    raise TabletSwitchError("tablet_switch_unavailable")


class TabletSwitchSource:
    """Record a bounded sequence of normalized tablet-mode events."""

    def __init__(
        self,
        *,
        backend: InputBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        hinge_reader: Callable[[], float | None] | None = None,
        max_events: int = 2,
        reconnect_limit: int = 3,
    ) -> None:
        if max_events < 1 or reconnect_limit < 0:
            raise ValueError("invalid tablet switch capture limits")
        self._backend = backend or EvdevBackend()
        self._clock = clock
        self._wall_clock = wall_clock
        self._hinge_tracker = None if hinge_reader is not None else HingeAngleTracker()
        self._hinge_reader = hinge_reader
        self._max_events = max_events
        self._reconnect_limit = reconnect_limit
        self.observations: tuple[SwitchObservation, ...] = ()

    def record(self, scenario: str) -> tuple[EventRecord, ...]:
        del scenario  # Scenario names label recordings; they never alter input interpretation.
        if self._hinge_tracker is not None:
            self._hinge_tracker.start()
        try:
            return self._record()
        finally:
            if self._hinge_tracker is not None:
                self._hinge_tracker.stop()

    def _record(self) -> tuple[EventRecord, ...]:
        records: list[EventRecord] = []
        observations: list[SwitchObservation] = []
        self.observations = ()
        started_at: float | None = None
        reconnects = 0

        while len(records) < self._max_events:
            device = find_tablet_switch(self._backend)
            try:
                for event in device.read_loop():
                    if event.type != ecodes.EV_SW or event.code != ecodes.SW_TABLET_MODE:
                        continue
                    observed_at = self._clock()
                    if started_at is None:
                        started_at = observed_at
                    t_ms = round((observed_at - started_at) * 1000)
                    enabled = bool(event.value)
                    latency_ms = max(0.0, (self._wall_clock() - event.timestamp()) * 1000)
                    hinge_degrees = self._hinge_degrees_near(observed_at)
                    records.append(EventRecord(t_ms=t_ms, event=TabletModeChanged(enabled)))
                    observations.append(
                        SwitchObservation(
                            enabled=enabled,
                            t_ms=t_ms,
                            monotonic_timestamp_ns=round(observed_at * 1_000_000_000),
                            delivery_latency_ms=latency_ms,
                            hinge_degrees=hinge_degrees,
                        )
                    )
                    if len(records) == self._max_events:
                        self.observations = tuple(observations)
                        return tuple(records)
            except OSError:
                pass
            finally:
                device.close()

            reconnects += 1
            if reconnects > self._reconnect_limit:
                raise TabletSwitchError("tablet_switch_disconnected")

        return tuple(records)

    def _hinge_degrees_near(self, observed_at: float) -> float | None:
        if self._hinge_tracker is not None:
            time.sleep(0.15)
            return self._hinge_tracker.read_near(observed_at)
        if self._hinge_reader is None:
            return None
        return self._hinge_reader()


def summarize_observations(
    observations: tuple[SwitchObservation, ...],
) -> dict[str, dict[str, float | int | None]]:
    return {
        "delivery_latency_ms": _distribution(
            observation.delivery_latency_ms for observation in observations
        ),
        "folded_hinge_degrees": _distribution(
            observation.hinge_degrees for observation in observations if observation.enabled
        ),
        "laptop_hinge_degrees": _distribution(
            observation.hinge_degrees for observation in observations if not observation.enabled
        ),
    }


def _distribution(values: Iterable[float | None]) -> dict[str, float | int | None]:
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    p95_index = ceil(len(ordered) * 0.95) - 1
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": median(ordered),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }
