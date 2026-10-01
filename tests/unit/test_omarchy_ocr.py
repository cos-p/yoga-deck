from dataclasses import dataclass

import pytest

from yoga_deck.adapters.omarchy_ocr import OmarchyOcrAdapter, OmarchyOcrError


@dataclass
class Completed:
    returncode: int = 0


class FakeRunner:
    def __init__(self, result: Completed | None = None, error: Exception | None = None) -> None:
        self.result = result or Completed()
        self.error = error
        self.calls = []

    def run(self, args, timeout):
        self.calls.append((args, timeout))
        if self.error is not None:
            raise self.error
        return self.result


def test_ocr_adapter_uses_stable_omarchy_command_without_reading_output() -> None:
    runner = FakeRunner()

    OmarchyOcrAdapter(runner, timeout=90).capture_text()

    assert runner.calls == [(["omarchy", "capture", "text"], 90)]


def test_ocr_adapter_redacts_command_failure() -> None:
    adapter = OmarchyOcrAdapter(FakeRunner(Completed(returncode=1)))

    with pytest.raises(OmarchyOcrError, match="ocr_capture_failed"):
        adapter.capture_text()


def test_ocr_adapter_redacts_runner_error() -> None:
    adapter = OmarchyOcrAdapter(FakeRunner(error=OSError("private command output")))

    with pytest.raises(OmarchyOcrError, match="ocr_capture_failed"):
        adapter.capture_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["complete", "cancel", "timeout", "failure"])
async def test_async_ocr_keeps_loop_responsive_and_reaps_its_process(monkeypatch, outcome):
    import asyncio

    from yoga_deck.adapters.omarchy_ocr import AsyncOmarchyOcrAdapter

    entered = asyncio.Event()
    exited = asyncio.Event()
    killed = []
    calls = []

    class Process:
        pid = 12345
        returncode = None

        async def wait(self):
            entered.set()
            await exited.wait()
            return self.returncode

    process = Process()

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    def kill_group(pid, sig):
        killed.append(pid)
        process.returncode = -9
        exited.set()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("os.killpg", kill_group)
    adapter = AsyncOmarchyOcrAdapter(timeout=0.02 if outcome == "timeout" else 10)
    task = asyncio.create_task(adapter.capture_text())
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()
    if outcome in ("complete", "failure"):
        process.returncode = 0 if outcome == "complete" else 1
        exited.set()
    elif outcome == "cancel":
        task.cancel()
    if outcome == "complete":
        await task
    elif outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(OmarchyOcrError, match="ocr_capture_failed"):
            await task
    assert exited.is_set()
    assert bool(killed) is (outcome != "complete")
    assert calls[0][0] == ("omarchy", "capture", "text")
    assert calls[0][1]["start_new_session"] is True
    assert calls[0][1]["stdout"] == asyncio.subprocess.DEVNULL
    assert calls[0][1]["stderr"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_cancellation_during_spawn_still_reaps_child_after_repeated_cancellation(monkeypatch):
    import asyncio

    from yoga_deck.adapters.omarchy_ocr import AsyncOmarchyOcrAdapter

    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Process:
        pid = 12345

        async def wait(self):
            await stopped.wait()
            return -9

    async def spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("os.killpg", lambda *args: stopped.set())
    task = asyncio.create_task(AsyncOmarchyOcrAdapter().capture_text())
    await entered.wait()
    task.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
