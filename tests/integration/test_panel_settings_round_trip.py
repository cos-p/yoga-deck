"""The whole path a panel control takes, with no part of it faked.

A control sends text over the socket; the runtime validates it against the settings table,
writes the machine-owned file, and plans the respawn that makes the change visible; the next
snapshot shows the new value and names the file it came from. Every layer between is real
here, because the point of this change was that the layers agree.
"""

import asyncio
import json
from pathlib import Path

import pytest

from yoga_deck.adapters.shell_transport import ShellTransportServer, request_sync
from yoga_deck.adapters.user_settings import (
    LEGACY_PEN_FILENAME,
    OVERRIDES_FILENAME,
    SETTINGS_FILENAME,
    UserSettingsSource,
)
from yoga_deck.core import RefreshOskTheme, TabletModeChanged
from yoga_deck.runtime import PostureRuntime

pytestmark = pytest.mark.integration


class RecordingCoordinator:
    def __init__(self) -> None:
        self.effects: list[object] = []

    async def apply(self, effect: object) -> None:
        self.effects.append(effect)

    async def reconcile(self) -> None:
        return None


class NoSwitch:
    async def connect(self):  # pragma: no cover - the runtime never runs here
        raise AssertionError("this test drives the runtime by hand")


def _source(root: Path) -> UserSettingsSource:
    return UserSettingsSource(
        root / SETTINGS_FILENAME,
        legacy_pen_path=root / LEGACY_PEN_FILENAME,
        overrides_path=root / OVERRIDES_FILENAME,
    )


async def _serve(root: Path, coordinator: RecordingCoordinator, socket_path: Path):
    settings = _source(root)
    runtime = PostureRuntime(NoSwitch(), coordinator, settings_store=settings)
    # Folded, which is the only posture in which a settings change means anything now.
    await runtime._accept(TabletModeChanged(enabled=True))
    server = ShellTransportServer(
        socket_path=socket_path,
        state_provider=lambda: runtime.state,
        command_handler=runtime.handle_shell_command,
        config_provider=settings.snapshot,
    )
    await server.start()
    return server


async def _read_config(socket_path: Path) -> dict:
    """Every setting keyed by name, exactly as the panel receives it."""

    response = await asyncio.to_thread(
        request_sync, {"command": "get_config"}, socket_path=socket_path
    )
    return {entry["name"]: entry for entry in response["config"]["settings"]}


async def _send(socket_path: Path, payload: dict) -> dict:
    return await asyncio.to_thread(request_sync, payload, socket_path=socket_path)


@pytest.mark.asyncio
async def test_a_panel_control_changes_a_setting_and_sees_the_change_it_made(tmp_path) -> None:
    socket_path = tmp_path / "shell.sock"
    coordinator = RecordingCoordinator()
    server = await _serve(tmp_path, coordinator, socket_path)
    try:
        before = await _read_config(socket_path)
        stored = await _send(
            socket_path,
            # As the panel sends it: text on argv, because that is all a process gets.
            {"command": "set_config", "setting": "osk.landscape_height", "value": "420"},
        )
        after = await _read_config(socket_path)
    finally:
        await server.close()

    assert before["osk.landscape_height"]["value"] == 280
    assert before["osk.landscape_height"]["source"] is None
    assert stored["ok"] is True
    assert after["osk.landscape_height"]["value"] == 420
    assert after["osk.landscape_height"]["source"] == OVERRIDES_FILENAME
    # The keyboard rebuilds its command line on respawn, so this is the change landing.
    assert any(isinstance(effect, RefreshOskTheme) for effect in coordinator.effects)


@pytest.mark.asyncio
async def test_a_control_never_rewrites_the_file_a_person_edits(tmp_path) -> None:
    hand_written = tmp_path / SETTINGS_FILENAME
    body = "# my notes\n[osk]\nlandscape_height = 300  # measured by hand\n"
    hand_written.write_text(body, encoding="utf-8")
    socket_path = tmp_path / "shell.sock"
    server = await _serve(tmp_path, RecordingCoordinator(), socket_path)
    try:
        await _send(
            socket_path,
            {"command": "set_config", "setting": "osk.landscape_height", "value": "420"},
        )
        after = await _read_config(socket_path)
    finally:
        await server.close()

    assert hand_written.read_text(encoding="utf-8") == body
    assert after["osk.landscape_height"]["value"] == 420
    assert after["osk.landscape_height"]["source"] == OVERRIDES_FILENAME


@pytest.mark.asyncio
async def test_a_reset_hands_the_setting_back_to_the_hand_written_file(tmp_path) -> None:
    (tmp_path / SETTINGS_FILENAME).write_text("[osk]\nlandscape_height = 300\n", encoding="utf-8")
    socket_path = tmp_path / "shell.sock"
    server = await _serve(tmp_path, RecordingCoordinator(), socket_path)
    try:
        await _send(
            socket_path,
            {"command": "set_config", "setting": "osk.landscape_height", "value": "420"},
        )
        await _send(socket_path, {"command": "set_config", "setting": "osk.landscape_height"})
        after = await _read_config(socket_path)
    finally:
        await server.close()

    assert after["osk.landscape_height"]["value"] == 300
    assert after["osk.landscape_height"]["source"] == SETTINGS_FILENAME
    # Nothing overridden any more, so the machine's file is gone rather than left empty.
    assert not (tmp_path / OVERRIDES_FILENAME).exists()


@pytest.mark.asyncio
async def test_a_refused_value_leaves_the_files_and_the_keyboard_alone(tmp_path) -> None:
    socket_path = tmp_path / "shell.sock"
    coordinator = RecordingCoordinator()
    server = await _serve(tmp_path, coordinator, socket_path)
    try:
        response = await _send(
            socket_path,
            {"command": "set_config", "setting": "osk.landscape_height", "value": "5000"},
        )
    finally:
        await server.close()

    assert response == {"ok": False, "error": "invalid_value"}
    assert not (tmp_path / OVERRIDES_FILENAME).exists()
    # The fold itself planned effects; nothing planned a respawn, which is the change landing.
    assert not any(isinstance(effect, RefreshOskTheme) for effect in coordinator.effects)


@pytest.mark.asyncio
async def test_the_snapshot_a_panel_reads_survives_being_json(tmp_path) -> None:
    """The panel parses this in QML, so anything that will not encode is a broken control."""

    socket_path = tmp_path / "shell.sock"
    server = await _serve(tmp_path, RecordingCoordinator(), socket_path)
    try:
        response = await _send(socket_path, {"command": "get_config"})
    finally:
        await server.close()

    assert json.loads(json.dumps(response)) == response
    assert response["config"]["overrides_file"] == OVERRIDES_FILENAME
