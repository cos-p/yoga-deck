import json
import os

import pytest

from yoga_deck.adapters.tablet_switch import TabletSwitchSource, summarize_observations

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1",
        reason="set YOGA_DECK_HARDWARE=1 for guided physical capture",
    ),
]


def test_repeated_fold_unfold_threshold_and_latency_distribution() -> None:
    """Fold and unfold five times after starting this guided test."""

    source = TabletSwitchSource(max_events=10)

    records = source.record("repeated-fold-unfold")

    assert {record.event.enabled for record in records} == {False, True}
    assert len(source.observations) == 10
    summary = summarize_observations(source.observations)
    assert summary["delivery_latency_ms"]["count"] == 10
    folded_count = summary["folded_hinge_degrees"]["count"]
    laptop_count = summary["laptop_hinge_degrees"]["count"]
    assert folded_count == laptop_count
    assert folded_count in {0, 5}
    summary["hinge_measurement"] = {
        "available": folded_count == 5,
        "diagnostic": None if folded_count == 5 else "hinge_samples_not_synchronized",
    }
    encoded = json.dumps(summary, sort_keys=True)
    assert "/dev/" not in encoded
    print(encoded)
