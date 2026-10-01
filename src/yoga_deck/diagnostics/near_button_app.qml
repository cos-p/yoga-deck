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
  WlrLayershell.namespace: "yoga-deck-near-button-test"
  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

  property int secondsRemaining: Number(Quickshell.env("YOGA_DECK_NEAR_BUTTON_SECONDS"))
  property int totalSeconds: Number(Quickshell.env("YOGA_DECK_NEAR_BUTTON_SECONDS"))
  property int repetitions: Number(Quickshell.env("YOGA_DECK_NEAR_BUTTON_TRIALS"))
  property string observedPointerType: "waiting"
  property var pendingPointerTypes: []
  readonly property int repetition: Math.min(
    repetitions,
    Math.floor((totalSeconds - secondsRemaining) / (totalSeconds / repetitions)) + 1
  )

  function record(pointerType) {
    observedPointerType = pointerType
    pendingPointerTypes.push(pointerType)
    launchNext()
  }

  function launchNext() {
    if (sink.running || pendingPointerTypes.length === 0) return
    var pointerType = pendingPointerTypes.shift()
    sink.command = [
      Quickshell.env("YOGA_DECK_NEAR_BUTTON_PYTHON"),
      "-m", "yoga_deck.diagnostics.near_button_app_sink",
      Quickshell.env("YOGA_DECK_NEAR_BUTTON_OUTPUT"),
      pointerType,
      Quickshell.env("YOGA_DECK_NEAR_BUTTON_TRIALS"),
      Quickshell.env("YOGA_DECK_NEAR_BUTTON_SECONDS"),
      Quickshell.env("YOGA_DECK_NEAR_BUTTON_STARTED")
    ]
    sink.running = true
  }

  Process {
    id: sink
    onExited: function(code) {
      if (code !== 0) Qt.quit()
      else root.launchNext()
    }
  }

  Timer {
    interval: 1000
    repeat: true
    running: true
    onTriggered: {
      root.secondsRemaining -= 1
      if (root.secondsRemaining <= 0) Qt.quit()
    }
  }

  Rectangle {
    anchors.fill: parent
    color: root.color

    Text {
      anchors.centerIn: parent
      color: root.observedPointerType === "eraser" ? "#68d391" : "white"
      font.pixelSize: 26
      text: root.observedPointerType === "eraser"
            ? "Application received eraser mode"
            : Quickshell.env("YOGA_DECK_NEAR_BUTTON_INSTRUCTION")
    }

    Text {
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 32
      color: "#b8c0cc"
      font.pixelSize: 22
      text: "Repetition " + root.repetition + " of " + root.repetitions
            + " — closes in " + root.secondsRemaining + " seconds"
    }

    HoverHandler {
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Pen
      onActiveChanged: if (active) root.record("pen")
    }

    HoverHandler {
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Eraser
      onActiveChanged: if (active) root.record("eraser")
    }

    TapHandler {
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Pen
      onTapped: function(point, button) { root.record("pen") }
    }

    TapHandler {
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Eraser
      onTapped: function(point, button) { root.record("eraser") }
    }

    PointHandler {
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Pen
      onActiveChanged: if (active) root.record("pen")
    }

    PointHandler {
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Eraser
      onActiveChanged: if (active) root.record("eraser")
    }
  }
}
