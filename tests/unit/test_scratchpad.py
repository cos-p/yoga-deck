from dataclasses import dataclass

import pytest

from yoga_deck.adapters.scratchpad import ScratchpadAdapter, ScratchpadError


@dataclass
class Process:
    pid: int = 42


class FakeSpawner:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    async def spawn(self, args, environment, *, start_new_session):
        self.calls.append((args, environment, start_new_session))
        if self.error is not None:
            raise self.error
        return Process()


@pytest.mark.asyncio
async def test_scratchpad_adapter_uses_fixed_argv_without_a_path_or_shell() -> None:
    spawner = FakeSpawner()

    await ScratchpadAdapter(spawner).launch()

    args, environment, start_new_session = spawner.calls[0]
    assert args == ("xournalpp",)
    assert environment["GDK_BACKEND"] == "wayland"
    assert start_new_session is True


@pytest.mark.asyncio
async def test_scratchpad_adapter_redacts_launch_failure() -> None:
    adapter = ScratchpadAdapter(FakeSpawner(error=OSError("private app path")))

    with pytest.raises(ScratchpadError, match="scratchpad_launch_failed"):
        await adapter.launch()
