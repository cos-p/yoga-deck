import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

PanelWindow {
  id: root
  anchors { top: true; right: true; bottom: true; left: true }
  screen: Quickshell.screens.find(function(candidate) { return candidate.name === "eDP-1" })
  color: "#ee111318"
  exclusionMode: ExclusionMode.Ignore
  WlrLayershell.namespace: "yoga-deck-coordinate-oracle"
  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

  property var targets: JSON.parse(Quickshell.env("YOGA_DECK_ORACLE_TARGETS"))
  property string role: "touch"
  property int targetIndex: 0
  property bool recording: false
  property real acceptanceRadius: 120
  readonly property var currentTarget: targets[targetIndex]

  function record(point) {
    if (recording) return
    var dx = point.position.x - currentTarget.x
    var dy = point.position.y - currentTarget.y
    if (Math.sqrt(dx * dx + dy * dy) > acceptanceRadius) return
    recording = true
    sink.command = [
      Quickshell.env("YOGA_DECK_ORACLE_PYTHON"),
      "-m", "yoga_deck.diagnostics.coordinate_oracle_sink",
      Quickshell.env("YOGA_DECK_ORACLE_OUTPUT"),
      role, String(targetIndex), String(point.position.x), String(point.position.y)
    ]
    sink.running = true
  }

  function advance() {
    if (targetIndex < 4) {
      targetIndex += 1
    } else if (role === "touch") {
      role = "pen"
      targetIndex = 0
    } else {
      Qt.quit()
    }
    recording = false
  }

  Process {
    id: sink
    onExited: function(code) {
      if (code === 0) root.advance()
      else Qt.quit()
    }
  }

  Rectangle {
    anchors.fill: parent
    color: root.color

    Text {
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.top: parent.top
      anchors.topMargin: 24
      color: "white"
      font.pixelSize: 22
      text: "Yoga Deck: " + (root.role === "touch" ? "finger" : "pen")
    }

    Text {
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 24
      color: "#b8c0cc"
      font.pixelSize: 18
      text: "Target " + (root.targetIndex + 1) + " of 5 — Escape cancels"
    }

    Rectangle {
      width: 34
      height: 34
      radius: 17
      x: root.currentTarget.x - width / 2
      y: root.currentTarget.y - height / 2
      color: "transparent"
      border.width: 4
      border.color: "#68d391"

      Rectangle {
        width: 6
        height: 6
        radius: 3
        anchors.centerIn: parent
        color: "#68d391"
      }
    }

    TapHandler {
      enabled: root.role === "touch" && !root.recording
      acceptedButtons: Qt.NoButton
      acceptedDevices: PointerDevice.TouchScreen
      acceptedPointerTypes: PointerDevice.Finger
      onTapped: function(point, button) { root.record(point) }
    }

    TapHandler {
      enabled: root.role === "pen" && !root.recording
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Pen | PointerDevice.Eraser
      onTapped: function(point, button) { root.record(point) }
    }

    focus: true
    Keys.onEscapePressed: Qt.quit()
  }
}
