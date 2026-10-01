from yoga_deck.diagnostics.hinge_correlation import (
    HingeSample,
    SwitchEdge,
    correlate_thresholds,
)


def test_reordered_samples_are_correlated_without_reuse() -> None:
    edges = (
        SwitchEdge(timestamp_ns=1_000_000_000, folded=True),
        SwitchEdge(timestamp_ns=2_000_000_000, folded=False),
    )
    samples = (
        HingeSample(timestamp_ns=2_010_000_000, degrees=195.0),
        HingeSample(timestamp_ns=990_000_000, degrees=215.0),
    )

    result = correlate_thresholds(edges, samples, window_ms=50, minimum_coverage=1.0)

    assert result.coverage == 1.0
    assert result.folded_degrees == (215.0,)
    assert result.laptop_degrees == (195.0,)
    assert result.thresholds_available is True


def test_missing_samples_report_coverage_and_withhold_thresholds() -> None:
    edges = (
        SwitchEdge(timestamp_ns=1, folded=True),
        SwitchEdge(timestamp_ns=2_000_000_000, folded=False),
    )
    samples = (HingeSample(timestamp_ns=1, degrees=210.0),)

    result = correlate_thresholds(edges, samples, window_ms=50, minimum_coverage=0.8)

    assert result.coverage == 0.5
    assert result.missing_edges == 1
    assert result.thresholds_available is False


def test_one_sample_cannot_be_reused_for_two_edges() -> None:
    edges = (
        SwitchEdge(timestamp_ns=1_000_000_000, folded=True),
        SwitchEdge(timestamp_ns=1_010_000_000, folded=False),
    )
    samples = (HingeSample(timestamp_ns=1_005_000_000, degrees=200.0),)

    result = correlate_thresholds(edges, samples, window_ms=20)

    assert result.matched_edges == 1
    assert result.thresholds_available is False
