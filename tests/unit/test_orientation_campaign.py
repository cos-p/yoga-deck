import math
from collections.abc import Iterator

import pytest

from yoga_deck.core import Orientation
from yoga_deck.diagnostics.orientation_campaign import (
    AlignmentSample,
    OrientationAlignmentResult,
    OrientationCampaignSummary,
    OrientationTransitionRecord,
    TargetPoint,
    compute_expected_points,
    evaluate_alignment_sample,
    format_campaign_markdown_report,
    run_guided_physical_campaign,
    summarize_latencies,
    transform_normalized_position,
)


def test_compute_expected_points_normal_and_portrait():
    normal_points = compute_expected_points(Orientation.NORMAL, 1920, 1080, 1.25, inset=40.0)
    # Logical dimensions: 1920 / 1.25 = 1536, 1080 / 1.25 = 864
    assert len(normal_points) == 5
    point_dict = {p.name: (p.target_x, p.target_y) for p in normal_points}
    assert point_dict["top_left"] == (40.0, 40.0)
    assert point_dict["top_right"] == (1496.0, 40.0)
    assert point_dict["bottom_right"] == (1496.0, 824.0)
    assert point_dict["bottom_left"] == (40.0, 824.0)
    assert point_dict["center"] == (768.0, 432.0)

    right_points = compute_expected_points(Orientation.RIGHT, 1920, 1080, 1.25, inset=40.0)
    # Logical dimensions: 1080 / 1.25 = 864, 1920 / 1.25 = 1536
    assert len(right_points) == 5
    r_dict = {p.name: (p.target_x, p.target_y) for p in right_points}
    assert r_dict["top_left"] == (40.0, 40.0)
    assert r_dict["top_right"] == (824.0, 40.0)
    assert r_dict["bottom_right"] == (824.0, 1496.0)
    assert r_dict["bottom_left"] == (40.0, 1496.0)
    assert r_dict["center"] == (432.0, 768.0)


def test_evaluate_alignment_sample_pass_fail():
    target = TargetPoint("center", 768.0, 432.0)
    passing = evaluate_alignment_sample(target, 770.0, 430.0, max_tolerance_px=10.0)
    assert passing.passed is True
    assert passing.delta_pixels == pytest.approx(math.hypot(2, -2))

    failing = evaluate_alignment_sample(target, 800.0, 450.0, max_tolerance_px=10.0)
    assert failing.passed is False
    assert failing.delta_pixels > 10.0


@pytest.mark.parametrize(
    ("orientation", "raw", "expected"),
    [
        (Orientation.NORMAL, (0.0, 0.0), (0.0, 0.0)),
        (Orientation.RIGHT, (0.0, 1.0), (0.0, 0.0)),
        (Orientation.INVERTED, (1.0, 1.0), (0.0, 0.0)),
        (Orientation.LEFT, (1.0, 0.0), (0.0, 0.0)),
    ],
)
def test_transform_normalized_position_maps_physical_corner_to_visual_corner(
    orientation, raw, expected
) -> None:
    assert transform_normalized_position(*raw, orientation, 1536.0, 864.0) == expected


def test_summarize_latencies():
    empty = summarize_latencies([])
    assert empty["count"] == 0
    assert empty["median"] is None

    stats = summarize_latencies([1.2, 5.0, 2.4, 3.1, 4.8])
    assert stats["count"] == 5
    assert stats["min"] == 1.2
    assert stats["median"] == 3.1
    assert stats["max"] == 5.0


def test_format_campaign_markdown_report_sanitization():
    sample = AlignmentSample(
        point_name="top_left",
        target_x=40.0,
        target_y=40.0,
        actual_x=42.0,
        actual_y=41.0,
        delta_pixels=2.24,
        passed=True,
    )
    alignment = OrientationAlignmentResult(
        orientation="normal",
        transform=0,
        logical_width=1536.0,
        logical_height=864.0,
        touch_samples=(sample,),
        pen_samples=(sample,),
        passed=True,
    )
    transition = OrientationTransitionRecord(
        from_orientation="normal",
        to_orientation="right",
        sensor_value="right-up",
        delivery_latency_ms=45.2,
    )
    summary = OrientationCampaignSummary(
        mount_direction_valid=True,
        orientations_tested=("normal", "right", "inverted", "left"),
        transitions=(transition,),
        alignments=(alignment,),
        external_monitors_preserved=True,
        starting_transform_restored=True,
        delivery_latencies_ms={"count": 1, "min": 45.2, "median": 45.2, "p95": 45.2, "max": 45.2},
        flicker_observed=False,
        summary_passed=True,
    )

    report = format_campaign_markdown_report(summary)
    assert "# Orientation and Four-Corner Alignment Validation" in report
    assert "Mount Direction Valid**: Pass" in report
    assert "45.2" in report
    # Ensure no raw /dev nodes or personal paths are present
    assert "/dev/" not in report
    assert "/home/" not in report


@pytest.mark.asyncio
async def test_guided_campaign_uses_observed_coordinates_and_human_flicker() -> None:
    from yoga_deck.core import OrientationChanged

    class FakeSession:
        current_event = OrientationChanged(Orientation.NORMAL)

        async def events(self):
            yield OrientationChanged(Orientation.RIGHT)

        async def close(self) -> None:
            self.closed = True

    class FakeSource:
        async def connect(self):
            return FakeSession()

    class FakeAdapter:
        def __init__(self) -> None:
            self.applied = []

        def apply(self, orientation: Orientation) -> None:
            self.applied.append(orientation)

    monitors = [
        {
            "name": "eDP-1",
            "width": 1920,
            "height": 1080,
            "scale": 1.25,
            "transform": 0,
        }
    ]
    observations: Iterator[tuple[float, float]] = iter([(42.0, 39.0)] * 5 + [(44.0, 41.0)] * 5)

    summary = await run_guided_physical_campaign(
        target_orientations=(Orientation.RIGHT,),
        source=FakeSource(),
        adapter=FakeAdapter(),
        monitor_query=lambda: monitors,
        sample_callback=lambda device, orientation, target: next(observations),
        flicker_callback=lambda orientation: True,
    )

    assert summary.alignments[0].touch_samples[0].actual_x == 42.0
    assert summary.alignments[0].pen_samples[0].actual_x == 44.0
    assert summary.flicker_observed is True
    assert summary.summary_passed is False


@pytest.mark.asyncio
async def test_guided_campaign_requires_a_real_sample_callback() -> None:
    with pytest.raises(ValueError, match="sample_callback_required"):
        await run_guided_physical_campaign(target_orientations=())
