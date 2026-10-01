"""Read-only Linux and Hyprland diagnostic discovery adapter."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, overload

from yoga_deck.diagnostics.report import (
    Component,
    ComponentStatus,
    InspectionReport,
    Monitor,
)

_INPUT_ROLES = (
    "tablet_switch",
    "internal_keyboard",
    "touchpad",
    "trackpoint",
    "touchscreen",
    "pen",
)
_SENSOR_ROLES = ("accelerometer", "gyroscope", "hinge_sensor")
#: Roles whose event nodes the runtime opens itself. Hyprland reads the others through logind,
#: so an unreadable node there is expected and harmless.
RUNTIME_INPUT_ROLES = frozenset({"tablet_switch", "pen"})

AccessCheck = Callable[[Path], bool]


def _readable(node: Path) -> bool:
    return os.access(node, os.R_OK)


def classify_input(name: str, capabilities: set[str]) -> str | None:
    """Map a non-unique kernel name and capabilities to an owned device role."""

    normalized = name.casefold()
    if "thinkpad extra buttons" in normalized and "sw" in capabilities:
        return "tablet_switch"
    if "wacom" in normalized and "finger" in normalized and "absolute" in capabilities:
        return "touchscreen"
    if "wacom" in normalized and normalized.endswith("pen") and "absolute" in capabilities:
        return "pen"
    if "touchpad" in normalized and "absolute" in capabilities:
        return "touchpad"
    if "trackpoint" in normalized and "relative" in capabilities:
        return "trackpoint"
    if "at translated" in normalized and "keyboard" in normalized and "keys" in capabilities:
        return "internal_keyboard"
    return None


@overload
def parse_hyprland_monitors(payload: str) -> tuple[Monitor, ...]: ...


@overload
def parse_hyprland_monitors(
    payload: str, *, with_diagnostic: bool
) -> tuple[tuple[Monitor, ...], str | None]: ...


def parse_hyprland_monitors(
    payload: str, *, with_diagnostic: bool = False
) -> tuple[Monitor, ...] | tuple[tuple[Monitor, ...], str | None]:
    diagnostic: str | None = None
    monitors: tuple[Monitor, ...] = ()
    try:
        raw = json.loads(payload)
        if not isinstance(raw, list):
            raise ValueError
        monitors = tuple(_parse_monitor(item) for item in raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        diagnostic = "malformed_hyprland_response"
    if with_diagnostic:
        return monitors, diagnostic
    return monitors


def _parse_monitor(item: Any) -> Monitor:
    if not isinstance(item, dict):
        raise ValueError
    connector = item["name"]
    return Monitor(
        connector=connector,
        internal=str(connector).startswith(("eDP-", "LVDS-")),
        width=int(item["width"]),
        height=int(item["height"]),
        scale=float(item["scale"]),
        transform=int(item["transform"]),
    )


class LinuxDiscovery:
    """Collect a normalized report without changing devices or compositor state."""

    def __init__(
        self,
        sys_root: Path = Path("/sys"),
        *,
        dev_root: Path = Path("/dev"),
        readable: AccessCheck = _readable,
    ) -> None:
        self._sys_root = sys_root
        self._dev_root = dev_root
        self._readable = readable

    def inspect(self) -> InspectionReport:
        diagnostics: list[str] = []
        components = [*self._input_components(diagnostics), *self._sensor_components(diagnostics)]
        monitors = self._monitors(diagnostics)
        return InspectionReport(
            components=tuple(components),
            monitors=monitors,
            diagnostics=tuple(diagnostics),
        )

    def runtime_input_denied(self) -> frozenset[str]:
        """Runtime-read roles present in sysfs whose event nodes this user cannot read."""

        try:
            scanned = _scan_inputs(self._sys_root, self._dev_root, self._readable)
        except OSError:
            return frozenset()
        return frozenset(role for role, (_, readable) in scanned.items() if not readable)

    def _input_components(self, diagnostics: list[str]) -> Iterable[Component]:
        try:
            found = _scan_inputs(self._sys_root, self._dev_root, self._readable)
        except OSError:
            diagnostics.append("input_discovery_failed")
            return [Component(role, ComponentStatus.ERROR) for role in _INPUT_ROLES]

        components = []
        for role in _INPUT_ROLES:
            if role not in found:
                status = ComponentStatus.MISSING
            elif found[role][1]:
                status = ComponentStatus.AVAILABLE
            else:
                status = ComponentStatus.PERMISSION_DENIED
            capabilities = found.get(role, (set(), True))[0]
            components.append(
                Component(role, status, tuple(sorted(_public_capabilities(capabilities))))
            )
        if any(component.status is ComponentStatus.PERMISSION_DENIED for component in components):
            diagnostics.append("input_permission_denied")
        return components

    def _sensor_components(self, diagnostics: list[str]) -> Iterable[Component]:
        found: set[str] = set()
        try:
            for device in (self._sys_root / "bus/iio/devices").glob("iio:device*"):
                name = (device / "name").read_text(errors="replace").strip().casefold()
                if "accel" in name:
                    found.add("accelerometer")
                if "gyro" in name:
                    found.add("gyroscope")
                if "hinge" in name:
                    found.add("hinge_sensor")
        except OSError:
            diagnostics.append("sensor_discovery_failed")
            return [Component(role, ComponentStatus.ERROR) for role in _SENSOR_ROLES]
        return [
            Component(
                role,
                ComponentStatus.AVAILABLE if role in found else ComponentStatus.MISSING,
            )
            for role in _SENSOR_ROLES
        ]

    @staticmethod
    def _monitors(diagnostics: list[str]) -> tuple[Monitor, ...]:
        try:
            result = subprocess.run(
                ["hyprctl", "-j", "monitors"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            diagnostics.append("hyprland_unavailable")
            return ()
        if result.returncode != 0:
            diagnostics.append("hyprland_unavailable")
            return ()
        monitors, diagnostic = parse_hyprland_monitors(result.stdout, with_diagnostic=True)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
        return monitors


def _scan_inputs(
    sys_root: Path, dev_root: Path, readable: AccessCheck
) -> dict[str, tuple[set[str], bool]]:
    """Map each owned role to its capabilities and whether the runtime can open it.

    A role counts as readable when any of its event nodes is; only runtime-read roles are
    checked, and the node path never leaves this function.
    """

    found: dict[str, tuple[set[str], bool]] = {}
    for device in tuple((sys_root / "class/input").glob("event*/device")):
        name = (device / "name").read_text(errors="replace").strip()
        capabilities = _read_input_capabilities(device / "capabilities")
        role = classify_input(name, capabilities)
        if role is None:
            continue
        node_readable = role not in RUNTIME_INPUT_ROLES or readable(
            dev_root / "input" / device.parent.name
        )
        previous_capabilities, previous_readable = found.get(role, (set(), False))
        found[role] = (previous_capabilities | capabilities, previous_readable or node_readable)
    return found


def _read_input_capabilities(directory: Path) -> set[str]:
    capabilities: set[str] = set()
    for kernel_name, public_name in (
        ("key", "keys"),
        ("rel", "relative"),
        ("abs", "absolute"),
        ("sw", "sw"),
    ):
        path = directory / kernel_name
        try:
            if any(int(word, 16) for word in path.read_text().split()):
                capabilities.add(public_name)
        except FileNotFoundError:
            continue
    return capabilities


def _public_capabilities(capabilities: set[str]) -> set[str]:
    renamed = {"switch" if capability == "sw" else capability for capability in capabilities}
    return renamed & Component.safe_capabilities
