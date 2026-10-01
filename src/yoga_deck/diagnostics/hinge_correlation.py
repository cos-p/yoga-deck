"""Correlate monotonic hinge samples with tablet-switch edges."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HingeSample:
    timestamp_ns: int
    degrees: float


@dataclass(frozen=True, slots=True)
class SwitchEdge:
    timestamp_ns: int
    folded: bool


@dataclass(frozen=True, slots=True)
class ThresholdCorrelation:
    total_edges: int
    matched_edges: int
    missing_edges: int
    coverage: float
    folded_degrees: tuple[float, ...]
    laptop_degrees: tuple[float, ...]
    thresholds_available: bool


def correlate_thresholds(
    edges: tuple[SwitchEdge, ...],
    samples: tuple[HingeSample, ...],
    *,
    window_ms: float = 300,
    minimum_coverage: float = 0.8,
) -> ThresholdCorrelation:
    ordered_edges = sorted(edges, key=lambda item: item.timestamp_ns)
    available = list(sorted(samples, key=lambda item: item.timestamp_ns))
    folded: list[float] = []
    laptop: list[float] = []
    window_ns = window_ms * 1_000_000

    for edge in ordered_edges:
        candidates = [
            (abs(sample.timestamp_ns - edge.timestamp_ns), index, sample)
            for index, sample in enumerate(available)
            if abs(sample.timestamp_ns - edge.timestamp_ns) <= window_ns
        ]
        if not candidates:
            continue
        _, index, sample = min(candidates, key=lambda item: item[0])
        available.pop(index)
        (folded if edge.folded else laptop).append(sample.degrees)

    matched = len(folded) + len(laptop)
    total = len(edges)
    coverage = matched / total if total else 0.0
    thresholds_available = coverage >= minimum_coverage and bool(folded) and bool(laptop)
    return ThresholdCorrelation(
        total_edges=total,
        matched_edges=matched,
        missing_edges=total - matched,
        coverage=coverage,
        folded_degrees=tuple(folded),
        laptop_degrees=tuple(laptop),
        thresholds_available=thresholds_available,
    )
