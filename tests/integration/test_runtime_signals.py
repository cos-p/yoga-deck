"""Service stop signals run the runtime's in-process shutdown recovery.

systemd stops ``yoga-deck.service`` with SIGTERM (logout, ``systemctl --user stop``, restart).
The runtime must treat that, and SIGINT, as an orderly shutdown: cancel its producers, let the
reducer plan internal-input recovery and OSK teardown, apply them once, and exit 0. The unit's
``ExecStopPost`` recovery remains a backup, not the primary path.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from yoga_deck import runtime_main
from yoga_deck.core import SetInternalInputsEnabled, SetOskEnabled, TabletModeChanged
from yoga_deck.runtime import PostureRuntime

pytestmark = pytest.mark.integration


class FoldedSession:
    """A tablet switch that reports folded and then waits for events forever."""

    current_enabled = True

    def __init__(self) -> None:
        self.closed = False

    async def events(self):
        await asyncio.Future()
        yield TabletModeChanged(True)  # pragma: no cover - never reached

    async def close(self) -> None:
        self.closed = True


class FoldedSource:
    def __init__(self) -> None:
        self.session = FoldedSession()
        self.connected = False

    async def connect(self):
        if self.connected:
            await asyncio.Future()
        self.connected = True
        return self.session


class RecordingCoordinator:
    """Tracks the internal-input and OSK intent a real compositor would end up with."""

    def __init__(self, *, shutdown_gate: asyncio.Event | None = None) -> None:
        self.inputs_enabled = True
        self.osk_enabled = False
        self.shutdown_recoveries = 0
        self.effects = []
        self._shutdown_gate = shutdown_gate
        self.shutdown_started = asyncio.Event()

    async def recover_inputs(self) -> None:
        self.inputs_enabled = True

    async def reconcile(self) -> None:
        pass

    async def apply(self, effect) -> None:
        self.effects.append(effect)
        if isinstance(effect, SetInternalInputsEnabled):
            if effect.enabled and not self.inputs_enabled:
                self.shutdown_recoveries += 1
                self.shutdown_started.set()
                if self._shutdown_gate is not None:
                    await self._shutdown_gate.wait()
            self.inputs_enabled = effect.enabled
        elif isinstance(effect, SetOskEnabled):
            self.osk_enabled = effect.enabled


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def _folded_runtime(coordinator: RecordingCoordinator) -> tuple[PostureRuntime, FoldedSource]:
    source = FoldedSource()
    runtime = PostureRuntime(source, coordinator, retry_delay=0, reconcile_interval=3600)
    return runtime, source


@pytest.mark.asyncio
async def test_sigterm_recovers_internal_inputs_and_exits_cleanly() -> None:
    coordinator = RecordingCoordinator()
    runtime, source = _folded_runtime(coordinator)
    diagnostics = []
    serving = asyncio.create_task(
        runtime_main.run_until_signalled(runtime.run(), emit=diagnostics.append)
    )
    await _wait_until(lambda: not coordinator.inputs_enabled and coordinator.osk_enabled)

    os.kill(os.getpid(), signal.SIGTERM)

    assert await asyncio.wait_for(serving, 5) == 0
    assert coordinator.inputs_enabled is True
    assert coordinator.osk_enabled is False
    assert coordinator.shutdown_recoveries == 1
    assert runtime.state.shutdown_requested
    assert source.session.closed
    assert all(set(item) == {"component", "code"} for item in diagnostics)


@pytest.mark.asyncio
async def test_sigint_takes_the_same_orderly_path() -> None:
    coordinator = RecordingCoordinator()
    runtime, _ = _folded_runtime(coordinator)
    serving = asyncio.create_task(
        runtime_main.run_until_signalled(runtime.run(), emit=list().append)
    )
    await _wait_until(lambda: not coordinator.inputs_enabled)

    os.kill(os.getpid(), signal.SIGINT)

    assert await asyncio.wait_for(serving, 5) == 0
    assert coordinator.inputs_enabled is True
    assert coordinator.shutdown_recoveries == 1


@pytest.mark.asyncio
async def test_repeated_signals_during_shutdown_do_not_skip_or_repeat_recovery() -> None:
    gate = asyncio.Event()
    coordinator = RecordingCoordinator(shutdown_gate=gate)
    runtime, _ = _folded_runtime(coordinator)
    diagnostics = []
    serving = asyncio.create_task(
        runtime_main.run_until_signalled(runtime.run(), emit=diagnostics.append)
    )
    await _wait_until(lambda: not coordinator.inputs_enabled)

    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(coordinator.shutdown_started.wait(), 2)
    # Recovery is in flight; further stop requests must neither cancel nor restart it.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGTERM):
        os.kill(os.getpid(), sig)
        await asyncio.sleep(0.01)
    assert not serving.done()
    gate.set()

    assert await asyncio.wait_for(serving, 5) == 0
    assert coordinator.inputs_enabled is True
    assert coordinator.osk_enabled is False
    assert coordinator.shutdown_recoveries == 1
    assert {"component": "runtime", "code": "shutdown_in_progress"} in diagnostics


@pytest.mark.asyncio
async def test_a_stuck_shutdown_is_bounded_and_reported() -> None:
    gate = asyncio.Event()
    coordinator = RecordingCoordinator(shutdown_gate=gate)
    runtime, _ = _folded_runtime(coordinator)
    diagnostics = []
    abandoned = []
    serving = asyncio.create_task(
        runtime_main.run_until_signalled(
            runtime.run(),
            emit=diagnostics.append,
            shutdown_timeout=0.1,
            on_timeout=abandoned.append,
        )
    )
    await _wait_until(lambda: not coordinator.inputs_enabled)

    os.kill(os.getpid(), signal.SIGTERM)

    code = await asyncio.wait_for(serving, 5)
    assert code == runtime_main.EXIT_SHUTDOWN_TIMED_OUT != 0
    assert abandoned == [code]
    assert {"component": "runtime", "code": "shutdown_timed_out"} in diagnostics
    # Let the abandoned shutdown settle so the test loop closes cleanly.
    gate.set()
    await _wait_until(lambda: coordinator.inputs_enabled)


@pytest.mark.asyncio
async def test_signal_handlers_are_removed_after_the_runtime_stops() -> None:
    async def finishes() -> None:
        return None

    assert await runtime_main.run_until_signalled(finishes(), emit=list().append) == 0
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


@pytest.mark.asyncio
async def test_a_runtime_crash_is_not_masked_as_a_clean_stop() -> None:
    async def crashes() -> None:
        raise RuntimeError("private detail")

    with pytest.raises(RuntimeError):
        await runtime_main.run_until_signalled(crashes(), emit=list().append)


def test_entry_point_installs_the_handlers(monkeypatch) -> None:
    recorded = []

    async def fake_run() -> None:
        try:
            asyncio.get_running_loop().call_soon(os.kill, os.getpid(), signal.SIGTERM)
            await asyncio.Future()
        finally:
            recorded.append("shutdown")

    monkeypatch.setattr(runtime_main, "_run", fake_run)

    assert runtime_main.main() == 0
    assert recorded == ["shutdown"]


_CHILD = textwrap.dedent(
    """
    import asyncio, json, sys

    from yoga_deck import runtime_main
    from yoga_deck.core import SetInternalInputsEnabled, SetOskEnabled
    from yoga_deck.runtime import PostureRuntime


    def say(**fields):
        print(json.dumps(fields), flush=True)


    class Session:
        current_enabled = True

        async def events(self):
            await asyncio.Future()
            yield None

        async def close(self):
            say(event="switch_closed")


    class Source:
        connected = False

        async def connect(self):
            if self.connected:
                await asyncio.Future()
            self.connected = True
            return Session()


    class Coordinator:
        folded = False

        async def recover_inputs(self):
            say(event="startup_recovery")

        async def reconcile(self):
            pass

        async def apply(self, effect):
            if isinstance(effect, SetInternalInputsEnabled):
                if effect.enabled and self.folded:
                    # A slow compositor: repeated stop signals arrive mid-recovery.
                    await asyncio.sleep(float(sys.argv[1]))
                self.folded = not effect.enabled
                say(event="inputs", enabled=effect.enabled, sequence=effect.sequence)
            elif isinstance(effect, SetOskEnabled):
                say(event="osk", enabled=effect.enabled, sequence=effect.sequence)


    async def fake_run():
        runtime = PostureRuntime(Source(), Coordinator(), retry_delay=0, reconcile_interval=3600)
        await runtime.run()


    runtime_main._run = fake_run
    sys.exit(runtime_main.main())
    """
)


def _read_until(process: subprocess.Popen, predicate, timeout: float = 10.0) -> list[dict]:
    lines = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        lines.append(json.loads(line))
        if predicate(lines[-1]):
            return lines
    raise AssertionError(f"condition not reached: {lines}")


@pytest.mark.parametrize("repeat", [1, 3])
def test_real_sigterm_to_the_entry_point_process(tmp_path, repeat) -> None:
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    slow_recovery = "0.3" if repeat > 1 else "0"
    process = subprocess.Popen(
        [sys.executable, str(script), slow_recovery],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _read_until(process, lambda item: item == {"event": "osk", "enabled": True, "sequence": 2})
        for _ in range(repeat):
            process.send_signal(signal.SIGTERM)
            time.sleep(0.05)
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    events = [json.loads(line) for line in stdout.splitlines()]
    assert process.returncode == 0, stderr
    inputs = [item for item in events if item["event"] == "inputs"]
    assert inputs[-1] == {"event": "inputs", "enabled": True, "sequence": 3}
    assert [item for item in inputs if item["sequence"] == 3] == [inputs[-1]]
    assert {"event": "osk", "enabled": False, "sequence": 3} in events
    assert {"event": "switch_closed"} in events
    for line in stderr.splitlines():
        assert set(json.loads(line)) == {"component", "code"}
