import QtQuick
import QtTest
import "../../omarchy-plugin" as YogaDeck

TestCase {
  name: "YogaDeckPanel"

  Component {
    id: panelComponent
    YogaDeck.Panel { }
  }

  function makePanel() {
    var panel = createTemporaryObject(panelComponent, this)
    verify(panel !== null, "Panel.qml must instantiate through the real Qt engine")
    return panel
  }

  function test_available_and_degraded_status() {
    var panel = makePanel()

    panel.applyData(JSON.stringify({
      "schema_version": 1,
      "available": true,
      "posture": "tablet",
      "orientation": "left",
      "orientation_lock": true,
      "inputs_enabled": false
    }))
    compare(panel.backendAvailable, true)
    compare(panel.posture, "tablet")
    compare(panel.orientation, "left")
    compare(panel.orientationLock, true)
    compare(panel.inputsEnabled, false)
    compare(panel.degradedReason, "")

    panel.applyData(JSON.stringify({
      "schema_version": 1,
      "available": true,
      "posture": "laptop",
      "orientation": "normal",
      "orientation_lock": false,
      "inputs_enabled": true,
      "degraded": "input_access_denied"
    }))
    compare(panel.degradedReason, "input_access_denied")

    panel.applyData(JSON.stringify({
      "schema_version": 1,
      "available": false,
      "error": "runtime_unavailable"
    }))
    compare(panel.backendAvailable, false)
    compare(panel.posture, "unknown")
    compare(panel.errorText, "runtime_unavailable")
    compare(panel.degradedReason, "")
  }

  function test_schema_invalid_config_degrades_without_fabricating_settings() {
    var panel = makePanel()

    panel.applyConfig(JSON.stringify({
      "ok": true,
      "config": {"schema_version": 99, "settings": []}
    }))

    compare(panel.configLoaded, false)
    compare(panel.configEntries.length, 0)
    verify(panel.configError !== "")
  }

  function test_schema_invalid_status_clears_stale_available_state() {
    var panel = makePanel()
    panel.applyData(JSON.stringify({
      "schema_version": 1,
      "available": true,
      "posture": "tablet",
      "orientation": "right",
      "orientation_lock": false,
      "inputs_enabled": false
    }))
    compare(panel.backendAvailable, true)

    panel.applyData(JSON.stringify({"schema_version": 1, "available": true}))

    compare(panel.backendAvailable, false)
    compare(panel.posture, "unknown")
    verify(panel.errorText !== "")
  }

  function test_valid_settings_snapshot_reaches_the_render_model() {
    var panel = makePanel()
    panel.applyConfig(JSON.stringify({
      "ok": true,
      "config": {
        "schema_version": 1,
        "overrides_file": "config.local.toml",
        "settings": [
          {
            "name": "osk.landscape_height",
            "section": "osk",
            "kind": "integer",
            "summary": "Height",
            "value": 320,
            "source": null,
            "slider_minimum": 140,
            "slider_maximum": 560,
            "choices": []
          }
        ],
        "ignored": []
      }
    }))

    compare(panel.configLoaded, true)
    compare(panel.configEntries.length, 1)
    compare(panel.configEntries[0].value, 320)
    compare(panel.overridesFile, "config.local.toml")
  }

  function test_safety_controls_follow_backend_availability() {
    var panel = makePanel()
    var lockControl = findChild(panel, "orientationLockControl")
    var actionProcess = findChild(panel, "actionProcess")
    var recoveryControl = findChild(panel, "recoveryControl")
    verify(lockControl !== null)
    verify(actionProcess !== null)
    verify(recoveryControl !== null)

    var rotateNames = [
      "rotateNormalControl",
      "rotateRightControl",
      "rotateInvertedControl",
      "rotateLeftControl"
    ]
    var rotateControls = []
    for (var index = 0; index < rotateNames.length; index++) {
      var rotateControl = findChild(panel, rotateNames[index])
      verify(rotateControl !== null)
      rotateControls.push(rotateControl)
    }

    panel.applyData(JSON.stringify({
      "schema_version": 1,
      "available": false,
      "error": "runtime_unavailable"
    }))
    compare(lockControl.enabled, false)
    compare(recoveryControl.enabled, false)
    for (index = 0; index < rotateControls.length; index++)
      compare(rotateControls[index].enabled, false)

    panel.applyData(JSON.stringify({
      "schema_version": 1,
      "available": true,
      "posture": "laptop",
      "orientation": "normal",
      "orientation_lock": false,
      "inputs_enabled": true
    }))
    compare(lockControl.enabled, true)
    compare(recoveryControl.enabled, true)
    for (index = 0; index < rotateControls.length; index++)
      compare(rotateControls[index].enabled, true)

    lockControl.clicked()
    compare(actionProcess.command[actionProcess.command.length - 2], "--set-lock")
    compare(actionProcess.command[actionProcess.command.length - 1], "true")
    for (index = 0; index < rotateControls.length; index++) {
      rotateControls[index].clicked()
      compare(actionProcess.command[actionProcess.command.length - 2], "--rotate")
      compare(actionProcess.command[actionProcess.command.length - 1],
        ["normal", "right", "inverted", "left"][index])
    }
    recoveryControl.clicked()
    compare(actionProcess.command[actionProcess.command.length - 1], "--recover-inputs")
  }
}
