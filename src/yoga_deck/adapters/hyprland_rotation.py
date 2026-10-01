"""Strict Hyprland boundary for coordinated internal rotation."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass

from yoga_deck.adapters.async_lifecycle import complete_before_cancelling
from yoga_deck.adapters.hyprland_inputs import CommandRunner, SubprocessRunner
from yoga_deck.core import Orientation

_INTERNAL_OUTPUT = "eDP-1"
_TOUCHSCREEN = "wacom-pen-and-multitouch-sensor-finger"
_PEN = "wacom-pen-and-multitouch-sensor-pen"
_TRANSFORMS = {
    Orientation.NORMAL: 0,
    Orientation.RIGHT: 3,
    Orientation.INVERTED: 2,
    Orientation.LEFT: 1,
}


class HyprlandRotationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RotationTargets:
    output: str
    touchscreen: str
    pen: str


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    mode: str
    position: str
    scale: float
    vrr: int
    color_management: str
    bitdepth: int
    sdr_brightness: float
    sdr_saturation: float
    sdr_min_luminance: float
    sdr_max_luminance: int


class HyprlandRotationAdapter:
    def __init__(self, runner: CommandRunner | None = None, timeout: float = 2.0) -> None:
        self._runner = runner or SubprocessRunner()
        self._timeout = timeout

    def discover_targets(self) -> RotationTargets:
        monitors = self._run(["hyprctl", "-j", "monitors", "all"])
        devices = self._run(["hyprctl", "-j", "devices"])
        return parse_rotation_targets(monitors, devices)

    def apply(self, orientation: Orientation) -> None:
        monitors = self._run(["hyprctl", "-j", "monitors", "all"])
        devices = self._run(["hyprctl", "-j", "devices"])
        targets = parse_rotation_targets(monitors, devices)
        monitor = parse_monitor_snapshot(monitors)
        transform = _TRANSFORMS[orientation]
        monitor_lua = ", ".join(
            (
                f'output = "{targets.output}"',
                f'mode = "{monitor.mode}"',
                f'position = "{monitor.position}"',
                f"scale = {monitor.scale}",
                f"transform = {transform}",
                f"vrr = {monitor.vrr}",
                f'cm = "{monitor.color_management}"',
                f"bitdepth = {monitor.bitdepth}",
                f"sdrbrightness = {monitor.sdr_brightness}",
                f"sdrsaturation = {monitor.sdr_saturation}",
                f"sdr_min_luminance = {monitor.sdr_min_luminance}",
                f"sdr_max_luminance = {monitor.sdr_max_luminance}",
            )
        )
        lua = "; ".join(
            (
                f"hl.monitor({{ {monitor_lua} }})",
                f'hl.device({{ name = "{targets.touchscreen}", transform = {transform}, '
                f'output = "{targets.output}" }})',
                f'hl.device({{ name = "{targets.pen}", transform = {transform}, '
                f'output = "{targets.output}" }})',
            )
        )
        self._run(["hyprctl", "eval", lua], failure="rotation_transaction_failed")

    def _run(self, args: list[str], *, failure: str = "hyprland_unavailable") -> str:
        try:
            result = self._runner.run(args, self._timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HyprlandRotationError(failure) from error
        if result.returncode != 0:
            raise HyprlandRotationError(failure)
        return result.stdout


class AsyncHyprlandRotationAdapter:
    def __init__(self, adapter: HyprlandRotationAdapter | None = None) -> None:
        self._adapter = adapter or HyprlandRotationAdapter()

    async def apply(self, orientation: Orientation) -> None:
        await complete_before_cancelling(asyncio.to_thread(self._adapter.apply, orientation))


def parse_rotation_targets(monitors_payload: str, devices_payload: str) -> RotationTargets:
    try:
        monitors = json.loads(monitors_payload)
        devices = json.loads(devices_payload)
        if not isinstance(monitors, list) or not isinstance(devices, dict):
            raise ValueError
        monitor_names = _names(monitors)
        touch_names = _names(devices["touch"])
        tablet_names = _names(devices["tablets"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise HyprlandRotationError("malformed_hyprland_rotation_state") from error

    if not (
        _INTERNAL_OUTPUT in monitor_names and _TOUCHSCREEN in touch_names and _PEN in tablet_names
    ):
        raise HyprlandRotationError("rotation_targets_incomplete")
    return RotationTargets(_INTERNAL_OUTPUT, _TOUCHSCREEN, _PEN)


def parse_monitor_snapshot(monitors_payload: str) -> MonitorSnapshot:
    try:
        monitors = json.loads(monitors_payload)
        monitor = next(value for value in monitors if value.get("name") == _INTERNAL_OUTPUT)
        width = _number(monitor, "width", int)
        height = _number(monitor, "height", int)
        refresh = _number(monitor, "refreshRate", (int, float))
        x = _number(monitor, "x", int)
        y = _number(monitor, "y", int)
        scale = _number(monitor, "scale", (int, float))
        vrr = int(bool(monitor["vrr"]))
        color_management = monitor["colorManagementPreset"]
        current_format = monitor.get("currentFormat", "XRGB8888")
        sdr_brightness = _number(monitor, "sdrBrightness", (int, float), 1.0)
        sdr_saturation = _number(monitor, "sdrSaturation", (int, float), 1.0)
        sdr_min = _number(monitor, "sdrMinLuminance", (int, float), 0.2)
        sdr_max = _number(monitor, "sdrMaxLuminance", (int, float), 80.0)
        if not isinstance(color_management, str) or not isinstance(current_format, str):
            raise ValueError
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError, ValueError) as error:
        raise HyprlandRotationError("malformed_hyprland_rotation_state") from error

    bitdepth = 10 if "101010" in current_format else 8
    return MonitorSnapshot(
        mode=f"{width}x{height}@{refresh:.3f}",
        position=f"{x}x{y}",
        scale=float(scale),
        vrr=vrr,
        color_management=color_management,
        bitdepth=bitdepth,
        sdr_brightness=float(sdr_brightness),
        sdr_saturation=float(sdr_saturation),
        sdr_min_luminance=float(sdr_min),
        sdr_max_luminance=int(sdr_max),
    )


def _number(
    value: dict[str, object],
    key: str,
    expected: type | tuple[type, ...],
    default: int | float | None = None,
) -> int | float:
    candidate = value.get(key, default)
    if isinstance(candidate, bool) or not isinstance(candidate, expected):
        raise ValueError
    return candidate


def _names(values: object) -> set[str]:
    if not isinstance(values, list):
        raise ValueError
    names = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError
        name = value.get("name")
        if name is None:
            continue
        if not isinstance(name, str):
            raise ValueError
        names.add(name)
    return names
