import json

from yoga_deck.adapters.linux_discovery import (
    LinuxDiscovery,
    classify_input,
    parse_hyprland_monitors,
)
from yoga_deck.diagnostics import ComponentStatus, render_text
from yoga_deck.diagnostics.support_report import build_support_report, render_support_text


def test_input_classification_uses_name_and_capabilities_not_event_number() -> None:
    assert classify_input("ThinkPad Extra Buttons", {"sw"}) == "tablet_switch"
    assert classify_input("AT Translated Set 2 keyboard", {"keys"}) == "internal_keyboard"
    assert classify_input("SYNA8004 touchpad", {"keys", "absolute"}) == "touchpad"
    assert classify_input("TPPS/2 ALPS TrackPoint", {"keys", "relative"}) == "trackpoint"
    assert classify_input("Wacom Pen and multitouch sensor Pen", {"keys", "absolute"}) == "pen"
    assert classify_input("Wacom Pen and multitouch sensor Finger", {"absolute"}) == "touchscreen"
    assert classify_input("External USB Keyboard event19", {"keys"}) is None


def test_hyprland_parser_omits_description_and_unrelated_monitor_settings() -> None:
    payload = json.dumps(
        [
            {
                "name": "eDP-1",
                "description": "Panel with private serial 123",
                "width": 1920,
                "height": 1080,
                "scale": 1.25,
                "transform": 1,
                "focused": True,
            }
        ]
    )

    monitors = parse_hyprland_monitors(payload)

    assert monitors[0].connector == "eDP-1"
    assert monitors[0].internal is True
    assert "description" not in monitors[0].as_dict()


def test_malformed_hyprland_payload_returns_diagnostic_instead_of_raising() -> None:
    monitors, diagnostic = parse_hyprland_monitors("not-json", with_diagnostic=True)

    assert monitors == ()
    assert diagnostic == "malformed_hyprland_response"


def test_missing_sysfs_sources_are_reported_without_private_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(LinuxDiscovery, "_monitors", staticmethod(lambda diagnostics: ()))

    report = LinuxDiscovery(sys_root=tmp_path).inspect()

    assert report.components
    assert all(component.status is ComponentStatus.MISSING for component in report.components)
    assert str(tmp_path) not in json.dumps(report.as_dict())


def _fake_input(sys_root, event, name, **capabilities) -> None:
    device = sys_root / "class/input" / event / "device"
    (device / "capabilities").mkdir(parents=True)
    (device / "name").write_text(name + "\n")
    for kernel_name, value in capabilities.items():
        (device / "capabilities" / kernel_name).write_text(value + "\n")


def _fake_x390_inputs(sys_root) -> None:
    _fake_input(sys_root, "event7", "ThinkPad Extra Buttons", sw="1", key="1")
    _fake_input(sys_root, "event12", "Wacom Pen and multitouch sensor Pen", key="1", abs="1")
    _fake_input(sys_root, "event3", "AT Translated Set 2 keyboard", key="1")


def test_unreadable_runtime_nodes_are_permission_denied_not_available(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(LinuxDiscovery, "_monitors", staticmethod(lambda diagnostics: ()))
    _fake_x390_inputs(tmp_path / "sys")
    checked = []

    def denied(node):
        checked.append(node)
        return False

    discovery = LinuxDiscovery(tmp_path / "sys", dev_root=tmp_path / "dev", readable=denied)
    report = discovery.inspect()

    statuses = {component.role: component.status for component in report.components}
    assert statuses["tablet_switch"] is ComponentStatus.PERMISSION_DENIED
    assert statuses["pen"] is ComponentStatus.PERMISSION_DENIED
    # Hyprland reads the keyboard through logind; its node is never the runtime's to open.
    assert statuses["internal_keyboard"] is ComponentStatus.AVAILABLE
    assert sorted(node.name for node in checked) == ["event12", "event7"]
    assert report.diagnostics == ("input_permission_denied",)
    assert report.as_dict()["remediation"] == ["input_group_required"]
    assert discovery.runtime_input_denied() == frozenset({"tablet_switch", "pen"})

    text = render_text(report)
    assert "tablet_switch: permission_denied [keys, switch]" in text
    assert "remediation input_group_required:" in text
    assert "docs/install.md" in text
    encoded = json.dumps(report.as_dict()) + text
    assert "event7" not in encoded and "event12" not in encoded
    assert str(tmp_path) not in encoded


def test_readable_runtime_nodes_stay_available(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(LinuxDiscovery, "_monitors", staticmethod(lambda diagnostics: ()))
    _fake_x390_inputs(tmp_path / "sys")

    discovery = LinuxDiscovery(tmp_path / "sys", dev_root=tmp_path / "dev", readable=lambda n: True)
    report = discovery.inspect()

    statuses = {component.role: component.status for component in report.components}
    assert statuses["tablet_switch"] is ComponentStatus.AVAILABLE
    assert statuses["pen"] is ComponentStatus.AVAILABLE
    assert report.diagnostics == ()
    assert report.as_dict()["remediation"] == []
    assert discovery.runtime_input_denied() == frozenset()


def test_support_report_marks_permission_denied_discovery_degraded(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(LinuxDiscovery, "_monitors", staticmethod(lambda diagnostics: ()))
    _fake_x390_inputs(tmp_path / "sys")
    inspection = LinuxDiscovery(
        tmp_path / "sys", dev_root=tmp_path / "dev", readable=lambda n: False
    ).inspect()

    report = build_support_report(inspection, None)

    assert report.status == "degraded"
    assert report.as_dict()["schema_version"] == 2
    assert report.as_dict()["discovery"]["remediation"] == ["input_group_required"]
    assert "remediation input_group_required:" in render_support_text(report)
