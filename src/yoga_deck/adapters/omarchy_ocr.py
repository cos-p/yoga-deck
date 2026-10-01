"""Privacy-safe boundary for Omarchy's interactive OCR command."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from contextlib import suppress
from typing import Protocol

from yoga_deck.adapters.async_lifecycle import complete_before_cancelling


class OcrCompletedProcess(Protocol):
    returncode: int


class OcrCommandRunner(Protocol):
    def run(self, args: list[str], timeout: float) -> OcrCompletedProcess: ...


class OmarchyOcrError(RuntimeError):
    """Stable failure that never includes captured text or command output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SubprocessOcrRunner:
    def run(self, args: list[str], timeout: float) -> OcrCompletedProcess:
        return subprocess.run(
            args,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )


class OmarchyOcrAdapter:
    """Launch a user-selected OCR capture without reading its source or result."""

    def __init__(self, runner: OcrCommandRunner | None = None, timeout: float = 120.0) -> None:
        self._runner = runner or SubprocessOcrRunner()
        self._timeout = timeout

    def capture_text(self) -> None:
        try:
            result = self._runner.run(["omarchy", "capture", "text"], self._timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OmarchyOcrError("ocr_capture_failed") from error
        if result.returncode != 0:
            raise OmarchyOcrError("ocr_capture_failed")


class AsyncOmarchyOcrAdapter:
    """Own a cancellable OCR process group without blocking posture event handling."""

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout

    async def capture_text(self) -> None:
        # Shield process creation so cancellation cannot lose a newly spawned child.
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                "omarchy",
                "capture",
                "text",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        )
        process = None
        try:
            process = await asyncio.shield(spawning)
            result = await asyncio.wait_for(process.wait(), timeout=self._timeout)
            if result != 0:
                raise OmarchyOcrError("ocr_capture_failed")
        except BaseException as error:
            # Put acquisition and cleanup in the same protected operation: a second
            # cancellation during spawn must not discard the returned process handle.
            await complete_before_cancelling(self._stop_spawned(spawning))
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, Exception):
                raise OmarchyOcrError("ocr_capture_failed") from error
            raise

    async def _stop_spawned(self, spawning: asyncio.Task[asyncio.subprocess.Process]) -> None:
        try:
            process = await spawning
        except Exception:
            return  # No child was created, but retrieve the spawn failure.
        await self._stop(process)

    @staticmethod
    async def _stop(process: asyncio.subprocess.Process) -> None:
        # The region picker is a descendant of the command, so stopping only the leader
        # would leave a picker alive after runtime shutdown or a capture timeout.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
