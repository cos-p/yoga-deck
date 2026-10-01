import os

import pytest

from yoga_deck.diagnostics.pen_actions import run_guided_pen_campaign

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1",
        reason="set YOGA_DECK_HARDWARE=1 for physical pen observation",
    ),
]


def test_physical_pen_action_campaign() -> None:
    summary = run_guided_pen_campaign(trials=5)

    assert summary.scenarios["proximity"].complete == 5
    assert summary.scenarios["tip_pressure"].complete == 5
    assert len(summary.pressure_peaks) == 5
