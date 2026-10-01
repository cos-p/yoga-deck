#!/usr/bin/env python3
"""Thin local transport client for the Yoga Deck Omarchy Shell plugin."""

from __future__ import annotations

import argparse
import json
import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

Request = Callable[[dict[str, Any]], dict[str, Any]]

#: A settings snapshot is the one response that grows with the schema, so the read loop is
#: bounded well above the size of any command this client sends.
MAX_RESPONSE_BYTES = 65536


def socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        raise RuntimeError("xdg_runtime_dir_unavailable")
    return Path(runtime_dir) / "yoga-deck" / "shell.sock"


def request_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(socket_path()))
            client.sendall((json.dumps(payload) + "\n").encode())
            response = bytearray()
            while not response.endswith(b"\n") and len(response) <= MAX_RESPONSE_BYTES:
                chunk = client.recv(1024)
                if not chunk:
                    break
                response.extend(chunk)
        parsed = json.loads(response)
        if not isinstance(parsed, dict):
            raise ValueError
        return parsed
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return {
            "schema_version": 1,
            "available": False,
            "error": "runtime_unavailable",
        }


def collect_status(*, requester: Request = request_runtime) -> dict[str, Any]:
    return requester({"command": "status"})


def send_command(
    command: str,
    value: bool | str | None = None,
    *,
    setting: str | None = None,
    requester: Request = request_runtime,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"command": command}
    if setting is not None:
        payload["setting"] = setting
    if value is not None:
        payload["value"] = value
    return requester(payload)


def collect_config(*, requester: Request = request_runtime) -> dict[str, Any]:
    """Every setting with its value, its source, and enough of its schema to render it.

    Fetched on demand rather than folded into the status poll: the panel needs it when it
    opens and after a change, and the bar polls status the whole time it is on screen.
    """

    return requester({"command": "get_config"})


def set_config(
    name: str,
    value: str | None,
    *,
    requester: Request = request_runtime,
) -> dict[str, Any]:
    """Store one setting, or hand it back to the hand-written file when `value` is `None`.

    Values travel as the text the control produced. The runtime parses them with the same
    parser `yoga-doctor config set` uses, so a panel can never store what the prompt would
    refuse or what the settings file would then discard.
    """

    return send_command("set_config", value, setting=name, requester=requester)


def main() -> int:
    parser = argparse.ArgumentParser(description="Yoga Deck shell transport client")
    parser.add_argument("--set-lock", choices=["true", "false"])
    parser.add_argument("--rotate", choices=["normal", "right", "inverted", "left"])
    parser.add_argument("--recover-inputs", action="store_true")
    parser.add_argument("--get-config", action="store_true")
    parser.add_argument("--set-config", nargs=2, metavar=("NAME", "VALUE"))
    parser.add_argument("--unset-config", metavar="NAME")
    args = parser.parse_args()

    if args.set_lock is not None:
        response = send_command("set_lock", args.set_lock == "true")
    elif args.rotate is not None:
        response = send_command("rotate", args.rotate)
    elif args.recover_inputs:
        response = send_command("recover_inputs")
    elif args.get_config:
        response = collect_config()
    elif args.set_config is not None:
        response = set_config(args.set_config[0], args.set_config[1])
    elif args.unset_config is not None:
        response = set_config(args.unset_config, None)
    else:
        response = collect_status()

    print(json.dumps(response, sort_keys=True))
    return 0 if response.get("available", response.get("ok", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
