import json
import os
import subprocess

import pytest

from yoga_deck.adapters.hyprland_rotation import HyprlandRotationAdapter
from yoga_deck.core import Orientation

pytestmark = pytest.mark.live_desktop


def _monitors():
    result = subprocess.run(
        ["hyprctl", "-j", "monitors", "all"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_live_rotation_cycle_preserves_external_monitor_records() -> None:
    if os.environ.get("YOGA_DECK_LIVE_DESKTOP") != "1":
        pytest.skip("set YOGA_DECK_LIVE_DESKTOP=1 to mutate the current session")
    before = _monitors()
    internal = next(monitor for monitor in before if monitor["name"] == "eDP-1")
    if internal["transform"] not in range(4):
        pytest.skip("flipped initial transforms are outside this recovery contract")
    original = tuple(Orientation)[internal["transform"]]
    external_before = [monitor for monitor in before if monitor["name"] != "eDP-1"]
    adapter = HyprlandRotationAdapter()

    try:
        for orientation in Orientation:
            adapter.apply(orientation)
    finally:
        adapter.apply(original)

    external_after = [monitor for monitor in _monitors() if monitor["name"] != "eDP-1"]
    assert external_after == external_before
