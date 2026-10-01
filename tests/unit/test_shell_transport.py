import asyncio
import json

import pytest

from yoga_deck.adapters.shell_transport import (
    ShellCommand,
    ShellTransportServer,
    default_socket_path,
    request_sync,
    status_payload,
)
from yoga_deck.core import Orientation, State


def test_default_transport_fails_closed_without_a_private_runtime_directory(monkeypatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    with pytest.raises(RuntimeError, match="xdg_runtime_dir_unavailable"):
        default_socket_path()


def test_status_payload_exposes_only_reducer_owned_state() -> None:
    payload = status_payload(
        State(
            sequence=7,
            tablet_mode_observed=True,
            internal_inputs_enabled=False,
            applied_orientation=Orientation.LEFT,
            orientation_locked=True,
        )
    )

    assert payload == {
        "schema_version": 1,
        "available": True,
        "sequence": 7,
        "posture": "tablet",
        "inputs_enabled": False,
        "orientation": "left",
        "orientation_lock": True,
        "degraded": None,
        "error": None,
    }


@pytest.mark.asyncio
async def test_unix_transport_routes_commands_to_runtime_owner(tmp_path) -> None:
    commands = []
    state = State()

    async def handle(command: ShellCommand) -> None:
        commands.append(command)

    server = ShellTransportServer(
        socket_path=tmp_path / "shell.sock",
        state_provider=lambda: state,
        command_handler=handle,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(server.socket_path))
        writer.write(b'{"command":"set_lock","value":true}\n')
        await writer.drain()
        response = json.loads((await reader.readline()).decode())
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()

    assert response["ok"] is True
    assert commands == [ShellCommand("set_lock", True)]


def test_sync_client_reports_degraded_backend_without_safe_defaults(tmp_path) -> None:
    response = request_sync({"command": "status"}, socket_path=tmp_path / "missing.sock")

    assert response == {
        "schema_version": 1,
        "available": False,
        "error": "runtime_unavailable",
    }


@pytest.mark.asyncio
async def test_transport_accepts_theme_changed_and_rejects_a_theme_name(tmp_path) -> None:
    commands = []
    state = State()

    async def handle(command: ShellCommand) -> None:
        commands.append(command)

    server = ShellTransportServer(
        socket_path=tmp_path / "shell.sock",
        state_provider=lambda: state,
        command_handler=handle,
    )
    await server.start()
    try:
        accepted = await _round_trip(server.socket_path, b'{"command":"theme_changed"}\n')
        # The runtime re-resolves the palette itself, so a caller cannot name a theme and
        # cannot smuggle content through this command.
        rejected = await _round_trip(
            server.socket_path, b'{"command":"theme_changed","value":"nord"}\n'
        )
    finally:
        await server.close()

    assert accepted["ok"] is True
    assert rejected == {"ok": False, "error": "invalid_command"}
    assert commands == [ShellCommand("theme_changed")]


async def _round_trip(socket_path, payload: bytes) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.write(payload)
    await writer.drain()
    response = json.loads((await reader.readline()).decode())
    writer.close()
    await writer.wait_closed()
    return response


SNAPSHOT = {
    "schema_version": 1,
    "overrides_file": "config.local.toml",
    "settings": [{"name": "osk.landscape_height", "value": 280, "source": None}],
    "ignored": [],
}


def _config_server(tmp_path, commands: list, *, with_settings: bool = True) -> ShellTransportServer:
    async def handle(command: ShellCommand) -> None:
        commands.append(command)

    return ShellTransportServer(
        socket_path=tmp_path / "shell.sock",
        state_provider=State,
        command_handler=handle,
        config_provider=(lambda: SNAPSHOT) if with_settings else None,
    )


@pytest.mark.asyncio
async def test_a_client_can_read_every_setting_without_reading_a_file(tmp_path) -> None:
    server = _config_server(tmp_path, [])
    await server.start()
    try:
        response = await _round_trip(server.socket_path, b'{"command":"get_config"}\n')
    finally:
        await server.close()

    assert response["ok"] is True
    assert response["config"] == SNAPSHOT


@pytest.mark.asyncio
async def test_a_runtime_with_no_settings_source_says_so_rather_than_inventing_defaults(
    tmp_path,
) -> None:
    server = _config_server(tmp_path, [], with_settings=False)
    await server.start()
    try:
        response = await _round_trip(server.socket_path, b'{"command":"get_config"}\n')
    finally:
        await server.close()

    assert response == {"ok": False, "error": "settings_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", ["400", 400])
async def test_a_setting_arrives_as_the_scalar_the_file_holds(tmp_path, sent) -> None:
    """A control that can only send text and one that sends JSON must agree.

    The panel shells out through argv, so every value it sends is a string. Both forms go
    through the same table the settings file is read with, so neither can store what the
    other could not.
    """

    commands: list[ShellCommand] = []
    server = _config_server(tmp_path, commands)
    await server.start()
    try:
        await _round_trip(
            server.socket_path,
            json.dumps(
                {"command": "set_config", "setting": "osk.landscape_height", "value": sent}
            ).encode()
            + b"\n",
        )
    finally:
        await server.close()

    assert commands == [ShellCommand("set_config", 400, "osk.landscape_height")]


@pytest.mark.asyncio
async def test_a_choice_arrives_as_its_stored_word_not_as_an_enum(tmp_path) -> None:
    commands: list[ShellCommand] = []
    server = _config_server(tmp_path, commands)
    await server.start()
    try:
        await _round_trip(
            server.socket_path,
            b'{"command":"set_config","setting":"pen.far_button_action","value":"capture_text"}\n',
        )
    finally:
        await server.close()

    assert commands == [ShellCommand("set_config", "capture_text", "pen.far_button_action")]


@pytest.mark.asyncio
async def test_a_missing_value_asks_for_the_override_to_be_cleared(tmp_path) -> None:
    commands: list[ShellCommand] = []
    server = _config_server(tmp_path, commands)
    await server.start()
    try:
        response = await _round_trip(
            server.socket_path, b'{"command":"set_config","setting":"osk.font"}\n'
        )
    finally:
        await server.close()

    assert response["ok"] is True
    assert commands == [ShellCommand("set_config", None, "osk.font")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_body", "error"),
    [
        (
            b'{"command":"set_config","setting":"osk.landscape_height","value":5000}',
            "invalid_value",
        ),
        (
            b'{"command":"set_config","setting":"osk.landscape_height","value":"tall"}',
            "invalid_value",
        ),
        (b'{"command":"set_config","setting":"osk.key_popup","value":"maybe"}', "invalid_value"),
        (b'{"command":"set_config","setting":"osk.hieght","value":300}', "unknown_setting"),
        (b'{"command":"set_config","value":300}', "unknown_setting"),
    ],
)
async def test_a_value_the_file_could_not_hold_never_reaches_the_runtime(
    tmp_path, request_body, error
) -> None:
    """Refused at the door, like `rotate` already is, rather than stored and then discarded."""

    commands: list[ShellCommand] = []
    server = _config_server(tmp_path, commands)
    await server.start()
    try:
        response = await _round_trip(server.socket_path, request_body + b"\n")
    finally:
        await server.close()

    assert response == {"ok": False, "error": error}
    assert commands == []


def test_status_payload_relays_only_known_degraded_reasons() -> None:
    assert status_payload(State(), "input_access_denied")["degraded"] == "input_access_denied"
    assert status_payload(State(), "/dev/input/event71 denied")["degraded"] is None


@pytest.mark.asyncio
async def test_status_includes_runtime_degraded_reason(tmp_path) -> None:
    async def handle(command: ShellCommand) -> None:
        raise AssertionError("status must not reach the command handler")

    server = ShellTransportServer(
        socket_path=tmp_path / "shell.sock",
        state_provider=State,
        command_handler=handle,
        degraded_provider=lambda: "input_access_denied",
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(server.socket_path))
        writer.write(b'{"command":"status"}\n')
        await writer.drain()
        response = json.loads((await reader.readline()).decode())
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()

    assert response["ok"] is True
    assert response["posture"] == "laptop"
    assert response["degraded"] == "input_access_denied"
