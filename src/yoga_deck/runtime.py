"""Async owner for posture event ordering and input-side lifecycle safety."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from yoga_deck.adapters.async_lifecycle import complete_before_cancelling
from yoga_deck.adapters.input_access import InputAccessDenied
from yoga_deck.core import (
    ApplyRotation,
    CaptureText,
    Diagnostic,
    Event,
    InputRecoveryRequested,
    LaunchScratchpad,
    ManualOrientationRequested,
    MonitorTopologyChanged,
    Orientation,
    OrientationChanged,
    OrientationLockChanged,
    PenActionRequested,
    ReconcileObservedState,
    ReconcileRequested,
    RefreshOskTheme,
    ResumeRequested,
    SetInternalInputsEnabled,
    SetOskEnabled,
    SettingsChanged,
    ShutdownRequested,
    State,
    TabletModeChanged,
    ThemeChanged,
    reduce,
)

SafeDiagnostic = dict[str, str]

#: The one degraded reason the shell status exposes today; see ``PostureRuntime.degraded``.
INPUT_ACCESS_DENIED = "input_access_denied"


class ReconnectBackoff:
    """Per-source failure memory: report each new failure once, retry a denial slowly.

    A disconnect keeps the short fixed delay so a re-enumerated device comes back quickly. A
    permission denial cannot fix itself within seconds, so it doubles up to ``maximum``.
    """

    def __init__(self, base: float, maximum: float) -> None:
        self._base = base
        self._maximum = maximum
        self._code: str | None = None
        self._failures = 0

    def failure(self, code: str, *, slow: bool) -> tuple[bool, float]:
        """Return whether ``code`` is news worth a diagnostic and how long to wait."""

        is_new = code != self._code
        self._failures = 1 if is_new else self._failures + 1
        self._code = code
        if not slow:
            return is_new, self._base
        return is_new, min(self._base * 2 ** (self._failures - 1), self._maximum)

    def success(self) -> None:
        self._code = None
        self._failures = 0


class SwitchSession(Protocol):
    current_enabled: bool | None

    def events(self) -> AsyncIterator[TabletModeChanged]: ...

    async def close(self) -> None: ...


class ContinuousSwitchSource(Protocol):
    async def connect(self) -> SwitchSession: ...


class OrientationSession(Protocol):
    current_event: Event

    def events(self) -> AsyncIterator[Event]: ...

    async def close(self) -> None: ...


class ContinuousOrientationSource(Protocol):
    async def connect(self) -> OrientationSession: ...


class PenActionSession(Protocol):
    def events(self) -> AsyncIterator[PenActionRequested]: ...

    async def close(self) -> None: ...


class ContinuousPenActionSource(Protocol):
    async def connect(self) -> PenActionSession: ...


class ResumeSession(Protocol):
    def events(self) -> AsyncIterator[ResumeRequested]: ...

    async def close(self) -> None: ...


class ContinuousResumeSource(Protocol):
    async def connect(self) -> ResumeSession: ...


class MonitorSession(Protocol):
    def events(self) -> AsyncIterator[MonitorTopologyChanged]: ...

    async def close(self) -> None: ...


class ContinuousMonitorSource(Protocol):
    async def connect(self) -> MonitorSession: ...


class RuntimeCoordinator(Protocol):
    async def recover_inputs(self) -> None: ...

    async def reconcile(self) -> None: ...

    async def apply(
        self,
        effect: SetInternalInputsEnabled
        | SetOskEnabled
        | RefreshOskTheme
        | ApplyRotation
        | CaptureText
        | LaunchScratchpad,
    ) -> None: ...


class SettingsStore(Protocol):
    """The user's settings files, seen from the runtime as one writable key-value store.

    Narrow on purpose: the runtime stores a setting and plans a respawn, and never reads a
    settings value itself. The keyboard adapter re-resolves the whole file when it respawns.
    """

    def apply(self, name: str, value: Any) -> bool: ...


class PostureRuntime:
    """Own the reducer, ordered effects, reconnects, and fail-safe recovery."""

    def __init__(
        self,
        source: ContinuousSwitchSource,
        coordinator: RuntimeCoordinator,
        emit_diagnostic: Callable[[SafeDiagnostic], None] | None = None,
        orientation_source: ContinuousOrientationSource | None = None,
        pen_action_source: ContinuousPenActionSource | None = None,
        resume_source: ContinuousResumeSource | None = None,
        monitor_source: ContinuousMonitorSource | None = None,
        settings_store: SettingsStore | None = None,
        *,
        retry_delay: float = 0.5,
        max_retry_delay: float = 30.0,
        reconcile_interval: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._source = source
        self._coordinator = coordinator
        self._emit = emit_diagnostic or (lambda diagnostic: None)
        self._orientation_source = orientation_source
        self._pen_action_source = pen_action_source
        self._resume_source = resume_source
        self._monitor_source = monitor_source
        self._settings_store = settings_store
        self._retry_delay = retry_delay
        self._sleep = sleep or asyncio.sleep
        self._switch_backoff = ReconnectBackoff(retry_delay, max_retry_delay)
        self._pen_backoff = ReconnectBackoff(retry_delay, max_retry_delay)
        self._access_denied: set[str] = set()
        self._reconcile_interval = reconcile_interval
        self._state = State()
        self._event_lock = asyncio.Lock()
        self._capture_tasks: set[asyncio.Task[None]] = set()
        self._reconciliation_condition = asyncio.Condition()
        self._reconciliation_generation = 0

    @property
    def state(self) -> State:
        return self._state

    @property
    def degraded(self) -> str | None:
        """A stable reason the runtime cannot do its job, for the shell status."""

        return INPUT_ACCESS_DENIED if self._access_denied else None

    async def handle_shell_command(self, command: object) -> None:
        name = getattr(command, "name", None)
        value = getattr(command, "value", None)
        if name == "set_lock" and isinstance(value, bool):
            await self._accept(OrientationLockChanged(value))
            return
        if name == "rotate" and isinstance(value, str):
            try:
                orientation = Orientation(value)
            except ValueError as error:
                raise ValueError("invalid_orientation") from error
            await self._accept(ManualOrientationRequested(orientation))
            return
        if name == "recover_inputs" and value is None:
            await self._accept(InputRecoveryRequested())
            return
        if name == "theme_changed" and value is None:
            await self._accept(ThemeChanged())
            return
        if name == "set_config":
            await self._store_setting(getattr(command, "setting", None), value)
            return
        raise ValueError("invalid_shell_command")

    async def _store_setting(self, setting: object, value: object) -> None:
        """Write one setting, then plan the respawn that makes it visible.

        The transport has already checked the value against the same table the settings file
        is read through, so anything that arrives here is storable. What can still fail is the
        write, and a control whose user is watching is owed that failure rather than silence.
        """

        if self._settings_store is None or not isinstance(setting, str):
            raise ValueError("invalid_shell_command")
        try:
            changed = self._settings_store.apply(setting, value)
        except KeyError as error:
            raise ValueError("unknown_setting") from error
        except OSError as error:
            raise ValueError("settings_write_failed") from error
        # Resending the value a control already shows is not a reason to tear down a keyboard.
        if changed:
            await self._accept(SettingsChanged())

    async def run(self) -> None:
        await self._recover_startup()
        reconciler = asyncio.create_task(self._reconcile_periodically())
        orientation_task = (
            asyncio.create_task(self._monitor_orientation())
            if self._orientation_source is not None
            else None
        )
        pen_action_task = (
            asyncio.create_task(self._monitor_pen_actions())
            if self._pen_action_source is not None
            else None
        )
        resume_task = (
            asyncio.create_task(self._monitor_resume()) if self._resume_source is not None else None
        )
        monitor_task = (
            asyncio.create_task(self._monitor_topology())
            if self._monitor_source is not None
            else None
        )
        reconciliation_generation = 0
        try:
            while True:
                session: SwitchSession | None = None
                try:
                    session = await self._source.connect()
                    self._switch_backoff.success()
                    self._access_denied.discard("tablet_switch")
                    if reconciliation_generation:
                        await self._accept(TabletModeChanged(session.current_enabled))
                        await self._reconcile_compositor()
                    else:
                        await self._reconcile_compositor()
                        await self._accept(TabletModeChanged(session.current_enabled))
                    events = session.events()
                    while True:
                        event, reconciliation_generation = await self._next_event_or_reconcile(
                            events, reconciliation_generation
                        )
                        if event is None:
                            break
                        assert isinstance(event, TabletModeChanged)
                        await self._accept(event)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await self._switch_unavailable(error)
                finally:
                    if session is not None:
                        await session.close()
        finally:
            await complete_before_cancelling(
                self._shutdown(
                    reconciler, orientation_task, pen_action_task, resume_task, monitor_task
                )
            )

    async def _switch_unavailable(self, error: Exception) -> None:
        denied = isinstance(error, InputAccessDenied)
        code = "device_permission_denied" if denied else "device_disconnected"
        report, delay = self._switch_backoff.failure(code, slow=denied)
        if report:
            self._diagnostic("tablet_switch", code)
        if denied:
            self._access_denied.add("tablet_switch")
            # An unreadable switch will stay unreadable until the user logs in again, so the
            # last folded reading must not keep the keyboard inhibited: converge to laptop.
            with suppress(ValueError):
                await self._accept(TabletModeChanged(False))
        await self._sleep(delay)

    async def _shutdown(self, *producers: asyncio.Task[None] | None) -> None:
        for task in producers:
            if task is not None:
                task.cancel()
        for task in producers:
            if task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await task
        async with self._event_lock:
            transition = reduce(self._state, ShutdownRequested())
            self._state = transition.state
            for effect in transition.effects:
                if isinstance(effect, (SetInternalInputsEnabled, SetOskEnabled)):
                    try:
                        await self._coordinator.apply(effect)
                    except Exception:
                        if isinstance(effect, SetOskEnabled):
                            self._diagnostic("osk", "osk_transition_failed")
                        else:
                            self._diagnostic("hyprland", "input_recovery_incomplete")
        # Recover physical inputs before waiting for the interactive child to be reaped.
        captures = tuple(self._capture_tasks)
        for task in captures:
            task.cancel()
        await asyncio.gather(*captures, return_exceptions=True)

    async def _recover_startup(self) -> None:
        try:
            await self._coordinator.recover_inputs()
        except Exception:
            self._diagnostic("hyprland", "input_recovery_incomplete")

    async def _reconcile_compositor(self) -> None:
        async with self._event_lock:
            if not self._state.shutdown_requested:
                await self._reconcile_compositor_serialized()

    async def _reconcile_compositor_serialized(self) -> None:
        try:
            await self._coordinator.reconcile()
        except Exception:
            self._diagnostic("hyprland", "reconcile_required")
            return
        transition = reduce(self._state, ReconcileRequested())
        self._state = transition.state
        for effect in transition.effects:
            if isinstance(effect, (SetInternalInputsEnabled, SetOskEnabled)):
                try:
                    await self._coordinator.apply(effect)
                except Exception:
                    if isinstance(effect, SetOskEnabled):
                        self._diagnostic("osk", "osk_transition_failed")
                    else:
                        self._diagnostic("hyprland", "reconcile_required")

    async def _reconcile_periodically(self) -> None:
        while True:
            await asyncio.sleep(self._reconcile_interval)
            await self._reconcile_compositor()

    async def _accept(self, event: Event) -> None:
        async with self._event_lock:
            if self._state.shutdown_requested:
                raise ValueError("runtime_stopping")
            await self._accept_serialized(event)

    async def _accept_serialized(self, event: Event) -> None:
        transition = reduce(self._state, event)
        self._state = transition.state
        for effect in transition.effects:
            if isinstance(effect, CaptureText):
                task = asyncio.create_task(self._capture(effect))
                self._capture_tasks.add(task)
                task.add_done_callback(self._capture_tasks.discard)
                continue
            if isinstance(
                effect,
                (
                    SetInternalInputsEnabled,
                    SetOskEnabled,
                    RefreshOskTheme,
                    ApplyRotation,
                    LaunchScratchpad,
                ),
            ):
                try:
                    await self._coordinator.apply(effect)
                except Exception:
                    component = (
                        "scratchpad"
                        if isinstance(effect, LaunchScratchpad)
                        else "osk"
                        if isinstance(effect, (SetOskEnabled, RefreshOskTheme))
                        else "hyprland"
                    )
                    code = (
                        "scratchpad_launch_failed"
                        if isinstance(effect, LaunchScratchpad)
                        else "osk_theme_refresh_failed"
                        if isinstance(effect, RefreshOskTheme)
                        else "osk_transition_failed"
                        if isinstance(effect, SetOskEnabled)
                        else "reconcile_required"
                    )
                    self._diagnostic(component, code)
                    if isinstance(event, InputRecoveryRequested):
                        raise ValueError("input_recovery_incomplete") from None
                    if isinstance(effect, (SetInternalInputsEnabled, ApplyRotation)):
                        await self._reconcile_compositor_serialized()
            elif isinstance(effect, ReconcileObservedState):
                await self._request_observed_state_reconciliation()
            elif isinstance(effect, Diagnostic):
                self._diagnostic("tablet_switch", effect.code)

    async def _capture(self, effect: CaptureText) -> None:
        try:
            await self._coordinator.apply(effect)
        except Exception:
            self._diagnostic("ocr", "ocr_capture_failed")

    def _diagnostic(self, component: str, code: str) -> None:
        self._emit({"component": component, "code": code})

    async def _monitor_orientation(self) -> None:
        assert self._orientation_source is not None
        reconciliation_generation = 0
        while True:
            session: OrientationSession | None = None
            try:
                session = await self._orientation_source.connect()
                await self._accept(session.current_event)
                events = session.events()
                while True:
                    event, reconciliation_generation = await self._next_event_or_reconcile(
                        events, reconciliation_generation
                    )
                    if event is None:
                        break
                    assert isinstance(event, (OrientationChanged, OrientationLockChanged))
                    await self._accept(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnostic("orientation_sensor", "orientation_sensor_unavailable")
                await asyncio.sleep(self._retry_delay)
            finally:
                if session is not None:
                    await session.close()

    async def _monitor_pen_actions(self) -> None:
        assert self._pen_action_source is not None
        reconciliation_generation = 0
        while True:
            session: PenActionSession | None = None
            try:
                session = await self._pen_action_source.connect()
                self._pen_backoff.success()
                self._access_denied.discard("pen_action")
                events = session.events()
                while True:
                    event, reconciliation_generation = await self._next_event_or_reconcile(
                        events, reconciliation_generation
                    )
                    if event is None:
                        break
                    assert isinstance(event, PenActionRequested)
                    await self._accept(event)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                denied = isinstance(error, InputAccessDenied)
                if denied:
                    self._access_denied.add("pen_action")
                code = "device_permission_denied" if denied else "pen_action_source_unavailable"
                report, delay = self._pen_backoff.failure(code, slow=denied)
                if report:
                    self._diagnostic("pen_action", code)
                await self._sleep(delay)
            finally:
                if session is not None:
                    await session.close()

    async def _monitor_resume(self) -> None:
        assert self._resume_source is not None
        while True:
            session: ResumeSession | None = None
            try:
                session = await self._resume_source.connect()
                async for event in session.events():
                    await self._accept(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnostic("resume", "resume_monitor_unavailable")
                await asyncio.sleep(self._retry_delay)
            finally:
                if session is not None:
                    await session.close()

    async def _monitor_topology(self) -> None:
        assert self._monitor_source is not None
        while True:
            session: MonitorSession | None = None
            try:
                session = await self._monitor_source.connect()
                async for event in session.events():
                    await self._accept(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._diagnostic("hyprland", "monitor_topology_unavailable")
                await asyncio.sleep(self._retry_delay)
            finally:
                if session is not None:
                    await session.close()

    async def _request_observed_state_reconciliation(self) -> None:
        async with self._reconciliation_condition:
            self._reconciliation_generation += 1
            self._reconciliation_condition.notify_all()

    async def _next_event_or_reconcile(
        self, events: AsyncIterator[object], reconciliation_generation: int
    ) -> tuple[object | None, int]:
        event_task = asyncio.create_task(self._next(events))
        reconciliation_task = asyncio.create_task(
            self._wait_for_reconciliation(reconciliation_generation)
        )
        tasks = (event_task, reconciliation_task)
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if reconciliation_task in done:
                return None, reconciliation_task.result()
            return event_task.result(), reconciliation_generation
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_for_reconciliation(self, reconciliation_generation: int) -> int:
        async with self._reconciliation_condition:
            await self._reconciliation_condition.wait_for(
                lambda: self._reconciliation_generation > reconciliation_generation
            )
            return self._reconciliation_generation

    async def _next(self, events: AsyncIterator[object]) -> object:
        return await anext(events)
