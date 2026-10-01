"""Hyprland adapter for a strict allowlist of internal input devices."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from yoga_deck.adapters.async_lifecycle import complete_before_cancelling
from yoga_deck.adapters.hyprland_instance import SIGNATURE_VARIABLE, resolve_instance_signature

_PROC_ROOT = Path("/proc")


class HyprlandError(RuntimeError):
    """Stable adapter failure without compositor output or device details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class InputRole(Enum):
    KEYBOARD = "internal_keyboard"
    TOUCHPAD = "touchpad"
    TRACKPOINT = "trackpoint"


@dataclass(frozen=True, slots=True)
class InternalInput:
    role: InputRole
    name: str


_OWNED_NAMES = {
    InputRole.KEYBOARD: "at-translated-set-2-keyboard",
    InputRole.TOUCHPAD: "synps/2-synaptics-touchpad",
    InputRole.TRACKPOINT: "tpps/2-alps-trackpoint",
}


class CompletedProcess(Protocol):
    stdout: str
    stderr: str
    returncode: int


class CommandRunner(Protocol):
    def run(self, args: list[str], timeout: float) -> CompletedProcess: ...


class SubprocessRunner:
    def run(self, args: list[str], timeout: float) -> CompletedProcess:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_hyprctl_environment() if args[:1] == ["hyprctl"] else None,
        )


def _hyprctl_environment() -> dict[str, str] | None:
    """Follow a restarted compositor; inherit the environment unchanged otherwise."""

    signature = resolve_instance_signature(proc_root=_PROC_ROOT)
    if signature is None or signature == os.environ.get(SIGNATURE_VARIABLE):
        return None
    environment = dict(os.environ)
    environment[SIGNATURE_VARIABLE] = signature
    return environment


class HyprlandInputAdapter:
    def __init__(self, runner: CommandRunner | None = None, timeout: float = 2.0) -> None:
        self._runner = runner or SubprocessRunner()
        self._timeout = timeout

    def discover_owned(self) -> tuple[InternalInput, ...]:
        try:
            result = self._runner.run(["hyprctl", "-j", "devices"], self._timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HyprlandError("hyprland_unavailable") from error
        if result.returncode != 0:
            raise HyprlandError("hyprland_unavailable")
        return parse_owned_inputs(result.stdout)

    def set_enabled(self, device: InternalInput, enabled: bool) -> None:
        if not _is_owned(device):
            raise HyprlandError("unsafe_input_target")
        lua = f'hl.device({{ name = "{device.name}", enabled = {"true" if enabled else "false"} }})'
        try:
            result = self._runner.run(["hyprctl", "eval", lua], self._timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HyprlandError("hyprland_mutation_failed") from error
        if result.returncode != 0:
            raise HyprlandError("hyprland_mutation_failed")


class HyprlandInputRecovery:
    """Synchronous emergency recovery used directly by yoga-doctor."""

    def __init__(self, adapter: HyprlandInputAdapter | None = None) -> None:
        self._adapter = adapter or HyprlandInputAdapter()

    def recover_inputs(self) -> None:
        failures = 0
        devices = self._adapter.discover_owned()
        for device in devices:
            try:
                self._adapter.set_enabled(device, True)
            except HyprlandError:
                failures += 1
        if failures:
            raise HyprlandError("input_recovery_incomplete")


class AsyncHyprlandInputAdapter:
    """Run the subprocess adapter without blocking the coordinator event loop."""

    def __init__(self, adapter: HyprlandInputAdapter | None = None) -> None:
        self._adapter = adapter or HyprlandInputAdapter()

    async def discover_owned(self) -> tuple[InternalInput, ...]:
        return await asyncio.to_thread(self._adapter.discover_owned)

    async def set_enabled(self, device: InternalInput, enabled: bool) -> None:
        await complete_before_cancelling(
            asyncio.to_thread(self._adapter.set_enabled, device, enabled)
        )


def parse_owned_inputs(payload: str) -> tuple[InternalInput, ...]:
    try:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise ValueError
        keyboards = raw["keyboards"]
        mice = raw["mice"]
        if not isinstance(keyboards, list) or not isinstance(mice, list):
            raise ValueError
        keyboard_names = _names(keyboards)
        mouse_names = _names(mice)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise HyprlandError("malformed_hyprland_devices") from error

    found = []
    for role, name in _OWNED_NAMES.items():
        available = keyboard_names if role is InputRole.KEYBOARD else mouse_names
        if name in available:
            found.append(InternalInput(role, name))
    return tuple(found)


def _names(devices: list[Any]) -> set[str]:
    names = set()
    for device in devices:
        if not isinstance(device, dict) or not isinstance(device.get("name"), str):
            raise ValueError
        names.add(device["name"])
    return names


def _is_owned(device: InternalInput) -> bool:
    return _OWNED_NAMES.get(device.role) == device.name


def is_owned_input(device: InternalInput) -> bool:
    """Return whether a value is one of this hardware adapter's exact owned targets."""

    return _is_owned(device)
