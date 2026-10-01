import os

import pytest

from yoga_deck.diagnostics.ocr_capture import run_guided_ocr_capture

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.live_desktop,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1"
        or os.environ.get("YOGA_DECK_LIVE_DESKTOP") != "1",
        reason="set YOGA_DECK_HARDWARE=1 and YOGA_DECK_LIVE_DESKTOP=1 for live OCR capture",
    ),
]


def test_guided_omarchy_ocr_capture_campaign() -> None:
    summary = run_guided_ocr_capture(trials=5)

    assert summary.attempted == 5
    assert summary.command_failed == 0
    assert len(summary.latency_ms) == 5
