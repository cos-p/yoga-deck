"""Optional scratchpad launch boundary with no shell or document path."""

from __future__ import annotations

import asyncio
import os
from typing import Protocol


class ProcessSpawner(Protocol):
    async def spawn(
        self,
        args: tuple[str, ...],
        environment: dict[str, str],
        *,
        start_new_session: bool,
    ) -> object: ...


class ScratchpadError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AsyncioProcessSpawner:
    async def spawn(
        self,
        args: tuple[str, ...],
        environment: dict[str, str],
        *,
        start_new_session: bool,
    ) -> object:
        return await asyncio.create_subprocess_exec(
            *args,
            env=environment,
            start_new_session=start_new_session,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )


class ScratchpadAdapter:
    """Launch the already-installed Xournal++ without selecting or creating a document."""

    def __init__(self, spawner: ProcessSpawner | None = None) -> None:
        self._spawner = spawner or AsyncioProcessSpawner()

    async def launch(self) -> None:
        try:
            environment = dict(os.environ)
            environment["GDK_BACKEND"] = "wayland"
            await self._spawner.spawn(("xournalpp",), environment, start_new_session=True)
        except OSError as error:
            raise ScratchpadError("scratchpad_launch_failed") from error
