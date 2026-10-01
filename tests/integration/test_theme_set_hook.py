"""The installed `theme-set` hook against a real transport socket.

The hook is the only part of the retint path that is not Python, so it is exercised as the
shell script Omarchy actually runs rather than through an in-process stub.
"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from yoga_deck.adapters.shell_transport import ShellCommand, ShellTransportServer
from yoga_deck.core import State

pytestmark = pytest.mark.integration

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "theme-set.d" / "yoga-deck"


def run_hook(runtime_dir: Path, theme: str = "nord") -> subprocess.CompletedProcess:
    """Invoke the hook the way `omarchy-hook` does: bash, theme name as the first argument."""

    return subprocess.run(
        ["bash", str(HOOK), theme],
        env={"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": str(runtime_dir)},
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.asyncio
async def test_hook_delivers_theme_changed_to_a_listening_runtime(tmp_path) -> None:
    commands: list[ShellCommand] = []

    async def handle(command: ShellCommand) -> None:
        commands.append(command)

    socket_path = tmp_path / "yoga-deck" / "shell.sock"
    socket_path.parent.mkdir(mode=0o700, parents=True)
    server = ShellTransportServer(
        socket_path=socket_path,
        state_provider=State,
        command_handler=handle,
    )
    await server.start()
    try:
        result = await asyncio.to_thread(run_hook, tmp_path)
    finally:
        await server.close()

    assert result.returncode == 0
    assert commands == [ShellCommand("theme_changed")]


def test_hook_exits_cleanly_and_silently_with_no_runtime_listening(tmp_path) -> None:
    # A theme change must never fail because Yoga Deck is not running.
    result = run_hook(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_hook_has_no_shared_tmp_fallback_when_runtime_dir_is_missing() -> None:
    result = subprocess.run(
        ["bash", str(HOOK), "nord"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert 'or "/tmp"' not in HOOK.read_text(encoding="utf-8")


def test_hook_does_not_forward_the_theme_name_or_any_other_argument(tmp_path) -> None:
    """The runtime owns palette resolution, so the wire carries no theme identity."""

    received: list[bytes] = []
    socket_path = tmp_path / "yoga-deck" / "shell.sock"
    socket_path.parent.mkdir(mode=0o700, parents=True)

    async def serve() -> None:
        async def handle(reader, writer):
            received.append(await reader.readline())
            writer.write(b'{"ok": true}\n')
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        async with server:
            await asyncio.to_thread(run_hook, tmp_path, "a-theme-named-after-the-user")

    asyncio.run(serve())

    assert json.loads(received[0]) == {"command": "theme_changed"}
