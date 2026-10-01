import asyncio
import json
import os

import pytest

from yoga_deck.diagnostics.orientation_campaign import (
    capture_device_position,
    format_campaign_markdown_report,
    query_monitors_safe,
    run_guided_physical_campaign,
)

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.live_desktop,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1"
        or os.environ.get("YOGA_DECK_LIVE_DESKTOP") != "1",
        reason="set YOGA_DECK_HARDWARE=1 and YOGA_DECK_LIVE_DESKTOP=1 for live capture",
    ),
]


def test_orientation_and_four_corner_alignment_campaign() -> None:
    """Validate mount direction, four orientations, four corners + center touch/pen alignment."""

    def sample(device, orientation, target):
        monitor = next(item for item in query_monitors_safe() if item.get("name") == "eDP-1")
        return capture_device_position(
            device,
            orientation,
            width_px=monitor["width"],
            height_px=monitor["height"],
            scale=monitor["scale"],
            wait_callback=lambda: input(
                f"Place {device} at {target.name} ({target.target_x:.0f}, "
                f"{target.target_y:.0f}) in {orientation.value}; press Enter: "
            ),
        )

    def flicker(orientation):
        return input(f"Visible flicker entering {orientation.value}? [y/N] ").lower() in {
            "y",
            "yes",
        }

    summary = asyncio.run(
        run_guided_physical_campaign(
            prompt_callback=print,
            sample_callback=sample,
            flicker_callback=flicker,
        )
    )

    assert summary.summary_passed is True
    report_md = format_campaign_markdown_report(summary)
    assert "/dev/" not in report_md
    print(json.dumps(summary.as_dict(), indent=2))
