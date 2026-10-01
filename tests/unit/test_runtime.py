import asyncio

import pytest

from yoga_deck.adapters.shell_transport import ShellCommand
from yoga_deck.core import (
    CaptureText,
    MonitorTopologyChanged,
    Orientation,
    OrientationChanged,
    OrientationLockChanged,
    PenAction,
    PenActionRequested,
    RefreshOskTheme,
    ResumeRequested,
    SetInternalInputsEnabled,
    SetOskEnabled,
    TabletModeChanged,
)
from yoga_deck.runtime import PostureRuntime


class FakeCoordinator:
    def __init__(self, *, fail_apply_once=False) -> None:
        self.calls = []
        self.effects = []
        self.fail_apply_once = fail_apply_once

    async def recover_inputs(self):
        self.calls.append(("recover", None))

    async def reconcile(self):
        self.calls.append(("reconcile", None))

    async def apply(self, effect):
        self.effects.append(effect)
        value = getattr(effect, "enabled", getattr(effect, "orientation", None))
        self.calls.append(("apply", (value, effect.sequence)))
        if self.fail_apply_once:
            self.fail_apply_once = False
            raise RuntimeError("private compositor output")


class FakeSession:
    def __init__(self, current, events=(), *, disconnect=False) -> None:
        self.current_enabled = current
        self._events = events
        self._disconnect = disconnect
        self.closed = False

    async def events(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield TabletModeChanged(event)
        if self._disconnect:
            raise OSError("private device path")
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FakeSource:
    def __init__(self, sessions) -> None:
        self.sessions = list(sessions)

    async def connect(self):
        if self.sessions:
            return self.sessions.pop(0)
        await asyncio.Future()


class FakeOrientationSession:
    def __init__(self, current, events=(), *, disconnect=False) -> None:
        self.current_event = OrientationChanged(current)
        self._events = events
        self._disconnect = disconnect
        self.closed = False

    async def events(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield event if isinstance(event, OrientationLockChanged) else OrientationChanged(event)
        if self._disconnect:
            raise OSError("private bus name")
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FakePenActionSession:
    def __init__(self, events=(), *, disconnect=False) -> None:
        self._events = events
        self._disconnect = disconnect
        self.closed = False

    async def events(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield event
        if self._disconnect:
            raise OSError("private pen device path")
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FakeResumeSession:
    def __init__(self, events=(), *, gate=None, disconnect=False) -> None:
        self._events = events
        self._gate = gate
        self._disconnect = disconnect
        self.closed = False

    async def events(self):
        if self._gate is not None:
            await self._gate.wait()
        for event in self._events:
            await asyncio.sleep(0)
            yield event
        if self._disconnect:
            raise OSError("private bus name")
        await asyncio.Future()

    async def close(self):
        self.closed = True


class FakeMonitorSession:
    def __init__(self, events=(), *, gate=None, disconnect=False) -> None:
        self._events = events
        self._gate = gate
        self._disconnect = disconnect
        self.closed = False

    async def events(self):
        if self._gate is not None:
            await self._gate.wait()
        for event in self._events:
            await asyncio.sleep(0)
            yield event
        if self._disconnect:
            raise OSError("private monitor socket")
        await asyncio.Future()

    async def close(self):
        self.closed = True


async def wait_for(predicate) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached")


@pytest.mark.asyncio
async def test_startup_recovers_then_reconciles_current_folded_state() -> None:
    coordinator = FakeCoordinator()
    session = FakeSession(True)
    runtime = PostureRuntime(FakeSource([session]), coordinator, retry_delay=0)
    task = asyncio.create_task(runtime.run())

    await wait_for(lambda: runtime.state.tablet_mode_observed is True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert coordinator.calls[:2] == [("recover", None), ("reconcile", None)]
    input_effects = [
        effect for effect in coordinator.effects if isinstance(effect, SetInternalInputsEnabled)
    ]
    osk_effects = [effect for effect in coordinator.effects if isinstance(effect, SetOskEnabled)]
    assert [(effect.enabled, effect.sequence) for effect in input_effects] == [
        (True, 1),
        (False, 2),
        (True, 3),
    ]
    assert [(effect.enabled, effect.sequence) for effect in osk_effects] == [
        (False, 1),
        (True, 2),
        (False, 3),
    ]
    assert session.closed


@pytest.mark.asyncio
async def test_rapid_events_are_reduced_and_applied_in_sequence() -> None:
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(FakeSource([FakeSession(False, [True, False, True])]), coordinator)
    task = asyncio.create_task(runtime.run())

    await wait_for(lambda: ("apply", (False, 4)) in coordinator.calls)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [
        (effect.enabled, effect.sequence)
        for effect in coordinator.effects
        if isinstance(effect, SetInternalInputsEnabled)
    ][:4] == [
        (True, 1),
        (False, 2),
        (True, 3),
        (False, 4),
    ]


@pytest.mark.asyncio
async def test_device_disconnect_reconnects_and_reconciles() -> None:
    first = FakeSession(False, disconnect=True)
    second = FakeSession(True)
    diagnostics = []
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([first, second]), coordinator, diagnostics.append, retry_delay=0
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(lambda: any(call == ("apply", (False, 3)) for call in coordinator.calls))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert coordinator.calls.count(("reconcile", None)) >= 2
    assert diagnostics == [{"component": "tablet_switch", "code": "device_disconnected"}]
    assert first.closed and second.closed


@pytest.mark.asyncio
async def test_compositor_failure_emits_only_stable_diagnostic_and_reconciles() -> None:
    diagnostics = []
    coordinator = FakeCoordinator(fail_apply_once=True)
    runtime = PostureRuntime(
        FakeSource([FakeSession(True)]), coordinator, diagnostics.append, retry_delay=0
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(lambda: bool(diagnostics))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert diagnostics == [{"component": "hyprland", "code": "reconcile_required"}]
    assert "private" not in repr(diagnostics)


@pytest.mark.asyncio
async def test_partial_startup_recovery_failure_is_reported_but_runtime_continues() -> None:
    class RecoveryFailureCoordinator(FakeCoordinator):
        async def recover_inputs(self):
            await super().recover_inputs()
            raise RuntimeError("private target")

    diagnostics = []
    coordinator = RecoveryFailureCoordinator()
    runtime = PostureRuntime(
        FakeSource([FakeSession(False)]), coordinator, diagnostics.append, retry_delay=0
    )
    task = asyncio.create_task(runtime.run())
    await wait_for(lambda: ("reconcile", None) in coordinator.calls)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert diagnostics[0] == {"component": "hyprland", "code": "input_recovery_incomplete"}


@pytest.mark.asyncio
async def test_orientation_events_share_the_serial_reducer_owner() -> None:
    orientation = FakeOrientationSession(
        Orientation.NORMAL,
        [Orientation.RIGHT, Orientation.INVERTED, Orientation.LEFT],
    )
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([FakeSession(False)]),
        coordinator,
        orientation_source=FakeSource([orientation]),
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(lambda: any(c == ("apply", (Orientation.LEFT, 4)) for c in coordinator.calls))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rotations = [
        value
        for kind, value in coordinator.calls
        if kind == "apply" and isinstance(value[0], Orientation)
    ]
    assert rotations == [
        (Orientation.RIGHT, 2),
        (Orientation.INVERTED, 3),
        (Orientation.LEFT, 4),
    ]
    assert orientation.closed


@pytest.mark.asyncio
async def test_debounced_pen_actions_share_the_serial_reducer_owner() -> None:
    pen = FakePenActionSession(
        [
            PenActionRequested(PenAction.CAPTURE_TEXT, source_sequence=1),
            PenActionRequested(PenAction.CAPTURE_TEXT, source_sequence=1),
        ]
    )
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([FakeSession(False)]),
        coordinator,
        pen_action_source=FakeSource([pen]),
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(
        lambda: sum(isinstance(effect, CaptureText) for effect in coordinator.effects) == 1
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.state.last_pen_action_source_sequence == 1
    assert sum(isinstance(effect, CaptureText) for effect in coordinator.effects) == 1
    assert pen.closed


@pytest.mark.asyncio
async def test_orientation_disconnect_reconnects_with_redacted_diagnostic() -> None:
    diagnostics = []
    first = FakeOrientationSession(Orientation.NORMAL, disconnect=True)
    second = FakeOrientationSession(Orientation.RIGHT)
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([FakeSession(False)]),
        coordinator,
        diagnostics.append,
        orientation_source=FakeSource([first, second]),
        retry_delay=0,
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(
        lambda: any(
            call[1][0] is Orientation.RIGHT for call in coordinator.calls if call[0] == "apply"
        )
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert {
        "component": "orientation_sensor",
        "code": "orientation_sensor_unavailable",
    } in diagnostics
    assert "private" not in repr(diagnostics)
    assert first.closed and second.closed


@pytest.mark.asyncio
async def test_orientation_lock_blocks_sensor_rotation_until_unlock() -> None:
    orientation = FakeOrientationSession(
        Orientation.NORMAL,
        [
            OrientationLockChanged(True),
            Orientation.RIGHT,
            OrientationLockChanged(False),
        ],
    )
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([FakeSession(False)]),
        coordinator,
        orientation_source=FakeSource([orientation]),
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(
        lambda: any(
            call[0] == "apply" and call[1][0] is Orientation.RIGHT for call in coordinator.calls
        )
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rotations = [
        value
        for kind, value in coordinator.calls
        if kind == "apply" and isinstance(value[0], Orientation)
    ]
    assert rotations == [(Orientation.RIGHT, 4)]


@pytest.mark.asyncio
async def test_shell_controls_enter_the_serial_reducer_owner() -> None:
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(FakeSource([]), coordinator)

    await runtime.handle_shell_command(ShellCommand("set_lock", True))
    await runtime.handle_shell_command(ShellCommand("rotate", "left"))
    await runtime.handle_shell_command(ShellCommand("recover_inputs"))

    assert runtime.state.orientation_locked is True
    assert runtime.state.applied_orientation is Orientation.LEFT
    assert runtime.state.internal_inputs_enabled is True
    assert ("apply", (Orientation.LEFT, 2)) in coordinator.calls
    assert ("apply", (True, 3)) in coordinator.calls


@pytest.mark.asyncio
async def test_resume_reopens_observed_sources_before_reconciling_safe_state() -> None:
    first_switch = FakeSession(False)
    resumed_switch = FakeSession(True)
    first_orientation = FakeOrientationSession(Orientation.NORMAL)
    resumed_orientation = FakeOrientationSession(Orientation.LEFT)
    gate = asyncio.Event()
    resumed = FakeResumeSession([ResumeRequested()], gate=gate)
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([first_switch, resumed_switch]),
        coordinator,
        orientation_source=FakeSource([first_orientation, resumed_orientation]),
        resume_source=FakeSource([resumed]),
        retry_delay=0,
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(
        lambda: (
            runtime.state.tablet_mode_observed is False
            and runtime.state.observed_orientation is Orientation.NORMAL
        )
    )
    gate.set()
    await wait_for(
        lambda: (
            runtime.state.tablet_mode_observed is True
            and runtime.state.observed_orientation is Orientation.LEFT
            and any(call[0] == "apply" and call[1][0] is False for call in coordinator.calls)
        )
    )
    resumed_state = runtime.state
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert resumed_state.internal_inputs_enabled is False
    assert resumed_state.applied_orientation is Orientation.LEFT
    assert first_switch.closed and resumed_switch.closed
    assert first_orientation.closed and resumed_orientation.closed
    assert resumed.closed


@pytest.mark.asyncio
async def test_resume_monitor_failure_is_redacted_and_runtime_keeps_recoverable_state() -> None:
    diagnostics = []
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([FakeSession(False)]),
        coordinator,
        diagnostics.append,
        resume_source=FakeSource([FakeResumeSession(disconnect=True)]),
        retry_delay=0,
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(lambda: bool(diagnostics))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert diagnostics == [{"component": "resume", "code": "resume_monitor_unavailable"}]
    assert runtime.state.internal_inputs_enabled is True
    assert "private" not in repr(diagnostics)


@pytest.mark.asyncio
async def test_monitor_hotplug_reopens_observed_sources_before_reconciliation() -> None:
    first_switch = FakeSession(False)
    hotplugged_switch = FakeSession(True)
    first_orientation = FakeOrientationSession(Orientation.NORMAL)
    hotplugged_orientation = FakeOrientationSession(Orientation.RIGHT)
    gate = asyncio.Event()
    monitor = FakeMonitorSession([MonitorTopologyChanged()], gate=gate)
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([first_switch, hotplugged_switch]),
        coordinator,
        orientation_source=FakeSource([first_orientation, hotplugged_orientation]),
        monitor_source=FakeSource([monitor]),
        retry_delay=0,
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(
        lambda: (
            runtime.state.tablet_mode_observed is False
            and runtime.state.observed_orientation is Orientation.NORMAL
        )
    )
    gate.set()
    await wait_for(
        lambda: (
            runtime.state.tablet_mode_observed is True
            and runtime.state.observed_orientation is Orientation.RIGHT
            and any(call[0] == "apply" and call[1][0] is False for call in coordinator.calls)
        )
    )
    observed = runtime.state
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert observed.internal_inputs_enabled is False
    assert observed.applied_orientation is Orientation.RIGHT
    assert first_switch.closed and hotplugged_switch.closed
    assert first_orientation.closed and hotplugged_orientation.closed
    assert monitor.closed


@pytest.mark.asyncio
async def test_monitor_source_failure_is_redacted_and_runtime_keeps_recoverable_state() -> None:
    diagnostics = []
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(
        FakeSource([FakeSession(False)]),
        coordinator,
        diagnostics.append,
        monitor_source=FakeSource([FakeMonitorSession(disconnect=True)]),
        retry_delay=0,
    )
    task = asyncio.create_task(runtime.run())

    await wait_for(lambda: bool(diagnostics))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert diagnostics == [{"component": "hyprland", "code": "monitor_topology_unavailable"}]
    assert runtime.state.internal_inputs_enabled is True
    assert "private" not in repr(diagnostics)


@pytest.mark.asyncio
async def test_theme_hook_retints_a_folded_keyboard_through_the_reducer() -> None:
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(FakeSource([]), coordinator)
    await runtime._accept(TabletModeChanged(enabled=True))

    await runtime.handle_shell_command(ShellCommand("theme_changed"))

    assert any(isinstance(effect, RefreshOskTheme) for effect in coordinator.effects)


@pytest.mark.asyncio
async def test_theme_hook_is_inert_in_laptop_mode() -> None:
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(FakeSource([]), coordinator)

    await runtime.handle_shell_command(ShellCommand("theme_changed"))

    assert coordinator.effects == []


@pytest.mark.asyncio
async def test_a_failed_retint_is_reported_without_backend_detail() -> None:
    diagnostics: list[dict[str, str]] = []
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(FakeSource([]), coordinator, diagnostics.append)
    await runtime._accept(TabletModeChanged(enabled=True))
    coordinator.fail_apply_once = True

    await runtime.handle_shell_command(ShellCommand("theme_changed"))

    assert {"component": "osk", "code": "osk_theme_refresh_failed"} in diagnostics


class FakeSettingsStore:
    """The settings files, seen from the runtime as one writable store."""

    def __init__(self, *, changed: bool = True, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self._changed = changed
        self._error = error

    def apply(self, name: str, value: object) -> bool:
        self.calls.append((name, value))
        if self._error is not None:
            raise self._error
        return self._changed


@pytest.mark.asyncio
async def test_a_panel_control_stores_a_setting_and_the_keyboard_picks_it_up() -> None:
    """The respawn is the point: folded, the next fold is too late to see a slider work."""

    coordinator = FakeCoordinator()
    store = FakeSettingsStore()
    runtime = PostureRuntime(FakeSource([]), coordinator, settings_store=store)
    await runtime._accept(TabletModeChanged(enabled=True))

    await runtime.handle_shell_command(ShellCommand("set_config", 400, "osk.landscape_height"))

    assert store.calls == [("osk.landscape_height", 400)]
    assert any(isinstance(effect, RefreshOskTheme) for effect in coordinator.effects)


@pytest.mark.asyncio
async def test_resending_the_value_a_control_already_shows_does_not_disturb_the_keyboard() -> None:
    coordinator = FakeCoordinator()
    store = FakeSettingsStore(changed=False)
    runtime = PostureRuntime(FakeSource([]), coordinator, settings_store=store)
    await runtime._accept(TabletModeChanged(enabled=True))

    await runtime.handle_shell_command(ShellCommand("set_config", 280, "osk.landscape_height"))

    assert store.calls == [("osk.landscape_height", 280)]
    assert not any(isinstance(effect, RefreshOskTheme) for effect in coordinator.effects)


@pytest.mark.asyncio
async def test_clearing_an_override_is_a_change_like_any_other() -> None:
    coordinator = FakeCoordinator()
    store = FakeSettingsStore()
    runtime = PostureRuntime(FakeSource([]), coordinator, settings_store=store)
    await runtime._accept(TabletModeChanged(enabled=True))

    await runtime.handle_shell_command(ShellCommand("set_config", None, "osk.font"))

    assert store.calls == [("osk.font", None)]
    assert any(isinstance(effect, RefreshOskTheme) for effect in coordinator.effects)


@pytest.mark.asyncio
async def test_a_settings_change_in_laptop_mode_waits_for_the_next_fold() -> None:
    coordinator = FakeCoordinator()
    store = FakeSettingsStore()
    runtime = PostureRuntime(FakeSource([]), coordinator, settings_store=store)

    await runtime.handle_shell_command(ShellCommand("set_config", 400, "osk.landscape_height"))

    assert store.calls == [("osk.landscape_height", 400)]
    assert coordinator.effects == []


@pytest.mark.asyncio
async def test_a_failed_settings_write_is_reported_rather_than_swallowed() -> None:
    """A control with someone watching it is owed the failure; the keyboard path is not."""

    coordinator = FakeCoordinator()
    store = FakeSettingsStore(error=OSError("read-only file system"))
    runtime = PostureRuntime(FakeSource([]), coordinator, settings_store=store)

    with pytest.raises(ValueError, match="settings_write_failed"):
        await runtime.handle_shell_command(ShellCommand("set_config", 400, "osk.landscape_height"))

    assert coordinator.effects == []


@pytest.mark.asyncio
async def test_a_runtime_built_without_a_settings_store_refuses_to_pretend() -> None:
    coordinator = FakeCoordinator()
    runtime = PostureRuntime(FakeSource([]), coordinator)

    with pytest.raises(ValueError, match="invalid_shell_command"):
        await runtime.handle_shell_command(ShellCommand("set_config", 400, "osk.landscape_height"))
