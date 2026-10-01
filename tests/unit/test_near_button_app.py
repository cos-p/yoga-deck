import json

import pytest

from yoga_deck.diagnostics.near_button_app import (
    ApplicationPointerEvent,
    NearButtonProtocolError,
    analyze_near_button_trials,
    loads_application_events,
)
from yoga_deck.diagnostics.pen_actions import PenActionEvent


def test_analysis_requires_raw_eraser_and_application_eraser_in_same_trial() -> None:
    control_raw_events = (
        PenActionEvent(0, "proximity", "proximity", "down", 1.0),
        PenActionEvent(0, "proximity", "proximity", "up", 2.0),
    )
    control_application_events = (ApplicationPointerEvent(0, "pen", 1.5),)
    raw_events = (
        PenActionEvent(0, "button_near_tip", "eraser", "down", 10.0),
        PenActionEvent(0, "button_near_tip", "eraser", "up", 20.0),
        PenActionEvent(1, "button_near_tip", "eraser", "down", 60.0),
        PenActionEvent(1, "button_near_tip", "eraser", "up", 70.0),
    )
    application_events = (
        ApplicationPointerEvent(0, "eraser", 12.0),
        ApplicationPointerEvent(1, "pen", 65.0),
        ApplicationPointerEvent(2, "eraser", 110.0),
    )

    summary = analyze_near_button_trials(
        raw_events,
        application_events,
        trials=3,
        control_raw_events=control_raw_events,
        control_application_events=control_application_events,
    )

    assert summary.as_dict() == {
        "attempted": 3,
        "control_raw_pen": 1,
        "control_application_pen": 1,
        "raw_eraser": 2,
        "application_pen": 1,
        "application_eraser": 2,
        "confirmed": 1,
        "raw_only": 1,
        "application_only": 1,
    }


def test_application_protocol_is_narrow_and_rejects_extra_fields() -> None:
    payload = json.dumps({"trial": 1, "pointer_type": "eraser", "elapsed_ms": 12.5})

    assert loads_application_events(payload) == (ApplicationPointerEvent(1, "eraser", 12.5),)

    with pytest.raises(NearButtonProtocolError, match="near_button_protocol_invalid"):
        loads_application_events(
            '{"trial":0,"pointer_type":"eraser","elapsed_ms":1,"device":"private"}'
        )


def test_application_surface_accepts_only_stylus_pen_and_eraser_without_identity() -> None:
    from yoga_deck.diagnostics.near_button_app import QML_PATH

    source = QML_PATH.read_text()
    assert "PointerDevice.Stylus" in source
    assert "PointerDevice.Pen" in source
    assert "PointerDevice.Eraser" in source
    assert "TapHandler" in source
    assert "PointHandler" in source
    assert "uniqueId" not in source
    assert "/dev/input" not in source
    assert "WlrKeyboardFocus.None" in source
    assert "Timer" in source
    assert "Qt.quit()" in source
