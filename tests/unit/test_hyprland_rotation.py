import json
from dataclasses import dataclass

import pytest

from yoga_deck.adapters.hyprland_rotation import (
    HyprlandRotationAdapter,
    HyprlandRotationError,
    RotationTargets,
    parse_rotation_targets,
)
from yoga_deck.core import Orientation


@dataclass
class Result:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class Runner:
    def __init__(self, results=()) -> None:
        self.results = list(results)
        self.calls = []

    def run(self, args, timeout):
        self.calls.append((args, timeout))
        return self.results.pop(0) if self.results else Result()


def device_payload(*, include_external=True):
    touch = [{"name": "wacom-pen-and-multitouch-sensor-finger"}]
    tablets = [{"name": "wacom-pen-and-multitouch-sensor-pen"}]
    if include_external:
        touch.append({"name": "external-touchscreen"})
        tablets.append({"name": "pen-passthrough"})
        tablets.append({"type": "tabletTool"})
    return json.dumps({"touch": touch, "tablets": tablets})


def monitor_payload():
    return json.dumps(
        [
            {
                "name": "eDP-1",
                "width": 1920,
                "height": 1080,
                "refreshRate": 60.02,
                "x": 0,
                "y": 0,
                "scale": 1.25,
                "transform": 0,
                "vrr": False,
                "colorManagementPreset": "srgb",
            },
            {"name": "DP-1", "scale": 1.0, "transform": 0},
        ]
    )


def test_discovery_selects_only_exact_owned_rotation_targets() -> None:
    targets = parse_rotation_targets(monitor_payload(), device_payload())

    assert targets == RotationTargets(
        output="eDP-1",
        touchscreen="wacom-pen-and-multitouch-sensor-finger",
        pen="wacom-pen-and-multitouch-sensor-pen",
    )


@pytest.mark.parametrize("missing", ["monitor", "touch", "pen"])
def test_discovery_requires_every_member_of_rotation_transaction(missing) -> None:
    monitors = "[]" if missing == "monitor" else monitor_payload()
    devices = json.loads(device_payload(include_external=False))
    if missing == "touch":
        devices["touch"] = []
    if missing == "pen":
        devices["tablets"] = []

    with pytest.raises(HyprlandRotationError) as error:
        parse_rotation_targets(monitors, json.dumps(devices))

    assert error.value.code == "rotation_targets_incomplete"


def test_rotation_replays_observed_monitor_state_with_only_transform_changed() -> None:
    runner = Runner([Result(monitor_payload()), Result(device_payload()), Result()])
    adapter = HyprlandRotationAdapter(runner)

    adapter.apply(Orientation.RIGHT)

    args, _ = runner.calls[-1]
    assert args[:2] == ["hyprctl", "eval"]
    lua = args[2]
    assert 'output = "eDP-1"' in lua
    assert 'mode = "1920x1080@60.020"' in lua
    assert 'position = "0x0"' in lua
    assert "scale = 1.25" in lua
    assert "transform = 3" in lua
    assert "vrr = 0" in lua
    assert 'cm = "srgb"' in lua
    assert (
        'hl.device({ name = "wacom-pen-and-multitouch-sensor-finger", '
        'transform = 3, output = "eDP-1" })' in lua
    )
    assert (
        'hl.device({ name = "wacom-pen-and-multitouch-sensor-pen", '
        'transform = 3, output = "eDP-1" })' in lua
    )
    assert 'output = "DP-1"' not in lua


@pytest.mark.parametrize(
    ("orientation", "transform"),
    [
        (Orientation.NORMAL, 0),
        (Orientation.RIGHT, 3),
        (Orientation.INVERTED, 2),
        (Orientation.LEFT, 1),
    ],
)
def test_all_orientations_map_to_wayland_transforms(orientation, transform) -> None:

    runner = Runner([Result(monitor_payload()), Result(device_payload()), Result()])

    HyprlandRotationAdapter(runner).apply(orientation)

    assert f"transform = {transform}" in runner.calls[-1][0][2]


def test_compositor_errors_are_redacted() -> None:
    runner = Runner(
        [Result(monitor_payload()), Result(device_payload()), Result("", "private output", 1)]
    )

    with pytest.raises(HyprlandRotationError) as error:
        HyprlandRotationAdapter(runner).apply(Orientation.LEFT)

    assert error.value.code == "rotation_transaction_failed"
    assert "private" not in str(error.value)
