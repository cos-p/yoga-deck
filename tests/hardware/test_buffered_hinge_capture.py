import json
import os
from statistics import median

import pytest

from yoga_deck.diagnostics.hinge_campaign import capture_hinge_campaign

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1",
        reason="set YOGA_DECK_HARDWARE=1 for guided privileged capture",
    ),
]


def test_buffered_hinge_threshold_correlation() -> None:
    """Fold and unfold five times after starting this privileged guided test."""

    expected_events = int(os.environ.get("YOGA_DECK_HINGE_EVENTS", "10"))
    result = capture_hinge_campaign(max_events=expected_events)
    correlation = result.correlation

    diagnostic = {
        "sample_count": result.sample_count,
        "sample_offset_range_ms": result.sample_offset_range_ms,
        "coverage": correlation.coverage,
        "missing_edges": correlation.missing_edges,
    }
    print(json.dumps(diagnostic, sort_keys=True))

    assert correlation.total_edges == expected_events
    assert correlation.coverage >= 0.8
    assert correlation.thresholds_available is True
    summary = {
        **diagnostic,
        "folded_hinge_degrees": _distribution(correlation.folded_degrees),
        "laptop_hinge_degrees": _distribution(correlation.laptop_degrees),
        "delivery_latency_ms": _distribution(result.delivery_latencies_ms),
    }
    encoded = json.dumps(summary, sort_keys=True)
    assert "/dev/" not in encoded
    print(encoded)


def _distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": median(ordered),
        "max": ordered[-1],
    }
