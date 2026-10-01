"""Local, redacted status and control transport for the Omarchy Shell plugin."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yoga_deck.core import State
from yoga_deck.core.settings import SPECS_BY_NAME

#: A command is a handful of scalars; anything larger is not one.
_MAX_REQUEST_BYTES = 4096

#: A settings snapshot is the one response that grows with the schema, so the client's read
#: loop is bounded well above it rather than at the request size.
_MAX_RESPONSE_BYTES = 65536


def default_socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        # The transport contract is private-per-user. A shared /tmp fallback would let one
        # user pre-create or deny another user's predictable socket path.
        raise RuntimeError("xdg_runtime_dir_unavailable")
    return Path(runtime_dir) / "yoga-deck" / "shell.sock"


@dataclass(frozen=True, slots=True)
class ShellCommand:
    name: str
    value: bool | int | str | None = None
    #: The dotted setting name a `set_config` acts on. Unused by every other command.
    setting: str | None = None


#: Stable degraded reasons the panel may show; anything else is dropped rather than relayed.
DEGRADED_REASONS = frozenset({"input_access_denied"})


def status_payload(state: State, degraded: str | None = None) -> dict[str, Any]:
    posture = "unknown"
    if state.tablet_mode_observed is True:
        posture = "tablet"
    elif state.tablet_mode_observed is False:
        posture = "laptop"
    return {
        "schema_version": 1,
        "available": True,
        "sequence": state.sequence,
        "posture": posture,
        "inputs_enabled": state.internal_inputs_enabled,
        "orientation": state.applied_orientation.value,
        "orientation_lock": state.orientation_locked,
        # Additive and optional, so the status schema stays at 1 for older panels.
        "degraded": degraded if degraded in DEGRADED_REASONS else None,
        "error": None,
    }


def _parse_set_config(payload: dict[str, Any]) -> ShellCommand:
    """One setting change from a control, refused at the door if it is not storable.

    A null value clears the override rather than storing an empty one, which is how a panel
    hands a setting back to the hand-written file underneath it.
    """

    setting = payload.get("setting")
    spec = SPECS_BY_NAME.get(setting) if isinstance(setting, str) else None
    if spec is None:
        raise ValueError("unknown_setting")

    value = payload.get("value")
    if value is None:
        return ShellCommand("set_config", None, setting)

    checked = spec.validate(value)
    if checked is None and isinstance(value, str):
        # A client that can only send text -- the panel shells out through argv -- gets the
        # same parser the shell prompt uses, so no transport can store what `config set`
        # would refuse.
        checked = spec.parse_text(value)
    if checked is None:
        raise ValueError("invalid_value")
    # Carried onward as the scalar the settings file holds, so what a client sends back is
    # exactly what a snapshot handed it.
    return ShellCommand("set_config", spec.to_toml(checked), setting)


def _parse_command(payload: Any) -> ShellCommand:
    if not isinstance(payload, dict):
        raise ValueError("invalid_request")
    name = payload.get("command")
    value = payload.get("value")
    if name == "status" and value is None:
        return ShellCommand(name)
    if name == "get_config" and value is None:
        return ShellCommand(name)
    if name == "set_config":
        return _parse_set_config(payload)
    if name == "set_lock" and isinstance(value, bool):
        return ShellCommand(name, value)
    if name == "rotate" and value in {"normal", "right", "inverted", "left"}:
        return ShellCommand(name, value)
    if name == "recover_inputs" and value is None:
        return ShellCommand(name)
    if name == "theme_changed" and value is None:
        return ShellCommand(name)
    raise ValueError("invalid_command")


class ShellTransportServer:
    def __init__(
        self,
        *,
        state_provider: Callable[[], State],
        command_handler: Callable[[ShellCommand], Awaitable[None]],
        config_provider: Callable[[], dict[str, Any]] | None = None,
        degraded_provider: Callable[[], str | None] | None = None,
        socket_path: Path | None = None,
    ) -> None:
        self._degraded_provider = degraded_provider
        self.socket_path = socket_path or default_socket_path()
        self._state_provider = state_provider
        self._command_handler = command_handler
        # Settings are read on request rather than folded into every status poll, because a
        # panel needs them when it opens and the bar polls status twice a second all the time.
        self._config_provider = config_provider
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path)
        self.socket_path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.socket_path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        response: dict[str, Any]
        try:
            raw = await reader.readline()
            if not raw or len(raw) > _MAX_REQUEST_BYTES:
                raise ValueError("invalid_request")
            command = _parse_command(json.loads(raw))
            if command.name == "get_config":
                if self._config_provider is None:
                    raise ValueError("settings_unavailable")
                response = {"ok": True, "config": self._config_provider()}
            else:
                if command.name != "status":
                    await self._command_handler(command)
                degraded = self._degraded_provider() if self._degraded_provider else None
                response = {"ok": True, **status_payload(self._state_provider(), degraded)}
        except (ValueError, json.JSONDecodeError) as error:
            response = {"ok": False, "error": str(error)}
        except Exception:
            response = {"ok": False, "error": "command_failed"}
        writer.write((json.dumps(response, sort_keys=True) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()


def request_sync(payload: dict[str, Any], *, socket_path: Path | None = None) -> dict[str, Any]:
    path = socket_path or default_socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(path))
            client.sendall((json.dumps(payload) + "\n").encode())
            chunks = bytearray()
            while not chunks.endswith(b"\n") and len(chunks) <= _MAX_RESPONSE_BYTES:
                chunk = client.recv(1024)
                if not chunk:
                    break
                chunks.extend(chunk)
        response = json.loads(chunks)
        if not isinstance(response, dict):
            raise ValueError
        return response
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return {
            "schema_version": 1,
            "available": False,
            "error": "runtime_unavailable",
        }
