import os

import pytest

from yoga_deck.adapters.hyprland_inputs import HyprlandInputAdapter, InputRole

pytestmark = [
    pytest.mark.live_desktop,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_LIVE_DESKTOP") != "1",
        reason="set YOGA_DECK_LIVE_DESKTOP=1 to permit compositor input mutation",
    ),
]


def test_touchpad_can_be_disabled_and_is_always_reenabled() -> None:
    adapter = HyprlandInputAdapter()
    devices = adapter.discover_owned()
    touchpad = next(device for device in devices if device.role is InputRole.TOUCHPAD)

    try:
        adapter.set_enabled(touchpad, False)
    finally:
        adapter.set_enabled(touchpad, True)
