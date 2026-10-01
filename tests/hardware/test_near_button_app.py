import os

import pytest

from yoga_deck.diagnostics.near_button_app import run_guided_near_button_test

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.live_desktop,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1"
        or os.environ.get("YOGA_DECK_LIVE_DESKTOP") != "1",
        reason="set hardware and live-desktop opt-ins for the near-button application test",
    ),
]


def test_near_button_reaches_application_as_eraser() -> None:
    summary = run_guided_near_button_test(trials=3, seconds=15)

    assert summary.control_raw_pen == 1
    assert summary.control_application_pen == 1
    assert summary.raw_eraser == 3
    assert summary.application_eraser == 3
    assert summary.confirmed == 3
