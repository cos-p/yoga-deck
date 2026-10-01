"""Exercise the real subprocess lifecycle with a harmless sleeping Python child."""

import asyncio
import sys

import pytest

from yoga_deck.adapters.omarchy_ocr import AsyncOmarchyOcrAdapter, OmarchyOcrError

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_ocr_child_is_reaped_on_timeout_or_shutdown(monkeypatch, cancel):
    real_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    processes = []

    async def fake_command(*args, **kwargs):
        assert args == ("omarchy", "capture", "text")
        process = await real_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_command)
    task = asyncio.create_task(
        AsyncOmarchyOcrAdapter(timeout=0.02 if not cancel else 10).capture_text()
    )
    try:
        await asyncio.wait_for(spawned.wait(), 2)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else OmarchyOcrError):
            await asyncio.wait_for(task, 2)
        assert processes[0].returncode is not None
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()
