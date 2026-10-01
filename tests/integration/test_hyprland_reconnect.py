"""Hard compositor loss and replacement against a fake Hyprland.

The real runtime, coordinators, and Hyprland adapters run unchanged; only the ``hyprctl``
process boundary and the socket2 connector are replaced by a fake compositor that can crash
and be replaced by a fresh instance with default input and monitor configuration. This is
fake-adapter coverage, not evidence that a real compositor restart behaves this way.
"""

import asyncio
import json
import re
from dataclasses import dataclass

import pytest

from yoga_deck.adapters.hyprland_inputs import HyprlandInputAdapter
from yoga_deck.adapters.hyprland_monitor_events import (
    HyprlandMonitorEventError,
    HyprlandMonitorEventSource,
)
from yoga_deck.adapters.hyprland_rotation import HyprlandRotationAdapter
from yoga_deck.coordinator import (
    InputCoordinator,
    OskCoordinator,
    RotationCoordinator,
    SystemCoordinator,
)
from yoga_deck.core import Orientation, OrientationChanged
from yoga_deck.runtime import PostureRuntime

pytestmark = pytest.mark.integration

INTERNAL = (
    "at-translated-set-2-keyboard",
    "synps/2-synaptics-touchpad",
    "tpps/2-alps-trackpoint",
)
EXTERNAL = ("usb-external-keyboard", "usb-external-mouse")
TOUCH = "wacom-pen-and-multitouch-sensor-finger"
PEN = "wacom-pen-and-multitouch-sensor-pen"
_DEVICE = re.compile(r'hl\.device\(\{ name = "([^"]+)", enabled = (true|false) \}\)')
_TRANSFORM = re.compile(r'hl\.device\(\{ name = "([^"]+)", transform = (\d)')
_MONITOR = re.compile(r'hl\.monitor\(\{ output = "([^"]+)".*?scale = ([\d.]+), transform = (\d)')


@dataclass
class Result:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class FakeHyprland:
    """One compositor process at a time; a replacement starts from default configuration."""

    def __init__(self) -> None:
        self.instance = 0
        self.alive = False
        self.evals: list[tuple[int, str]] = []
        self.socket2_connections: list[int] = []
        self._disconnected = asyncio.Event()
        self.start()

    def start(self) -> None:
        self.instance += 1
        self.alive = True
        self.enabled = dict.fromkeys(INTERNAL + EXTERNAL, True)
        self.monitors = {"eDP-1": {"scale": 1.25, "transform": 0}, "DP-1": {"scale": 1.0}}
        self.input_transform = {TOUCH: 0, PEN: 0}
        self._disconnected = asyncio.Event()

    def crash(self) -> None:
        self.alive = False
        self._disconnected.set()

    def run(self, args, timeout):
        if not self.alive:
            return Result(stderr="HYPRLAND_INSTANCE_SIGNATURE invalid", returncode=4)
        if args == ["hyprctl", "-j", "devices"]:
            return Result(json.dumps(self._devices()))
        if args == ["hyprctl", "-j", "monitors", "all"]:
            return Result(json.dumps(self._monitors()))
        if args[:2] == ["hyprctl", "eval"]:
            self._eval(args[2])
            return Result("ok")
        return Result(returncode=1)

    def _devices(self):
        return {
            "keyboards": [{"name": INTERNAL[0]}, {"name": EXTERNAL[0]}],
            "mice": [{"name": INTERNAL[1]}, {"name": INTERNAL[2]}, {"name": EXTERNAL[1]}],
            "touch": [{"name": TOUCH}],
            "tablets": [{"name": PEN}],
        }

    def _monitors(self):
        internal = self.monitors["eDP-1"]
        return [
            {
                "name": "eDP-1",
                "width": 1920,
                "height": 1080,
                "refreshRate": 60.02,
                "x": 0,
                "y": 0,
                "scale": internal["scale"],
                "transform": internal["transform"],
                "vrr": False,
                "colorManagementPreset": "srgb",
            },
            {"name": "DP-1", "scale": self.monitors["DP-1"]["scale"], "transform": 0},
        ]

    def _eval(self, lua: str) -> None:
        self.evals.append((self.instance, lua))
        for name, enabled in _DEVICE.findall(lua):
            self.enabled[name] = enabled == "true"
        for name, transform in _TRANSFORM.findall(lua):
            self.input_transform[name] = int(transform)
        for output, scale, transform in _MONITOR.findall(lua):
            self.monitors[output].update(scale=float(scale), transform=int(transform))

    def internal_enabled(self) -> set[bool]:
        return {self.enabled[name] for name in INTERNAL}

    async def connect_socket2(self):
        if not self.alive:
            raise HyprlandMonitorEventError("hyprland_socket_unavailable")
        self.socket2_connections.append(self.instance)
        return _Socket2(self._disconnected)


class _Socket2:
    def __init__(self, disconnected: asyncio.Event) -> None:
        self._disconnected = disconnected

    async def events(self):
        await self._disconnected.wait()
        raise HyprlandMonitorEventError("monitor_event_disconnected")
        yield  # pragma: no cover


class Osk:
    def __init__(self) -> None:
        self.enabled = False

    def set_enabled(self, enabled):
        self.enabled = enabled

    def refresh_theme(self):
        return None


class SwitchSession:
    def __init__(self, folded: bool) -> None:
        self.current_enabled = folded

    async def events(self):
        await asyncio.Future()
        yield  # pragma: no cover

    async def close(self):
        return None


class SwitchSource:
    def __init__(self, folded: bool) -> None:
        self.folded = folded

    async def connect(self):
        return SwitchSession(self.folded)


class OrientationSession:
    def __init__(self, orientation: Orientation) -> None:
        self.current_event = OrientationChanged(orientation)

    async def events(self):
        await asyncio.Future()
        yield  # pragma: no cover

    async def close(self):
        return None


class OrientationSource:
    def __init__(self, orientation: Orientation) -> None:
        self.orientation = orientation

    async def connect(self):
        return OrientationSession(self.orientation)


async def wait_until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


def build(hyprland: FakeHyprland, *, folded: bool, orientation: Orientation | None = None):
    diagnostics: list[dict[str, str]] = []
    coordinator = SystemCoordinator(
        InputCoordinator(HyprlandInputAdapter(hyprland)),
        RotationCoordinator(HyprlandRotationAdapter(hyprland)),
        osk=OskCoordinator(Osk()),
    )
    runtime = PostureRuntime(
        SwitchSource(folded),
        coordinator,
        diagnostics.append,
        orientation_source=None if orientation is None else OrientationSource(orientation),
        monitor_source=HyprlandMonitorEventSource(hyprland.connect_socket2),
        retry_delay=0.01,
        reconcile_interval=0.01,
    )
    return runtime, diagnostics


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def crash_and_replace(hyprland: FakeHyprland, diagnostics) -> None:
    hyprland.crash()
    await wait_until(lambda: {"component": "hyprland", "code": "reconcile_required"} in diagnostics)
    hyprland.start()


def assert_redacted(diagnostics) -> None:
    for diagnostic in diagnostics:
        assert set(diagnostic) == {"component", "code"}
        text = json.dumps(diagnostic)
        assert "SIGNATURE" not in text and "eDP" not in text and "keyboard" not in text


@pytest.mark.asyncio
async def test_folded_session_reapplies_inhibition_on_replacement_compositor() -> None:
    hyprland = FakeHyprland()
    runtime, diagnostics = build(hyprland, folded=True)
    task = asyncio.create_task(runtime.run())
    await wait_until(lambda: hyprland.internal_enabled() == {False})

    await crash_and_replace(hyprland, diagnostics)
    # The replacement compositor starts with every input enabled.
    assert hyprland.internal_enabled() == {True}
    await wait_until(lambda: hyprland.internal_enabled() == {False})
    await wait_until(lambda: hyprland.socket2_connections[-1:] == [2])
    await stop(task)

    assert runtime.state.tablet_mode_observed is True
    # Shutdown recovery re-enables owned inputs on the live replacement instance.
    assert hyprland.internal_enabled() == {True}
    assert all(hyprland.enabled[name] for name in EXTERNAL)
    assert not any(name in lua for _, lua in hyprland.evals for name in EXTERNAL)
    assert {"component": "hyprland", "code": "monitor_topology_unavailable"} in diagnostics
    assert_redacted(diagnostics)


@pytest.mark.asyncio
async def test_laptop_session_keeps_internal_inputs_enabled_across_replacement() -> None:
    hyprland = FakeHyprland()
    runtime, diagnostics = build(hyprland, folded=False)
    task = asyncio.create_task(runtime.run())
    await wait_until(lambda: len(hyprland.evals) >= 3)

    await crash_and_replace(hyprland, diagnostics)
    await wait_until(lambda: sum(instance == 2 for instance, _ in hyprland.evals) >= 6)
    observed_while_running = hyprland.internal_enabled()
    await stop(task)

    assert observed_while_running == {True}
    assert runtime.state.internal_inputs_enabled is True
    assert not any("enabled = false" in lua for _, lua in hyprland.evals)
    assert_redacted(diagnostics)


@pytest.mark.asyncio
async def test_replacement_compositor_receives_coordinated_rotation_again() -> None:
    hyprland = FakeHyprland()
    runtime, diagnostics = build(hyprland, folded=True, orientation=Orientation.RIGHT)
    task = asyncio.create_task(runtime.run())
    await wait_until(lambda: hyprland.monitors["eDP-1"]["transform"] == 3)

    await crash_and_replace(hyprland, diagnostics)
    assert hyprland.monitors["eDP-1"]["transform"] == 0
    await wait_until(lambda: hyprland.monitors["eDP-1"]["transform"] == 3)
    await wait_until(lambda: hyprland.internal_enabled() == {False})
    snapshot = (dict(hyprland.monitors), dict(hyprland.input_transform))
    await stop(task)

    monitors, inputs = snapshot
    assert monitors["eDP-1"] == {"scale": 1.25, "transform": 3}
    assert monitors["DP-1"] == {"scale": 1.0}
    assert inputs == {TOUCH: 3, PEN: 3}
    assert not any("DP-1" in lua.replace("eDP-1", "") for _, lua in hyprland.evals)
    assert_redacted(diagnostics)


@pytest.mark.asyncio
async def test_socket_drop_with_live_compositor_reconnects_without_changing_intent() -> None:
    hyprland = FakeHyprland()
    runtime, diagnostics = build(hyprland, folded=True)
    task = asyncio.create_task(runtime.run())
    await wait_until(lambda: hyprland.internal_enabled() == {False})
    await wait_until(lambda: len(hyprland.socket2_connections) == 1)

    # Only the event socket drops; the command socket and compositor state survive.
    dropped = hyprland._disconnected
    hyprland._disconnected = asyncio.Event()
    dropped.set()
    await wait_until(lambda: len(hyprland.socket2_connections) == 2)
    observed = hyprland.internal_enabled()
    await stop(task)

    assert observed == {False}
    assert runtime.state.tablet_mode_observed is True
    assert {"component": "hyprland", "code": "monitor_topology_unavailable"} in diagnostics
    assert_redacted(diagnostics)
