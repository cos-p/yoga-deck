import asyncio
import json
import math
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any, Protocol

from yoga_deck.adapters.hyprland_rotation import HyprlandRotationAdapter
from yoga_deck.adapters.sensor_proxy import SensorProxySource
from yoga_deck.core import Orientation

_INSET = 40.0
_ORIENTATION_MAP = {
    Orientation.NORMAL: ("normal", 0),
    Orientation.RIGHT: ("right-up", 3),
    Orientation.INVERTED: ("bottom-up", 2),
    Orientation.LEFT: ("left-up", 1),
}
_TRANSFORM_ORIENTATION = {
    transform: orientation for orientation, (_, transform) in _ORIENTATION_MAP.items()
}


class OrientationSource(Protocol):
    async def connect(self) -> Any: ...


class RotationAdapter(Protocol):
    def apply(self, orientation: Orientation) -> Any: ...


@dataclass(frozen=True, slots=True)
class TargetPoint:
    name: str
    target_x: float
    target_y: float


@dataclass(frozen=True, slots=True)
class AlignmentSample:
    point_name: str
    target_x: float
    target_y: float
    actual_x: float
    actual_y: float
    delta_pixels: float
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "point_name": self.point_name,
            "target": [round(self.target_x, 1), round(self.target_y, 1)],
            "actual": [round(self.actual_x, 1), round(self.actual_y, 1)],
            "delta_pixels": round(self.delta_pixels, 2),
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class OrientationAlignmentResult:
    orientation: str
    transform: int
    logical_width: float
    logical_height: float
    touch_samples: tuple[AlignmentSample, ...]
    pen_samples: tuple[AlignmentSample, ...]
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "orientation": self.orientation,
            "transform": self.transform,
            "logical_size": [self.logical_width, self.logical_height],
            "touch_samples": [sample.as_dict() for sample in self.touch_samples],
            "pen_samples": [sample.as_dict() for sample in self.pen_samples],
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class OrientationTransitionRecord:
    from_orientation: str | None
    to_orientation: str
    sensor_value: str
    delivery_latency_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_orientation": self.from_orientation,
            "to_orientation": self.to_orientation,
            "sensor_value": self.sensor_value,
            "delivery_latency_ms": round(self.delivery_latency_ms, 3),
        }


@dataclass(frozen=True, slots=True)
class OrientationCampaignSummary:
    mount_direction_valid: bool
    orientations_tested: tuple[str, ...]
    transitions: tuple[OrientationTransitionRecord, ...]
    alignments: tuple[OrientationAlignmentResult, ...]
    external_monitors_preserved: bool
    starting_transform_restored: bool
    delivery_latencies_ms: dict[str, Any]
    flicker_observed: bool
    summary_passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "mount_direction_valid": self.mount_direction_valid,
            "orientations_tested": list(self.orientations_tested),
            "transitions": [t.as_dict() for t in self.transitions],
            "alignments": [a.as_dict() for a in self.alignments],
            "external_monitors_preserved": self.external_monitors_preserved,
            "starting_transform_restored": self.starting_transform_restored,
            "delivery_latencies_ms": self.delivery_latencies_ms,
            "flicker_observed": self.flicker_observed,
            "summary_passed": self.summary_passed,
        }


def compute_expected_points(
    orientation: Orientation,
    width_px: int = 1920,
    height_px: int = 1080,
    scale: float = 1.25,
    inset: float = _INSET,
) -> tuple[TargetPoint, ...]:
    """Compute logical test points (4 corners + center) for a display mode and orientation."""
    if orientation in (Orientation.NORMAL, Orientation.INVERTED):
        logical_w = width_px / scale
        logical_h = height_px / scale
    else:
        logical_w = height_px / scale
        logical_h = width_px / scale

    return (
        TargetPoint("top_left", inset, inset),
        TargetPoint("top_right", logical_w - inset, inset),
        TargetPoint("bottom_right", logical_w - inset, logical_h - inset),
        TargetPoint("bottom_left", inset, logical_h - inset),
        TargetPoint("center", logical_w / 2.0, logical_h / 2.0),
    )


def evaluate_alignment_sample(
    target: TargetPoint,
    actual_x: float,
    actual_y: float,
    max_tolerance_px: float = 60.0,
) -> AlignmentSample:
    delta = math.hypot(actual_x - target.target_x, actual_y - target.target_y)
    return AlignmentSample(
        point_name=target.name,
        target_x=target.target_x,
        target_y=target.target_y,
        actual_x=actual_x,
        actual_y=actual_y,
        delta_pixels=delta,
        passed=delta <= max_tolerance_px,
    )


def summarize_latencies(latencies: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(latencies)
    if not ordered:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    p95_index = min(int(len(ordered) * 0.95), len(ordered) - 1)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "max": round(ordered[-1], 3),
    }


def query_monitors_safe() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["hyprctl", "-j", "monitors", "all"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        return json.loads(result.stdout)
    except Exception:
        return []


def query_cursorpos_safe() -> tuple[float, float]:
    try:
        result = subprocess.run(
            ["hyprctl", "cursorpos"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        parts = [p.strip() for p in result.stdout.strip().split(",")]
        return float(parts[0]), float(parts[1])
    except Exception:
        return 0.0, 0.0


def transform_normalized_position(
    normalized_x: float,
    normalized_y: float,
    orientation: Orientation,
    normal_logical_width: float,
    normal_logical_height: float,
) -> tuple[float, float]:
    """Transform a natural-device coordinate into monitor-local logical coordinates."""
    if orientation is Orientation.NORMAL:
        return normalized_x * normal_logical_width, normalized_y * normal_logical_height
    if orientation is Orientation.RIGHT:
        return (
            (1.0 - normalized_y) * normal_logical_height,
            normalized_x * normal_logical_width,
        )
    if orientation is Orientation.INVERTED:
        return (
            (1.0 - normalized_x) * normal_logical_width,
            (1.0 - normalized_y) * normal_logical_height,
        )
    return (
        normalized_y * normal_logical_height,
        (1.0 - normalized_x) * normal_logical_width,
    )


def query_device_position(
    device_role: str,
    orientation: Orientation,
    *,
    width_px: int,
    height_px: int,
    scale: float,
) -> tuple[float, float]:
    """Read the current Wacom axis state without relying on volatile event numbers."""
    from evdev import InputDevice, ecodes, list_devices

    expected_name = {
        "touch": "Wacom Pen and multitouch sensor Finger",
        "pen": "Wacom Pen and multitouch sensor Pen",
    }.get(device_role)
    if expected_name is None:
        raise ValueError("unknown_coordinate_device")

    device = None
    for path in list_devices():
        candidate = InputDevice(path)
        if candidate.name == expected_name:
            device = candidate
            break
        candidate.close()
    if device is None:
        raise RuntimeError("coordinate_device_unavailable")
    try:
        x_info = device.absinfo(ecodes.ABS_X)
        y_info = device.absinfo(ecodes.ABS_Y)
    finally:
        device.close()
    if x_info.max <= x_info.min or y_info.max <= y_info.min:
        raise RuntimeError("coordinate_axes_invalid")
    normalized_x = (x_info.value - x_info.min) / (x_info.max - x_info.min)
    normalized_y = (y_info.value - y_info.min) / (y_info.max - y_info.min)
    return transform_normalized_position(
        normalized_x,
        normalized_y,
        orientation,
        width_px / scale,
        height_px / scale,
    )


def capture_device_position(
    device_role: str,
    orientation: Orientation,
    *,
    width_px: int,
    height_px: int,
    scale: float,
    wait_callback: Callable[[], None],
) -> tuple[float, float]:
    """Capture contact events queued while a guided prompt is active."""
    from evdev import InputDevice, ecodes, list_devices

    expected_name = {
        "touch": "Wacom Pen and multitouch sensor Finger",
        "pen": "Wacom Pen and multitouch sensor Pen",
    }.get(device_role)
    if expected_name is None:
        raise ValueError("unknown_coordinate_device")

    device = None
    for path in list_devices():
        candidate = InputDevice(path)
        if candidate.name == expected_name:
            device = candidate
            break
        candidate.close()
    if device is None:
        raise RuntimeError("coordinate_device_unavailable")

    x_code = ecodes.ABS_MT_POSITION_X if device_role == "touch" else ecodes.ABS_X
    y_code = ecodes.ABS_MT_POSITION_Y if device_role == "touch" else ecodes.ABS_Y
    try:
        x_info = device.absinfo(x_code)
        y_info = device.absinfo(y_code)
        wait_callback()
        x_value = None
        y_value = None
        try:
            for event in device.read():
                if event.type != ecodes.EV_ABS:
                    continue
                if event.code == x_code:
                    x_value = event.value
                elif event.code == y_code:
                    y_value = event.value
        except BlockingIOError:
            pass
    finally:
        device.close()
    if x_value is None or y_value is None:
        raise RuntimeError("coordinate_sample_missing")
    normalized_x = (x_value - x_info.min) / (x_info.max - x_info.min)
    normalized_y = (y_value - y_info.min) / (y_info.max - y_info.min)
    return transform_normalized_position(
        normalized_x,
        normalized_y,
        orientation,
        width_px / scale,
        height_px / scale,
    )


def format_campaign_markdown_report(summary: OrientationCampaignSummary) -> str:
    """Render a sanitized markdown report matching the Yoga Deck results format."""
    mount_status = "Pass" if summary.mount_direction_valid else "Fail"
    align_status = "Pass" if summary.summary_passed else "Fail"
    mon_status = "Pass" if summary.external_monitors_preserved else "Fail"
    start_status = "Pass" if summary.starting_transform_restored else "Fail"
    flicker_status = "Observed" if summary.flicker_observed else "None (smooth transition)"

    lines = [
        "# Orientation and Four-Corner Alignment Validation",
        "",
        "Target: ThinkPad X390 Yoga running Omarchy and Hyprland at the observed scale.",
        "",
        "## Summary",
        "",
        f"- **Mount Direction Valid**: {mount_status}",
        f"- **Four-Corner & Center Alignment**: {align_status}",
        f"- **External Monitors Preserved**: {mon_status}",
        f"- **Starting Transform Restored**: {start_status}",
        f"- **Visible Flicker**: {flicker_status}",
        "",
        "## Delivery Latency Distribution",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Samples | {summary.delivery_latencies_ms['count']} |",
        f"| Minimum | {summary.delivery_latencies_ms.get('min')} ms |",
        f"| Median | {summary.delivery_latencies_ms.get('median')} ms |",
        f"| p95 | {summary.delivery_latencies_ms.get('p95')} ms |",
        f"| Maximum | {summary.delivery_latencies_ms.get('max')} ms |",
        "",
        "## Orientation Transitions",
        "",
        "| From | To | SensorProxy Value | Latency |",
        "|---|---|---|---:|",
    ]
    for t in summary.transitions:
        from_label = t.from_orientation or "initial"
        lat = f"{t.delivery_latency_ms:.2f} ms"
        lines.append(f"| {from_label} | {t.to_orientation} | `{t.sensor_value}` | {lat} |")

    lines.extend(
        [
            "",
            "## Four-Corner & Center Alignment Matrix",
            "",
            "Measured in monitor-local logical coordinates using the observed display scale:",
            "",
        ]
    )

    for a in summary.alignments:
        lines.append(f"### Orientation: {a.orientation} (transform {a.transform})")
        lines.append("")
        lines.append("| Device | Point | Target (x, y) | Actual (x, y) | Delta (px) | Result |")
        lines.append("|---|---|---:|---:|---:|---|")
        for s in a.touch_samples:
            target_str = f"({s.target_x:.0f}, {s.target_y:.0f})"
            actual_str = f"({s.actual_x:.0f}, {s.actual_y:.0f})"
            status = "PASS" if s.passed else "FAIL"
            delta_str = f"{s.delta_pixels:.1f}"
            lines.append(
                f"| Touch | {s.point_name} | {target_str} | {actual_str} | {delta_str} | {status} |"
            )
        for s in a.pen_samples:
            target_str = f"({s.target_x:.0f}, {s.target_y:.0f})"
            actual_str = f"({s.actual_x:.0f}, {s.actual_y:.0f})"
            status = "PASS" if s.passed else "FAIL"
            delta_str = f"{s.delta_pixels:.1f}"
            lines.append(
                f"| Pen | {s.point_name} | {target_str} | {actual_str} | {delta_str} | {status} |"
            )
        lines.append("")

    return "\n".join(lines)


async def run_guided_physical_campaign(
    target_orientations: Sequence[Orientation] = (
        Orientation.RIGHT,
        Orientation.INVERTED,
        Orientation.LEFT,
        Orientation.NORMAL,
    ),
    step_timeout_sec: float = 30.0,
    prompt_callback: Callable[[str], None] | None = None,
    sample_callback: Callable[[str, Orientation, TargetPoint], tuple[float, float]] | None = None,
    surface_callback: (
        Callable[
            [Orientation, tuple[TargetPoint, ...]],
            dict[str, tuple[tuple[float, float], ...]],
        ]
        | None
    ) = None,
    flicker_callback: Callable[[Orientation], bool] | None = None,
    source: OrientationSource | None = None,
    adapter: RotationAdapter | None = None,
    monitor_query: Callable[[], list[dict[str, Any]]] = query_monitors_safe,
) -> OrientationCampaignSummary:
    """Run an interactive guided hardware orientation campaign listening to SensorProxy."""
    if sample_callback is None and surface_callback is None:
        raise ValueError("sample_callback_required")

    source = source or SensorProxySource()
    session = await source.connect()

    monitors_before = monitor_query()
    internal_before = next(m for m in monitors_before if m["name"] == "eDP-1")
    original_transform = internal_before["transform"]
    original_orientation = _TRANSFORM_ORIENTATION[original_transform]
    external_before = [m for m in monitors_before if m["name"] != "eDP-1"]

    adapter = adapter or HyprlandRotationAdapter()
    alignments = []
    transitions = []
    latencies = []
    orientations_tested = []
    current_orientation = session.current_event.orientation or original_orientation
    flicker_observed = False
    mount_direction_valid = True

    try:
        for target in target_orientations:
            sensor_name, transform = _ORIENTATION_MAP[target]
            if prompt_callback:
                msg = (
                    f"Please rotate the physical device to {target.name.upper()} "
                    f"('{sensor_name}')..."
                )
                prompt_callback(msg)

            matched = False
            if current_orientation == target:
                matched = True
            else:
                async with asyncio.timeout(step_timeout_sec):
                    async for event in session.events():
                        if event.orientation == target:
                            matched = True
                            break

            if not matched:
                mount_direction_valid = False
                raise TimeoutError(f"Timed out waiting for physical rotation to {target.name}")

            start_t = time.monotonic()
            adapter.apply(target)
            elapsed_ms = (time.monotonic() - start_t) * 1000.0

            from_name = current_orientation.name.lower() if current_orientation else None
            transitions.append(
                OrientationTransitionRecord(
                    from_orientation=from_name,
                    to_orientation=target.name.lower(),
                    sensor_value=sensor_name,
                    delivery_latency_ms=elapsed_ms,
                )
            )
            latencies.append(elapsed_ms)
            orientations_tested.append(target.name.lower())
            current_orientation = target

            expected_points = compute_expected_points(
                target,
                width_px=internal_before["width"],
                height_px=internal_before["height"],
                scale=internal_before["scale"],
                inset=_INSET,
            )
            if surface_callback is not None:
                positions = surface_callback(target, expected_points)
                try:
                    touch_positions = positions["touch"]
                    pen_positions = positions["pen"]
                except KeyError as error:
                    raise RuntimeError("coordinate_sample_missing") from error
                if len(touch_positions) != 5 or len(pen_positions) != 5:
                    raise RuntimeError("coordinate_sample_count_mismatch")
                touch_samples = tuple(
                    evaluate_alignment_sample(point, *position)
                    for point, position in zip(expected_points, touch_positions, strict=True)
                )
                pen_samples = tuple(
                    evaluate_alignment_sample(point, *position)
                    for point, position in zip(expected_points, pen_positions, strict=True)
                )
            else:
                assert sample_callback is not None
                touch_samples = tuple(
                    evaluate_alignment_sample(pt, *sample_callback("touch", target, pt))
                    for pt in expected_points
                )
                pen_samples = tuple(
                    evaluate_alignment_sample(pt, *sample_callback("pen", target, pt))
                    for pt in expected_points
                )
            if flicker_callback is not None:
                flicker_observed = flicker_callback(target) or flicker_observed

            logical_w = (
                internal_before["width"] / internal_before["scale"]
                if target in (Orientation.NORMAL, Orientation.INVERTED)
                else internal_before["height"] / internal_before["scale"]
            )
            logical_h = (
                internal_before["height"] / internal_before["scale"]
                if target in (Orientation.NORMAL, Orientation.INVERTED)
                else internal_before["width"] / internal_before["scale"]
            )

            alignments.append(
                OrientationAlignmentResult(
                    orientation=target.name.lower(),
                    transform=transform,
                    logical_width=logical_w,
                    logical_height=logical_h,
                    touch_samples=touch_samples,
                    pen_samples=pen_samples,
                    passed=(
                        all(s.passed for s in touch_samples) and all(s.passed for s in pen_samples)
                    ),
                )
            )
    finally:
        adapter.apply(original_orientation)
        await session.close()

    monitors_after = monitor_query()
    internal_after = next(m for m in monitors_after if m["name"] == "eDP-1")
    external_after = [m for m in monitors_after if m["name"] != "eDP-1"]

    starting_transform_restored = internal_after["transform"] == original_transform
    external_monitors_preserved = external_after == external_before

    return OrientationCampaignSummary(
        mount_direction_valid=mount_direction_valid,
        orientations_tested=tuple(orientations_tested),
        transitions=tuple(transitions),
        alignments=tuple(alignments),
        external_monitors_preserved=external_monitors_preserved,
        starting_transform_restored=starting_transform_restored,
        delivery_latencies_ms=summarize_latencies(latencies),
        flicker_observed=flicker_observed,
        summary_passed=(
            all(a.passed for a in alignments)
            and mount_direction_valid
            and starting_transform_restored
            and external_monitors_preserved
            and not flicker_observed
        ),
    )
