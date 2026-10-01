"""Regression: an unreadable evdev node must not look like a disconnect.

Without the ``input`` group the runtime logged ``device_disconnected`` and
``pen_action_source_unavailable`` about forty times a second for a week while posture detection
was silently dead. These tests pin a distinct code, one report per state change, a slow retry,
and laptop-mode convergence.
"""

import asyncio

import pytest

from yoga_deck.adapters.pen_action_runtime import PenActionPermissionError
from yoga_deck.adapters.tablet_switch import TabletSwitchPermissionError
from yoga_deck.core import SetInternalInputsEnabled, TabletModeChanged
from yoga_deck.runtime import INPUT_ACCESS_DENIED, PostureRuntime, ReconnectBackoff


class FakeCoordinator:
    def __init__(self) -> None:
        self.effects = []

    async def recover_inputs(self):
        pass

    async def reconcile(self):
        pass

    async def apply(self, effect):
        self.effects.append(effect)


class Session:
    def __init__(self, current, events=(), *, disconnect=False) -> None:
        self.current_enabled = current
        self._events = events
        self._disconnect = disconnect

    async def events(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield TabletModeChanged(event) if isinstance(event, bool) else event
        if self._disconnect:
            raise OSError("private device path")
        await asyncio.Future()

    async def close(self):
        pass


class ScriptedSource:
    """Replay a script of sessions and failures, then fail with ``then`` forever."""

    def __init__(self, script, then) -> None:
        self.script = list(script)
        self.then = then
        self.connects = 0

    async def connect(self):
        self.connects += 1
        step = self.script.pop(0) if self.script else self.then
        if isinstance(step, Exception):
            raise step
        if step is None:
            await asyncio.Future()
        return step


class FakeSleep:
    """A fake clock: records requested delays and lets the test stop after ``limit`` waits."""

    def __init__(self, limit: int) -> None:
        self.delays = []
        self.limit = limit
        self.reached = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self.limit:
            self.reached.set()
            await asyncio.Future()
        await asyncio.sleep(0)


def _switch_denied():
    return TabletSwitchPermissionError("tablet_switch_permission_denied")


async def _run_until(runtime: PostureRuntime, sleep: FakeSleep) -> None:
    task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(sleep.reached.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_backoff_reports_each_new_failure_once_and_caps_slow_retries() -> None:
    backoff = ReconnectBackoff(0.5, 30.0)

    denied = [backoff.failure("device_permission_denied", slow=True) for _ in range(9)]
    assert [report for report, _ in denied] == [True] + [False] * 8
    assert [delay for _, delay in denied] == [0.5, 1, 2, 4, 8, 16, 30, 30, 30]

    assert backoff.failure("device_disconnected", slow=False) == (True, 0.5)
    assert backoff.failure("device_disconnected", slow=False) == (False, 0.5)
    backoff.success()
    assert backoff.failure("device_disconnected", slow=False) == (True, 0.5)


@pytest.mark.asyncio
async def test_switch_permission_denial_backs_off_reports_once_and_converges_to_laptop() -> None:
    diagnostics = []
    coordinator = FakeCoordinator()
    sleep = FakeSleep(limit=10)
    # Folded first, then the node becomes unreadable (e.g. the session lost its ACL).
    source = ScriptedSource([Session(True, disconnect=True)], then=_switch_denied())
    runtime = PostureRuntime(source, coordinator, diagnostics.append, retry_delay=0.5, sleep=sleep)

    await _run_until(runtime, sleep)

    assert diagnostics == [
        {"component": "tablet_switch", "code": "device_disconnected"},
        {"component": "tablet_switch", "code": "device_permission_denied"},
    ]
    # No tight loop: the disconnect keeps its short delay, the denial doubles to the cap.
    assert sleep.delays == [0.5, 0.5, 1, 2, 4, 8, 16, 30, 30, 30]
    assert runtime.state.tablet_mode_observed is False
    assert runtime.state.internal_inputs_enabled is True
    assert runtime.state.osk_enabled is False
    enabled = [e.enabled for e in coordinator.effects if isinstance(e, SetInternalInputsEnabled)]
    assert enabled[-1] is True
    assert runtime.degraded == INPUT_ACCESS_DENIED
    assert "private" not in repr(diagnostics)


@pytest.mark.asyncio
async def test_switch_access_regained_clears_degraded_state() -> None:
    diagnostics = []
    sleep = FakeSleep(limit=3)
    source = ScriptedSource(
        [_switch_denied(), _switch_denied(), Session(False, disconnect=True)],
        then=_switch_denied(),
    )
    runtime = PostureRuntime(
        source, FakeCoordinator(), diagnostics.append, retry_delay=0.5, sleep=sleep
    )
    task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(sleep.reached.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [d["code"] for d in diagnostics] == [
        "device_permission_denied",
        "device_disconnected",
    ]
    # A successful open resets the backoff, so the next failure starts short again.
    assert sleep.delays == [0.5, 1, 0.5]


@pytest.mark.asyncio
async def test_switch_denial_at_startup_keeps_the_safe_laptop_defaults() -> None:
    diagnostics = []
    coordinator = FakeCoordinator()
    sleep = FakeSleep(limit=4)
    runtime = PostureRuntime(
        ScriptedSource([], then=_switch_denied()),
        coordinator,
        diagnostics.append,
        retry_delay=0.5,
        sleep=sleep,
    )

    await _run_until(runtime, sleep)

    assert diagnostics == [{"component": "tablet_switch", "code": "device_permission_denied"}]
    assert runtime.state.internal_inputs_enabled is True
    assert runtime.state.tablet_mode_observed is False
    assert not any(
        isinstance(e, SetInternalInputsEnabled) and not e.enabled for e in coordinator.effects
    )


@pytest.mark.asyncio
async def test_pen_permission_denial_is_distinct_and_backs_off() -> None:
    diagnostics = []
    sleep = FakeSleep(limit=6)
    pen = ScriptedSource([], then=PenActionPermissionError("pen_action_permission_denied"))
    runtime = PostureRuntime(
        ScriptedSource([Session(False)], then=None),
        FakeCoordinator(),
        diagnostics.append,
        pen_action_source=pen,
        retry_delay=0.5,
        sleep=sleep,
    )

    await _run_until(runtime, sleep)

    assert diagnostics == [{"component": "pen_action", "code": "device_permission_denied"}]
    assert sleep.delays == [0.5, 1, 2, 4, 8, 16]
    assert pen.connects == 6
    assert runtime.degraded == INPUT_ACCESS_DENIED
    assert runtime.state.internal_inputs_enabled is True


@pytest.mark.asyncio
async def test_missing_pen_is_reported_once_but_keeps_the_short_retry() -> None:
    diagnostics = []
    sleep = FakeSleep(limit=4)
    pen = ScriptedSource([], then=RuntimeError("pen_action_source_unavailable"))
    runtime = PostureRuntime(
        ScriptedSource([Session(False)], then=None),
        FakeCoordinator(),
        diagnostics.append,
        pen_action_source=pen,
        retry_delay=0.5,
        sleep=sleep,
    )

    await _run_until(runtime, sleep)

    assert diagnostics == [{"component": "pen_action", "code": "pen_action_source_unavailable"}]
    assert sleep.delays == [0.5, 0.5, 0.5, 0.5]
    assert runtime.degraded is None
