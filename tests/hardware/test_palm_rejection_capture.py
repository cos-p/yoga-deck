import os

import pytest

from yoga_deck.diagnostics.palm_rejection import run_guided_palm_campaign

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.live_desktop,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1"
        or os.environ.get("YOGA_DECK_LIVE_DESKTOP") != "1",
        reason="set YOGA_DECK_HARDWARE=1 and YOGA_DECK_LIVE_DESKTOP=1 for live capture",
    ),
]


def test_native_wacom_palm_rejection_campaign() -> None:
    summary = run_guided_palm_campaign(trials=5)

    assert summary.scenarios["palm_only"].attempted == 5
    assert summary.scenarios["pen_hover_palm"].attempted == 5
    assert summary.scenarios["pen_tip_palm"].attempted == 5
    assert summary.scenarios["recovery"].attempted >= 5
    assert len(summary.recovery_latency_ms) == 5
