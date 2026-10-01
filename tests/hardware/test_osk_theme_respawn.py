import json
import os

import pytest

from yoga_deck.diagnostics.osk_theme_respawn import run_guided_osk_theme_respawn

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.live_desktop,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1"
        or os.environ.get("YOGA_DECK_LIVE_DESKTOP") != "1",
        reason="set hardware and live-desktop opt-ins for the OSK theme respawn test",
    ),
]


def test_palette_respawn_preserves_the_fcitx_input_context(tmp_path) -> None:
    """Focus an empty text field when prompted, type a few characters, then unfocus it."""

    summary = run_guided_osk_theme_respawn(theme="white", seconds=60, output=tmp_path)

    assert summary.child_replaced
    assert summary.bus_name_retained
    assert summary.bus_owner_unchanged
    assert summary.layer_remapped
    assert summary.geometry_unchanged
    assert summary.focus_preserved

    encoded = json.dumps(summary.as_dict(), sort_keys=True)
    assert "/dev/" not in encoded
    assert os.environ["USER"] not in encoded
    print(encoded)
