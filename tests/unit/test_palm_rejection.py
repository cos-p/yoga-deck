import json

import pytest

from yoga_deck.diagnostics.palm_rejection import (
    PalmEvent,
    PalmProtocolError,
    ScenarioSummary,
    analyze_trials,
    loads_events,
    trial_index,
)


def _trial_events(
    trial: int,
    scenario: str,
    *,
    delivered: bool,
    recovery_latency_ms: float | None = None,
) -> list[PalmEvent]:
    events = [
        PalmEvent(trial, scenario, "control", "trial_start", 0.0),
        PalmEvent(trial, scenario, "raw_touch", "contact", 100.0),
    ]
    if scenario != "palm_only":
        events.insert(0, PalmEvent(trial, scenario, "pen", "proximity_in", 90.0))
    if scenario == "pen_tip_palm":
        events.insert(1, PalmEvent(trial, scenario, "pen", "tip_down", 95.0))
    if delivered:
        events.append(PalmEvent(trial, scenario, "surface", "touch", 101.0))
    if scenario == "recovery":
        events.extend(
            [
                PalmEvent(trial, scenario, "pen", "proximity_out", 110.0),
                PalmEvent(
                    trial,
                    scenario,
                    "surface",
                    "touch",
                    110.0 + float(recovery_latency_ms),
                ),
            ]
        )
    return events


def test_analyze_trials_distinguishes_attempted_suppressed_and_recovery() -> None:
    events = []
    events += _trial_events(0, "palm_only", delivered=True)
    events += _trial_events(1, "pen_hover_palm", delivered=False)
    events += _trial_events(2, "pen_tip_palm", delivered=False)
    events += _trial_events(3, "recovery", delivered=False, recovery_latency_ms=42.5)

    summary = analyze_trials(events)

    assert summary.scenarios["palm_only"].attempted == 1
    assert summary.scenarios["palm_only"].delivered == 1
    assert summary.scenarios["pen_hover_palm"].suppressed == 1
    assert summary.scenarios["pen_tip_palm"].suppressed == 1
    assert summary.recovery_latency_ms == (42.5,)


def test_analyze_trials_does_not_count_missing_raw_contact_as_suppression() -> None:
    events = [PalmEvent(0, "pen_hover_palm", "pen", "proximity_in", 10.0)]

    summary = analyze_trials(events)

    assert summary.scenarios["pen_hover_palm"].attempted == 0
    assert summary.scenarios["pen_hover_palm"].suppressed == 0


def test_analyze_trials_deduplicates_repeated_contacts_in_one_slot() -> None:
    events = [
        PalmEvent(0, "pen_hover_palm", "control", "trial_start", 0.0),
        PalmEvent(0, "pen_hover_palm", "pen", "proximity_in", 0.0),
    ] + [
        PalmEvent(0, "pen_hover_palm", "raw_touch", "contact", float(index * 10))
        for index in range(5)
    ]
    events += [
        PalmEvent(0, "pen_hover_palm", "surface", "touch", 1.0),
        PalmEvent(0, "pen_hover_palm", "surface", "touch", 11.0),
    ]

    summary = analyze_trials(events)

    assert summary.scenarios["pen_hover_palm"] == ScenarioSummary(1, 1, 0, 0)


def test_analyze_trials_rejects_contact_without_expected_pen_state() -> None:
    events = [
        PalmEvent(0, "pen_hover_palm", "control", "trial_start", 0.0),
        PalmEvent(0, "pen_hover_palm", "raw_touch", "contact", 10.0),
        PalmEvent(0, "pen_hover_palm", "surface", "touch", 11.0),
        PalmEvent(1, "pen_tip_palm", "control", "trial_start", 20.0),
        PalmEvent(1, "pen_tip_palm", "pen", "proximity_in", 20.0),
        PalmEvent(1, "pen_tip_palm", "raw_touch", "contact", 21.0),
    ]

    summary = analyze_trials(events)

    assert summary.scenarios["pen_hover_palm"].attempted == 0
    assert summary.scenarios["pen_hover_palm"].invalid_state == 1
    assert summary.scenarios["pen_tip_palm"].attempted == 0
    assert summary.scenarios["pen_tip_palm"].invalid_state == 1


def test_trial_index_uses_bounded_equal_time_slots() -> None:
    assert trial_index(0, trials=5, duration_seconds=30) == 0
    assert trial_index(5999, trials=5, duration_seconds=30) == 0
    assert trial_index(6000, trials=5, duration_seconds=30) == 1
    assert trial_index(40000, trials=5, duration_seconds=30) == 4


def test_loads_events_accepts_only_narrow_normalized_protocol() -> None:
    payload = json.dumps(
        {
            "trial": 2,
            "scenario": "pen_hover_palm",
            "source": "raw_touch",
            "event": "contact",
            "elapsed_ms": 12.25,
        }
    )
    assert loads_events(payload) == (PalmEvent(2, "pen_hover_palm", "raw_touch", "contact", 12.25),)

    with pytest.raises(PalmProtocolError, match="palm_protocol_invalid"):
        loads_events(
            '{"trial":0,"scenario":"other","source":"raw_touch","event":"contact","elapsed_ms":1}'
        )


def test_palm_surface_observes_application_delivery_without_device_identity() -> None:
    from yoga_deck.diagnostics.palm_rejection import QML_PATH

    source = QML_PATH.read_text()
    assert "PointerDevice.TouchScreen" in source
    assert "PointerDevice.Stylus" in source
    assert "uniqueId" not in source
    assert "/dev/input" not in source
    assert "WlrKeyboardFocus.None" in source
    assert "Timer" in source
    assert "Qt.quit()" in source
    assert "secondsRemaining" in source
