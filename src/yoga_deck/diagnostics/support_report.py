"""Pure composition and rendering for privacy-safe support reports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .report import InspectionReport, remediation_codes, remediation_lines

_POSTURES = frozenset({"unknown", "laptop", "tablet"})
_ORIENTATIONS = frozenset({"normal", "right", "inverted", "left"})
_RUNTIME_ERRORS = frozenset({"command_failed", "runtime_status_invalid", "runtime_unavailable"})
_LIMITATIONS = (
    "external_monitor_hotplug_unvalidated",
    "hard_compositor_reconnect_unvalidated",
    "pen_silo_event_unverified",
)


@dataclass(frozen=True, slots=True)
class DiscoverySupport:
    available: bool
    inspection: InspectionReport | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.available != (self.inspection is not None) or (
            self.available and self.error is not None
        ):
            raise ValueError("invalid discovery support state")
        if not self.available and self.error != "discovery_unavailable":
            raise ValueError("unsupported discovery error")

    def as_dict(self) -> dict[str, object]:
        inspection = self.inspection or InspectionReport()
        return {
            "available": self.available,
            "components": [component.as_dict() for component in inspection.components],
            "monitors": [monitor.as_dict() for monitor in inspection.monitors],
            "diagnostics": list(inspection.diagnostics),
            "remediation": remediation_codes(inspection.diagnostics),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSupport:
    available: bool
    posture: str | None = None
    inputs_enabled: bool | None = None
    orientation: str | None = None
    orientation_lock: bool | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.available:
            valid = (
                self.posture in _POSTURES
                and type(self.inputs_enabled) is bool
                and self.orientation in _ORIENTATIONS
                and type(self.orientation_lock) is bool
                and self.error is None
            )
        else:
            valid = (
                self.posture is None
                and self.inputs_enabled is None
                and self.orientation is None
                and self.orientation_lock is None
                and self.error in _RUNTIME_ERRORS
            )
        if not valid:
            raise ValueError("invalid runtime support state")

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "posture": self.posture,
            "inputs_enabled": self.inputs_enabled,
            "orientation": self.orientation,
            "orientation_lock": self.orientation_lock,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class SupportReport:
    discovery: DiscoverySupport
    runtime: RuntimeSupport
    #: Version 2 added the ``permission_denied`` component status and discovery ``remediation``.
    schema_version: int = 2
    redacted: bool = True
    limitations: tuple[str, ...] = _LIMITATIONS

    @property
    def status(self) -> str:
        inspection = self.discovery.inspection
        discovery_ready = (
            inspection is not None
            and not inspection.diagnostics
            and all(component.status.value == "available" for component in inspection.components)
        )
        return "ready" if discovery_ready and self.runtime.available else "degraded"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "redacted": self.redacted,
            "status": self.status,
            "discovery": self.discovery.as_dict(),
            "runtime": self.runtime.as_dict(),
            "limitations": list(self.limitations),
        }


def build_support_report(
    inspection: InspectionReport | None,
    runtime_payload: Mapping[str, Any] | None,
) -> SupportReport:
    """Compose already-normalized discovery with a strictly parsed runtime status."""

    discovery = (
        DiscoverySupport(True, inspection=inspection)
        if inspection is not None
        else DiscoverySupport(False, error="discovery_unavailable")
    )
    return SupportReport(discovery=discovery, runtime=_parse_runtime(runtime_payload))


def _parse_runtime(payload: Mapping[str, Any] | None) -> RuntimeSupport:
    if payload is None:
        return RuntimeSupport(False, error="runtime_unavailable")
    if not isinstance(payload, Mapping):
        return RuntimeSupport(False, error="runtime_status_invalid")
    if payload.get("schema_version") != 1 or type(payload.get("available")) is not bool:
        return RuntimeSupport(False, error="runtime_status_invalid")
    if payload["available"] is False:
        error = payload.get("error")
        if error not in _RUNTIME_ERRORS:
            error = "runtime_status_invalid"
        return RuntimeSupport(False, error=error)
    try:
        return RuntimeSupport(
            True,
            posture=payload.get("posture"),
            inputs_enabled=payload.get("inputs_enabled"),
            orientation=payload.get("orientation"),
            orientation_lock=payload.get("orientation_lock"),
            error=payload.get("error"),
        )
    except ValueError:
        return RuntimeSupport(False, error="runtime_status_invalid")


def render_support_json(report: SupportReport) -> str:
    return json.dumps(report.as_dict(), indent=2, sort_keys=True)


def render_support_text(report: SupportReport) -> str:
    lines = ["Yoga Deck redacted support report", f"status: {report.status}"]
    discovery = report.discovery
    if discovery.available:
        lines.append("discovery: available")
        assert discovery.inspection is not None
        for component in discovery.inspection.components:
            capabilities = ""
            if component.capabilities:
                capabilities = f" [{', '.join(component.capabilities)}]"
            lines.append(f"component {component.role}: {component.status.value}{capabilities}")
        for monitor in discovery.inspection.monitors:
            location = "internal" if monitor.internal else "external"
            lines.append(
                f"monitor {monitor.connector}: {monitor.width}x{monitor.height} "
                f"scale {monitor.scale:g} transform {monitor.transform} ({location})"
            )
        for diagnostic in discovery.inspection.diagnostics:
            lines.append(f"discovery diagnostic: {diagnostic}")
        lines.extend(remediation_lines(discovery.inspection.diagnostics))
    else:
        lines.append(f"discovery: unavailable ({discovery.error})")

    runtime = report.runtime
    if runtime.available:
        lines.extend(
            (
                "runtime: available",
                f"posture: {runtime.posture}",
                f"internal inputs enabled: {str(runtime.inputs_enabled).lower()}",
                f"orientation: {runtime.orientation}",
                f"orientation lock: {str(runtime.orientation_lock).lower()}",
            )
        )
    else:
        lines.append(f"runtime: unavailable ({runtime.error})")
    for limitation in report.limitations:
        lines.append(f"limitation: {limitation}")
    return "\n".join(lines)
