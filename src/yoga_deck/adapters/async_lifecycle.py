"""Cancellation boundaries for operations that must finish before releasing ownership."""

import asyncio
from collections.abc import Awaitable
from contextlib import suppress


async def complete_before_cancelling[T](operation: Awaitable[T]) -> T:
    """Keep an in-flight operation owned until it settles, even after repeated cancellation.

    Cancelling an asyncio wrapper cannot stop a worker thread or undo a mutation already sent
    to a compositor. Callers must retain their ordering locks until that operation completes,
    so subsequent recovery cannot be overtaken by stale work. Operations must be bounded at
    their adapter boundary (for example by the hyprctl subprocess timeout).
    """
    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(task)
        # Retrieve failures, but preserve the caller's cancellation as the outcome.
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        raise
