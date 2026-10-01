"""Regression checks for recovery ordering and reconnects; all device boundaries are fake."""

import asyncio
import itertools
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest
from evdev import ecodes

from yoga_deck.adapters.hyprland_inputs import (
    AsyncHyprlandInputAdapter,
    InputRole,
    InternalInput,
)
from yoga_deck.adapters.pen_action_runtime import ContinuousPenActionSource, PenActionMapping
from yoga_deck.adapters.sensor_proxy import DbusNextSensorConnection, OrientationSession
from yoga_deck.coordinator import (
    InputCoordinator,
    OcrCoordinator,
    OskCoordinator,
    RotationCoordinator,
    SystemCoordinator,
)
from yoga_deck.core import (
    CaptureText,
    PenAction,
    PenActionRequested,
    State,
    TabletModeChanged,
    reduce,
)
from yoga_deck.runtime import PostureRuntime


class Inputs:
    def __init__(self):
        self.enabled = True
        self.device = InternalInput(InputRole.KEYBOARD, "at-translated-set-2-keyboard")

    def discover_owned(self):
        return (self.device,)

    def set_enabled(self, device, enabled):
        self.enabled = enabled


class Noop:
    async def apply(self, orientation):
        pass

    async def set_enabled(self, enabled):
        pass


def coordinator(inputs, ocr=None):
    return SystemCoordinator(
        InputCoordinator(inputs), RotationCoordinator(Noop()), osk=OskCoordinator(Noop()), ocr=ocr
    )


@pytest.mark.asyncio
async def test_pen_actions_continue_after_session_reconnect():
    class Device:
        name = "wacom-pen-and-multitouch-sensor-pen"

        def capabilities(self, **kwargs):
            return {ecodes.EV_KEY: [ecodes.BTN_TOOL_PEN, ecodes.BTN_STYLUS]}

        async def async_read_loop(self):
            for value in (1, 0, 1, 0, 1, 0):
                yield SimpleNamespace(type=ecodes.EV_KEY, code=ecodes.BTN_STYLUS, value=value)

        def close(self):
            pass

    class Backend:
        def list_devices(self):
            return ("fake-pen",)

        def open_device(self, path):
            return Device()

    ticks = itertools.count(0, 0.1)
    source = ContinuousPenActionSource(
        PenActionMapping(PenAction.CAPTURE_TEXT), backend=Backend(), clock=lambda: next(ticks)
    )
    state, captures = State(), []
    for _ in range(2):
        session = await source.connect()
        async for event in session.events():
            transition = reduce(state, event)
            state = transition.state
            captures.extend(e for e in transition.effects if isinstance(e, CaptureText))
        await session.close()
    assert len(captures) == 6, "Three presses before reconnect + three after should all work"


@pytest.mark.asyncio
async def test_even_async_ocr_does_not_delay_unfold_recovery():
    entered, release = asyncio.Event(), asyncio.Event()

    class AsyncOcr:
        async def capture_text(self):
            entered.set()
            await release.wait()

    inputs = Inputs()
    runtime = PostureRuntime(None, coordinator(inputs, OcrCoordinator(AsyncOcr())))
    await runtime._accept(TabletModeChanged(True))
    capture = asyncio.create_task(runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 1)))
    await entered.wait()
    unfold = asyncio.create_task(runtime._accept(TabletModeChanged(False)))
    for _ in range(10):
        await asyncio.sleep(0)
    recovered_while_picker_open = inputs.enabled
    release.set()
    await asyncio.gather(capture, unfold)
    assert recovered_while_picker_open, "An open OCR picker retains both safety serialization locks"


@pytest.mark.asyncio
async def test_shutdown_recovery_wins_over_inflight_disable_thread():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    class SlowInputs(Inputs):
        def set_enabled(self, device, enabled):
            if not enabled:
                started.set()
                release.wait(3)
            super().set_enabled(device, enabled)
            if not enabled:
                finished.set()

    class Session:
        current_enabled = True

        async def close(self):
            pass

        async def events(self):
            await asyncio.Future()
            yield

    class Source:
        async def connect(self):
            return Session()

    inputs = SlowInputs()
    runtime = PostureRuntime(
        Source(), coordinator(AsyncHyprlandInputAdapter(inputs)), reconcile_interval=100
    )
    task = asyncio.create_task(runtime.run())
    try:
        for _ in range(1000):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        task.cancel()
        with suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
    finally:
        release.set()
    for _ in range(1000):
        if finished.is_set():
            break
        await asyncio.sleep(0.001)
    assert finished.is_set()
    with suppress(asyncio.CancelledError):
        await task
    assert inputs.enabled is True, "Cancelled worker disables keyboard after shutdown recovery"


@pytest.mark.asyncio
async def test_sensor_session_close_disconnects_bus_and_signal_handlers():
    class Bus:
        disconnected = False

        def disconnect(self):
            self.disconnected = True

    class Sensor:
        async def call_release_accelerometer(self):
            pass

    class Properties:
        def on_properties_changed(self, callback):
            self.callback = callback

        def off_properties_changed(self, callback):
            self.callback = None

    class Dbus:
        def on_name_owner_changed(self, callback):
            self.callback = callback

        def off_name_owner_changed(self, callback):
            self.callback = None

    bus = Bus()
    connection = DbusNextSensorConnection(bus, Sensor(), Properties(), Dbus())
    await OrientationSession(connection, "normal").close()
    assert bus.disconnected, "Every resume/hotplug leaves another subscribed bus connection alive"


@pytest.mark.asyncio
async def test_open_ocr_allows_shell_recovery_rotation_and_reconciliation():
    from yoga_deck.adapters.shell_transport import ShellCommand
    from yoga_deck.core import Orientation

    entered, release = asyncio.Event(), asyncio.Event()

    class Ocr:
        async def capture_text(self):
            entered.set()
            await release.wait()

    inputs = Inputs()
    runtime = PostureRuntime(None, coordinator(inputs, OcrCoordinator(Ocr())))
    await runtime._accept(TabletModeChanged(True))
    await runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 1))
    await entered.wait()
    try:
        await asyncio.wait_for(runtime.handle_shell_command(ShellCommand("recover_inputs")), 1)
        await asyncio.wait_for(runtime.handle_shell_command(ShellCommand("rotate", "left")), 1)
        await asyncio.wait_for(runtime._reconcile_compositor(), 1)
        assert inputs.enabled
        assert runtime.state.applied_orientation is Orientation.LEFT
    finally:
        release.set()
        await asyncio.gather(*runtime._capture_tasks)


@pytest.mark.asyncio
async def test_shutdown_recovers_inputs_and_cancels_active_and_queued_captures():
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = []

    class Ocr:
        async def capture_text(self):
            calls.append("capture")
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    class Source:
        async def connect(self):
            await asyncio.Future()

    inputs = Inputs()
    runtime = PostureRuntime(Source(), coordinator(inputs, OcrCoordinator(Ocr())))
    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0)
    await runtime._accept(TabletModeChanged(True))
    await runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 1))
    await entered.wait()
    await runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 2))
    await runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 2))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert inputs.enabled
    assert cancelled.is_set()
    assert calls == ["capture"]
    assert not runtime._capture_tasks


@pytest.mark.asyncio
async def test_async_capture_failure_is_redacted_and_does_not_block_later_capture():
    calls = []

    class Ocr:
        async def capture_text(self):
            calls.append("capture")
            if len(calls) == 1:
                raise OSError("private OCR content")

    diagnostics = []
    runtime = PostureRuntime(None, coordinator(Inputs(), OcrCoordinator(Ocr())), diagnostics.append)
    await runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 1))
    await asyncio.gather(*runtime._capture_tasks)
    await runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 1))
    await runtime._accept(PenActionRequested(PenAction.CAPTURE_TEXT, 2))
    await asyncio.gather(*runtime._capture_tasks)
    assert calls == ["capture", "capture"]
    assert diagnostics == [{"component": "ocr", "code": "ocr_capture_failed"}]
