from yoga_deck.diagnostics import (
    Component,
    ComponentStatus,
    InspectionReport,
    Monitor,
    build_support_report,
    render_json,
    render_support_json,
    render_support_text,
    render_text,
)


def test_report_rejects_unrecognized_component_roles() -> None:
    try:
        Component("device-serial-123", ComponentStatus.AVAILABLE)
    except ValueError as error:
        assert str(error) == "unsupported component role"
    else:
        raise AssertionError("unsafe component role was accepted")


def test_renderers_only_include_normalized_public_fields() -> None:
    report = InspectionReport(
        components=(
            Component("internal_keyboard", ComponentStatus.AVAILABLE, ("keys",)),
            Component("orientation_sensor", ComponentStatus.ERROR),
        ),
        monitors=(Monitor("eDP-1", True, 1920, 1080, 1.25, 3),),
        diagnostics=("malformed_hyprland_response",),
    )

    text = render_text(report)
    encoded = render_json(report)

    assert "internal_keyboard: available [keys]" in text
    assert "malformed_hyprland_response" in text
    assert '"schema_version": 2' in encoded
    assert "username" not in encoded


def test_monitor_rejects_private_or_malformed_connector_names() -> None:
    for connector in ("/home/alice/eDP-1", "serial:123", "not a connector"):
        try:
            Monitor(connector, True, 1920, 1080, 1.0, 0)
        except ValueError as error:
            assert str(error) == "unsafe monitor connector"
        else:
            raise AssertionError("unsafe connector was accepted")


def test_report_rejects_raw_diagnostic_text() -> None:
    try:
        InspectionReport(diagnostics=("failed at /home/alice/device-serial-123",))
    except ValueError as error:
        assert str(error) == "unsupported diagnostic code"
    else:
        raise AssertionError("unsafe diagnostic text was accepted")


def test_support_report_has_versioned_redacted_schema() -> None:
    inspection = InspectionReport(
        components=(Component("tablet_switch", ComponentStatus.AVAILABLE, ("switch",)),),
        monitors=(Monitor("eDP-1", True, 1920, 1080, 1.25, 0),),
    )

    report = build_support_report(
        inspection,
        {
            "schema_version": 1,
            "available": True,
            "sequence": 928,
            "posture": "laptop",
            "inputs_enabled": True,
            "orientation": "normal",
            "orientation_lock": False,
            "error": None,
        },
    )

    assert report.as_dict() == {
        "schema_version": 2,
        "redacted": True,
        "status": "ready",
        "discovery": {
            "available": True,
            "components": [
                {
                    "role": "tablet_switch",
                    "status": "available",
                    "capabilities": ["switch"],
                }
            ],
            "monitors": [
                {
                    "connector": "eDP-1",
                    "internal": True,
                    "width": 1920,
                    "height": 1080,
                    "scale": 1.25,
                    "transform": 0,
                }
            ],
            "diagnostics": [],
            "remediation": [],
            "error": None,
        },
        "runtime": {
            "available": True,
            "posture": "laptop",
            "inputs_enabled": True,
            "orientation": "normal",
            "orientation_lock": False,
            "error": None,
        },
        "limitations": [
            "external_monitor_hotplug_unvalidated",
            "hard_compositor_reconnect_unvalidated",
            "pen_silo_event_unverified",
        ],
    }
    assert "928" not in render_support_json(report)
    assert "Yoga Deck redacted support report" in render_support_text(report)
    assert "runtime: available" in render_support_text(report)


def test_support_report_rejects_untrusted_runtime_content() -> None:
    private = "/home/alice/event12 clipboard-secret monitor-description"

    report = build_support_report(
        InspectionReport(),
        {
            "schema_version": 1,
            "available": True,
            "posture": private,
            "inputs_enabled": private,
            "orientation": private,
            "orientation_lock": private,
            "error": private,
            "device_id": private,
        },
    )

    encoded = render_support_json(report)
    assert report.status == "degraded"
    assert report.runtime.as_dict() == {
        "available": False,
        "posture": None,
        "inputs_enabled": None,
        "orientation": None,
        "orientation_lock": None,
        "error": "runtime_status_invalid",
    }
    assert private not in encoded


def test_support_report_safely_degrades_when_sources_are_unavailable() -> None:
    report = build_support_report(None, None)

    assert report.status == "degraded"
    assert report.discovery.as_dict() == {
        "available": False,
        "components": [],
        "monitors": [],
        "diagnostics": [],
        "remediation": [],
        "error": "discovery_unavailable",
    }
    assert report.runtime.error == "runtime_unavailable"


def test_support_report_rejects_malformed_runtime_payload_type() -> None:
    report = build_support_report(InspectionReport(), ["/home/alice/event12"])  # type: ignore[arg-type]

    assert report.runtime.available is False
    assert report.runtime.error == "runtime_status_invalid"
    assert "alice" not in render_support_json(report)
