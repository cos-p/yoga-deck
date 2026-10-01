"""Normalized, privacy-safe inspection report values and renderers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class ComponentStatus(Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    ERROR = "error"
    PERMISSION_DENIED = "permission_denied"


#: Stable remediation codes, keyed by the diagnostic that calls for them, with the one line of
#: advice a text report prints. Neither names a user, a group member list, or a device node.
REMEDIATIONS: dict[str, tuple[str, str]] = {
    "input_permission_denied": (
        "input_group_required",
        "add your user to the input group, then log out and back in (see docs/install.md)",
    ),
}


def remediation_codes(diagnostics: tuple[str, ...]) -> list[str]:
    return [REMEDIATIONS[code][0] for code in diagnostics if code in REMEDIATIONS]


def remediation_lines(diagnostics: tuple[str, ...]) -> list[str]:
    return [
        f"remediation {REMEDIATIONS[code][0]}: {REMEDIATIONS[code][1]}"
        for code in diagnostics
        if code in REMEDIATIONS
    ]


@dataclass(frozen=True, slots=True)
class Component:
    """A semantic hardware role with no raw device identity."""

    safe_roles: ClassVar[frozenset[str]] = frozenset(
        {
            "tablet_switch",
            "internal_keyboard",
            "touchpad",
            "trackpoint",
            "touchscreen",
            "pen",
            "accelerometer",
            "gyroscope",
            "hinge_sensor",
            "orientation_sensor",
        }
    )
    safe_capabilities: ClassVar[frozenset[str]] = frozenset(
        {"absolute", "keys", "relative", "switch"}
    )

    role: str
    status: ComponentStatus
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in self.safe_roles:
            raise ValueError("unsupported component role")
        if not set(self.capabilities) <= self.safe_capabilities:
            raise ValueError("unsupported component capability")

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "status": self.status.value,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True, slots=True)
class Monitor:
    """Sanitized monitor geometry, excluding description and unique EDID data."""

    connector: str
    internal: bool
    width: int
    height: int
    scale: float
    transform: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z]+(?:-[A-Za-z]+)*-\d+", self.connector):
            raise ValueError("unsafe monitor connector")
        if self.width <= 0 or self.height <= 0 or self.scale <= 0:
            raise ValueError("invalid monitor geometry")
        if self.transform not in range(8):
            raise ValueError("invalid monitor transform")

    def as_dict(self) -> dict[str, object]:
        return {
            "connector": self.connector,
            "internal": self.internal,
            "width": self.width,
            "height": self.height,
            "scale": self.scale,
            "transform": self.transform,
        }


@dataclass(frozen=True, slots=True)
class InspectionReport:
    safe_diagnostics: ClassVar[frozenset[str]] = frozenset(
        {
            "hyprland_unavailable",
            "input_discovery_failed",
            "input_permission_denied",
            "inspection_failed",
            "malformed_hyprland_response",
            "sensor_discovery_failed",
        }
    )

    #: Version 2 added the ``permission_denied`` component status and ``remediation``.
    schema_version: int = 2
    components: tuple[Component, ...] = ()
    monitors: tuple[Monitor, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not set(self.diagnostics) <= self.safe_diagnostics:
            raise ValueError("unsupported diagnostic code")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "components": [component.as_dict() for component in self.components],
            "monitors": [monitor.as_dict() for monitor in self.monitors],
            "diagnostics": list(self.diagnostics),
            "remediation": remediation_codes(self.diagnostics),
        }


def render_json(report: InspectionReport) -> str:
    return json.dumps(report.as_dict(), indent=2, sort_keys=True)


def render_text(report: InspectionReport) -> str:
    lines = ["Yoga Deck inspection"]
    for component in report.components:
        capabilities = ""
        if component.capabilities:
            capabilities = f" [{', '.join(component.capabilities)}]"
        lines.append(f"{component.role}: {component.status.value}{capabilities}")
    for monitor in report.monitors:
        location = "internal" if monitor.internal else "external"
        lines.append(
            f"{monitor.connector}: {monitor.width}x{monitor.height} "
            f"scale {monitor.scale:g} transform {monitor.transform} ({location})"
        )
    for diagnostic in report.diagnostics:
        lines.append(f"diagnostic: {diagnostic}")
    lines.extend(remediation_lines(report.diagnostics))
    return "\n".join(lines)
