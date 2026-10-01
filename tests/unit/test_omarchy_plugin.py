import importlib
import importlib.util
import json
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).parents[2] / "omarchy-plugin"
_STATUS_PATH = _PLUGIN_DIR / "status.py"

_spec = importlib.util.spec_from_file_location("yoga_deck_plugin_status", _STATUS_PATH)
status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(status)


def test_plugin_manifest_is_valid() -> None:
    manifest = json.loads((_PLUGIN_DIR / "manifest.json").read_text())
    assert manifest["schemaVersion"] == 1
    assert manifest["id"] == "cos.yoga-deck"
    assert manifest["entryPoints"]["barWidget"] == "Panel.qml"


def test_status_bridge_only_uses_runtime_transport() -> None:
    source = _STATUS_PATH.read_text()
    assert "hyprctl" not in source
    assert "/dev/input" not in source
    assert "fcntl" not in source
    assert "subprocess" not in source


def test_collect_status_forwards_runtime_payload() -> None:
    expected = {
        "schema_version": 1,
        "available": True,
        "posture": "tablet",
        "inputs_enabled": False,
        "orientation": "left",
        "orientation_lock": True,
        "error": None,
    }
    assert status.collect_status(requester=lambda payload: expected) == expected


def test_controls_send_explicit_commands() -> None:
    requests = []

    def requester(payload):
        requests.append(payload)
        return {"ok": True}

    status.send_command("set_lock", True, requester=requester)
    status.send_command("rotate", "right", requester=requester)
    status.send_command("recover_inputs", requester=requester)

    assert requests == [
        {"command": "set_lock", "value": True},
        {"command": "rotate", "value": "right"},
        {"command": "recover_inputs"},
    ]


def test_degraded_backend_is_reported_without_fabricated_state() -> None:
    response = status.collect_status(
        requester=lambda payload: {
            "schema_version": 1,
            "available": False,
            "error": "runtime_unavailable",
        }
    )
    assert response["available"] is False
    assert "posture" not in response


def test_status_bridge_fails_closed_without_a_private_runtime_directory(monkeypatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    with pytest.raises(RuntimeError, match="xdg_runtime_dir_unavailable"):
        status.socket_path()

    assert status.collect_status()["error"] == "runtime_unavailable"


_PANEL_PATH = _PLUGIN_DIR / "Panel.qml"


def test_settings_controls_send_the_same_command_for_a_change_and_for_a_reset() -> None:
    requests = []

    def requester(payload):
        requests.append(payload)
        return {"ok": True}

    status.collect_config(requester=requester)
    status.set_config("osk.landscape_height", "400", requester=requester)
    status.set_config("osk.font", None, requester=requester)

    assert requests == [
        {"command": "get_config"},
        {"command": "set_config", "setting": "osk.landscape_height", "value": "400"},
        # No value at all, which the runtime reads as "hand this key back to config.toml".
        {"command": "set_config", "setting": "osk.font"},
    ]


def test_the_panel_renders_settings_from_the_payload_rather_than_a_second_schema() -> None:
    """No setting may be named in QML.

    The runtime's settings table is the only place a setting exists. If the panel spelled one
    out, adding a setting would mean editing QML as well, and the two lists would drift.
    """

    panel = _PANEL_PATH.read_text()
    settings = importlib.import_module("yoga_deck.core.settings")

    for name in settings.SETTING_NAMES:
        assert name not in panel


def test_the_panel_has_a_control_for_every_kind_of_setting_that_exists() -> None:
    """A kind with no control would leave a setting on screen with no way to change it."""

    panel = _PANEL_PATH.read_text()
    settings = importlib.import_module("yoga_deck.core.settings")

    for spec in settings.SETTING_SPECS:
        assert f'kind === "{spec.kind}"' in panel


def test_the_panel_still_reaches_the_runtime_only_through_the_status_bridge() -> None:
    panel = _PANEL_PATH.read_text()

    assert "hyprctl" not in panel
    assert "XDG_CONFIG_HOME" not in panel
    # One helper, and it is the transport client. The panel opens no file and no socket of
    # its own, so everything it shows and everything it changes went through the runtime.
    assert panel.count("Qt.resolvedUrl(") == 1
    assert 'Qt.resolvedUrl("status.py")' in panel
