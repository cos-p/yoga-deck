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
  WlrLayershell.namespace: "yoga-deck-palm-rejection"
  WlrLayershell.layer: WlrLayer.Overlay
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

  property int secondsRemaining: Number(Quickshell.env("YOGA_DECK_PALM_SECONDS"))
  property int totalSeconds: Number(Quickshell.env("YOGA_DECK_PALM_SECONDS"))
  property int repetitions: Number(Quickshell.env("YOGA_DECK_PALM_TRIALS"))
  readonly property int repetition: Math.min(
    repetitions,
    Math.floor((totalSeconds - secondsRemaining) / (totalSeconds / repetitions)) + 1
  )

  function recordTouch() {
    sink.command = [
      Quickshell.env("YOGA_DECK_PALM_PYTHON"),
      "-m", "yoga_deck.diagnostics.palm_rejection_sink",
      Quickshell.env("YOGA_DECK_PALM_OUTPUT"),
      Quickshell.env("YOGA_DECK_PALM_SCENARIO"),
      Quickshell.env("YOGA_DECK_PALM_TRIALS"),
      Quickshell.env("YOGA_DECK_PALM_SECONDS"),
      Quickshell.env("YOGA_DECK_PALM_STARTED")
    ]
    sink.running = true
  }

  Process { id: sink }

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
      color: "white"
      font.pixelSize: 24
      text: Quickshell.env("YOGA_DECK_PALM_INSTRUCTION")
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

    TapHandler {
      acceptedButtons: Qt.NoButton
      acceptedDevices: PointerDevice.TouchScreen
      acceptedPointerTypes: PointerDevice.Finger
      onTapped: function(point, button) { root.recordTouch() }
    }

    HoverHandler {
      acceptedDevices: PointerDevice.Stylus
      acceptedPointerTypes: PointerDevice.Pen | PointerDevice.Eraser
    }
  }
}
