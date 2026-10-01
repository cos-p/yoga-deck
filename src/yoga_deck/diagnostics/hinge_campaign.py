"""Guided concurrent switch and buffered-IIO threshold capture."""

from __future__ import annotations

import time
from dataclasses import dataclass
from math import degrees
from threading import Event as ThreadEvent
from threading import Thread

from yoga_deck.adapters.iio_buffer import (
    BufferedHingeDevice,
    FrameDecoder,
    IIOError,
    discover_hinge_metadata,
    normalize_timestamp_ns,
)
from yoga_deck.adapters.tablet_switch import TabletSwitchSource

from .hinge_correlation import (
    HingeSample,
    SwitchEdge,
    ThresholdCorrelation,
    correlate_thresholds,
)


@dataclass(frozen=True, slots=True)
class HingeCampaignResult:
    correlation: ThresholdCorrelation
    delivery_latencies_ms: tuple[float, ...]
    sample_count: int
    sample_offset_range_ms: tuple[float, float] | None


def capture_hinge_campaign(max_events: int = 10) -> HingeCampaignResult:
    """Capture synchronized streams; requires explicit privileged hardware execution."""

    metadata = discover_hinge_metadata()
    monotonic_reference_ns = time.monotonic_ns()
    realtime_reference_ns = time.time_ns()
    buffered = BufferedHingeDevice(metadata)
    decoded = []
    reader_error: list[IIOError] = []
    stop_reader = ThreadEvent()

    def read_buffer() -> None:
        decoder = FrameDecoder(metadata.layout)
        try:
            for chunk in buffered.chunks(stop_event=stop_reader):
                decoded.extend(decoder.feed(chunk))
        except (IIOError, OSError) as error:
            if isinstance(error, IIOError):
                reader_error.append(error)

    with buffered:
        reader = Thread(target=read_buffer, name="yoga-deck-iio-buffer", daemon=True)
        reader.start()
        try:
            source = TabletSwitchSource(max_events=max_events, hinge_reader=lambda: None)
            source.record("hinge-threshold")
            time.sleep(0.35)
        finally:
            stop_reader.set()
            reader.join(timeout=2)
            if reader.is_alive():
                raise IIOError("iio_reader_did_not_stop")
    if reader_error:
        raise reader_error[0]

    samples = tuple(
        HingeSample(
            timestamp_ns=normalize_timestamp_ns(
                sample.timestamp_ns,
                monotonic_reference_ns=monotonic_reference_ns,
                realtime_reference_ns=realtime_reference_ns,
            ),
            degrees=degrees((sample.values["hinge"] + metadata.offset) * metadata.scale),
        )
        for sample in decoded
    )
    edges = tuple(
        SwitchEdge(observation.monotonic_timestamp_ns, observation.enabled)
        for observation in source.observations
    )
    latencies = tuple(
        observation.delivery_latency_ms
        for observation in source.observations
        if observation.delivery_latency_ms is not None
    )
    first_edge_ns = edges[0].timestamp_ns if edges else None
    offsets = (
        tuple((sample.timestamp_ns - first_edge_ns) / 1_000_000 for sample in samples)
        if first_edge_ns is not None
        else ()
    )
    return HingeCampaignResult(
        correlation=correlate_thresholds(edges, samples),
        delivery_latencies_ms=latencies,
        sample_count=len(samples),
        sample_offset_range_ms=(min(offsets), max(offsets)) if offsets else None,
    )
