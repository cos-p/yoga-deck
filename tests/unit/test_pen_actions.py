import json

import pytest

from yoga_deck.diagnostics.pen_actions import (
    PenActionEvent,
    PenProtocolError,
    analyze_pen_actions,
    loads_pen_events,
    normalize_pen_event,
)


def test_normalize_pen_keys_and_pressure_without_coordinates() -> None:
    assert normalize_pen_event(("BTN_DIGI", "BTN_TOOL_PEN"), 1) == (
        "proximity",
        "down",
        None,
    )
    assert normalize_pen_event("BTN_TOOL_PEN", 1) == ("proximity", "down", None)
    assert normalize_pen_event("BTN_TOOL_PEN", 0) == ("proximity", "up", None)
    assert normalize_pen_event("BTN_TOUCH", 1) == ("tip", "down", None)
    assert normalize_pen_event("BTN_STYLUS", 1) == ("barrel_primary", "down", None)
    assert normalize_pen_event("BTN_STYLUS2", 0) == ("barrel_secondary", "up", None)
    assert normalize_pen_event("BTN_TOOL_RUBBER", 1) == ("eraser", "down", None)
    assert normalize_pen_event("ABS_PRESSURE", 2048, pressure_max=4095) == (
        "pressure",
        "sample",
        pytest.approx(0.500122),
    )
    assert normalize_pen_event("ABS_X", 123) is None


def test_pen_protocol_rejects_extra_or_identifying_fields() -> None:
    payload = json.dumps(
        {
            "trial": 0,
            "scenario": "tip_pressure",
            "action": "tip",
            "state": "down",
            "elapsed_ms": 1.0,
            "value": None,
            "device": "/dev/input/event12",
        }
    )

    with pytest.raises(PenProtocolError, match="pen_protocol_invalid"):
        loads_pen_events(payload)


def test_summary_counts_complete_cycles_and_pressure_peaks_per_trial() -> None:
    events = (
        PenActionEvent(0, "tip_pressure", "tip", "down", 10.0),
        PenActionEvent(0, "tip_pressure", "pressure", "sample", 11.0, 0.25),
        PenActionEvent(0, "tip_pressure", "pressure", "sample", 12.0, 0.75),
        PenActionEvent(0, "tip_pressure", "tip", "up", 13.0),
        PenActionEvent(1, "tip_pressure", "tip", "down", 20.0),
        PenActionEvent(1, "tip_pressure", "pressure", "sample", 21.0, 0.5),
        PenActionEvent(1, "tip_pressure", "tip", "up", 22.0),
        PenActionEvent(0, "button_far_tip", "barrel_primary", "down", 30.0),
        PenActionEvent(0, "button_far_tip", "barrel_primary", "up", 31.0),
        PenActionEvent(1, "button_far_tip", "barrel_primary", "down", 40.0),
    )

    summary = analyze_pen_actions(events, trials=2)

    assert summary.scenarios["tip_pressure"].complete == 2
    assert summary.scenarios["button_far_tip"].complete == 1
    assert summary.scenarios["button_far_tip"].incomplete == 1
    assert summary.pressure_peaks == pytest.approx((0.5, 0.75))
    assert summary.as_dict()["pressure_peak"]["median"] == pytest.approx(0.625)


def test_duplicate_down_packets_do_not_create_extra_cycles() -> None:
    events = (
        PenActionEvent(0, "eraser", "eraser", "down", 1.0),
        PenActionEvent(0, "eraser", "eraser", "down", 2.0),
        PenActionEvent(0, "eraser", "eraser", "up", 3.0),
    )

    result = analyze_pen_actions(events, trials=1).scenarios["eraser"]

    assert result.complete == 1
    assert result.incomplete == 0


def test_action_cycle_may_cross_an_artificial_trial_boundary() -> None:
    events = (
        PenActionEvent(0, "proximity", "proximity", "down", 5999.0),
        PenActionEvent(1, "proximity", "proximity", "up", 6001.0),
    )

    result = analyze_pen_actions(events, trials=1).scenarios["proximity"]

    assert result.complete == 1


def test_pressure_distribution_is_capped_to_requested_trials() -> None:
    events = []
    for trial in range(3):
        events.extend(
            (
                PenActionEvent(trial, "tip_pressure", "tip", "down", trial * 10.0),
                PenActionEvent(
                    trial,
                    "tip_pressure",
                    "pressure",
                    "sample",
                    trial * 10.0 + 1,
                    0.2 + trial / 10,
                ),
                PenActionEvent(trial, "tip_pressure", "tip", "up", trial * 10.0 + 2),
            )
        )

    summary = analyze_pen_actions(events, trials=2)

    assert summary.scenarios["tip_pressure"].complete == 2
    assert summary.pressure_peaks == pytest.approx((0.2, 0.3))


def test_summary_reports_unexpected_button_code_in_physical_condition() -> None:
    events = (
        PenActionEvent(0, "button_far_tip", "barrel_primary", "down", 1.0),
        PenActionEvent(0, "button_far_tip", "barrel_primary", "up", 2.0),
    )

    summary = analyze_pen_actions(events, trials=1)

    assert summary.scenarios["button_far_tip"].complete == 1
    assert summary.observed_cycles["button_far_tip"] == {"barrel_primary": 1}
    assert summary.as_dict()["observed_cycles"]["button_far_tip"] == {"barrel_primary": 1}


def test_barrel_button_chatter_within_50_ms_is_one_observed_cycle() -> None:
    events = (
        PenActionEvent(0, "button_far_tip", "barrel_primary", "down", 100.0),
        PenActionEvent(0, "button_far_tip", "barrel_primary", "up", 120.0),
        PenActionEvent(0, "button_far_tip", "barrel_primary", "down", 125.3),
        PenActionEvent(0, "button_far_tip", "barrel_primary", "up", 145.0),
    )

    summary = analyze_pen_actions(events, trials=2)

    assert summary.raw_cycles["button_far_tip"] == {"barrel_primary": 2}
    assert summary.observed_cycles["button_far_tip"] == {"barrel_primary": 1}
