import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "cos.yoga-deck"
  ipcTarget: "cos.yoga-deck"
  manageIpc: false

  property string posture: "laptop"
  property string orientation: "normal"
  property int waylandTransform: 0
  property double scaleFactor: 1.25
  property bool orientationLock: false
  property bool inputsEnabled: true
  property string errorText: ""
  property string degradedReason: ""
  property bool loading: false
  property bool backendAvailable: false

  // Settings arrive as a list of self-describing rows rather than a fixed set of properties,
  // so a new row in the runtime's settings table becomes a new control here with no QML change.
  property var configEntries: []
  property var configIgnored: []
  property string overridesFile: "config.local.toml"
  property string configError: ""
  property bool configLoaded: false

  readonly property bool showPostureIcon: Boolean(setting("showPostureIcon", true))
  readonly property int pollIntervalMs: Math.max(500, Math.min(10000, Number(setting("pollIntervalMs", 2000))))
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // The one bridge to the runtime. Every control shells out to it; nothing here reads a
  // device, a socket, or a config file directly.
  readonly property string bridge: Qt.resolvedUrl("status.py").toString().replace("file://", "")

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function refresh() {
    if (collector.running) return
    loading = true
    collector.command = ["python3", root.bridge]
    collector.running = true
  }

  function applyData(raw) {
    loading = false
    try {
      var value = JSON.parse(String(raw || "{}"))
      if (value.schema_version !== 1 || typeof value.available !== "boolean")
        throw new Error("unsupported status schema")
      backendAvailable = Boolean(value.available)
      if (backendAvailable) {
        if (["unknown", "tablet", "laptop"].indexOf(value.posture) < 0
            || ["normal", "right", "inverted", "left"].indexOf(value.orientation) < 0
            || typeof value.orientation_lock !== "boolean"
            || typeof value.inputs_enabled !== "boolean")
          throw new Error("invalid status payload")
        posture = String(value.posture || "unknown")
        orientation = String(value.orientation || "normal")
        orientationLock = Boolean(value.orientation_lock)
        inputsEnabled = Boolean(value.inputs_enabled)
        degradedReason = value.degraded === "input_access_denied" ? "input_access_denied" : ""
      } else {
        posture = "unknown"
        degradedReason = ""
      }
      errorText = String(value.error || "")
    } catch (e) {
      backendAvailable = false
      posture = "unknown"
      degradedReason = ""
      errorText = "Could not parse status from Yoga Deck"
    }
  }

  function refreshConfig() {
    if (configCollector.running) return
    configCollector.command = ["python3", root.bridge, "--get-config"]
    configCollector.running = true
  }

  function applyConfig(raw) {
    try {
      var response = JSON.parse(String(raw || "{}"))
      var payload = response.config
      if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.settings)
          || !Array.isArray(payload.ignored))
        throw new Error(String(response.error || "unsupported settings schema"))
      for (var index = 0; index < payload.settings.length; index++) {
        var entry = payload.settings[index]
        if (!entry || typeof entry.name !== "string" || typeof entry.section !== "string"
            || ["integer", "text", "flag", "choice"].indexOf(entry.kind) < 0
            || typeof entry.summary !== "string" || !Array.isArray(entry.choices))
          throw new Error("invalid settings payload")
      }
      configEntries = payload.settings || []
      configIgnored = payload.ignored || []
      overridesFile = String(payload.overrides_file || overridesFile)
      configLoaded = true
      configError = ""
    } catch (e) {
      configLoaded = false
      configEntries = []
      configIgnored = []
      configError = "Could not read settings from Yoga Deck"
    }
  }

  // A null value clears the override, handing the setting back to the hand-written file.
  // Values travel as text: the runtime parses them with the same parser the shell prompt
  // uses, so this panel cannot store something `yoga-doctor config set` would refuse.
  function storeSetting(name, value) {
    if (configWriter.running) return
    var argv = ["python3", root.bridge]
    if (value === null) argv.push("--unset-config", String(name))
    else argv.push("--set-config", String(name), String(value))
    configWriter.command = argv
    configWriter.running = true
  }

  function applyWriteResult(raw) {
    try {
      var response = JSON.parse(String(raw || "{}"))
      configError = response.ok === false ? String(response.error || "settings_write_failed") : ""
    } catch (e) {
      configError = "Setting could not be stored"
    }
    refreshConfig()
  }

  function toggleLock() {
    actionProcess.command = ["python3", root.bridge, "--set-lock", orientationLock ? "false" : "true"]
    actionProcess.running = true
  }

  function rotateTo(name) {
    actionProcess.command = ["python3", root.bridge, "--rotate", name]
    actionProcess.running = true
  }

  function recoverInputs() {
    actionProcess.command = ["python3", root.bridge, "--recover-inputs"]
    actionProcess.running = true
  }

  function postureIcon() {
    if (orientationLock) return "󰌾"
    if (posture === "tablet") return "󰓹"
    if (orientation === "right") return "󰹒"
    if (orientation === "left") return "󰹐"
    if (orientation === "inverted") return "󰹑"
    return "󰌢"
  }

  function orientationLabel(name) {
    if (name === "right") return "Portrait Right (90°)"
    if (name === "left") return "Portrait Left (270°)"
    if (name === "inverted") return "Inverted Landscape (180°)"
    return "Landscape Normal (0°)"
  }

  function humanize(text) {
    var words = String(text).replace(/_/g, " ")
    return words.charAt(0).toUpperCase() + words.slice(1)
  }

  function settingLabel(entry) {
    return humanize(String(entry.name).split(".")[1])
  }

  function sectionLabel(section) {
    if (section === "osk") return "On-screen keyboard"
    if (section === "pen") return "Pen"
    return humanize(section)
  }

  function displayValue(entry) {
    if (entry.value === null || entry.value === undefined) return "unset"
    if (entry.value === true) return "on"
    if (entry.value === false) return "off"
    return String(entry.value)
  }

  function sourceLabel(entry) {
    return entry.source ? "from " + String(entry.source) : "default"
  }

  // The panel and the runtime ship together but are installed separately: the plugin is a
  // symlink into a checkout, while the service keeps running whatever it started with. So the
  // most likely reason settings are missing is a runtime older than this panel, and saying so
  // is more use than repeating the transport's own word for it.
  function configMessage() {
    if (configError === "") return "Reading settings…"
    if (configError === "runtime_unavailable") return "Yoga Deck is not running."
    if (configError === "invalid_command")
      return "The running Yoga Deck is older than this panel. Restart yoga-deck to get settings."
    if (configError === "settings_unavailable") return "This Yoga Deck runtime has no settings."
    return "Settings unavailable: " + configError
  }

  onOpenedChanged: if (opened) {
    refresh()
    refreshConfig()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Process {
    id: collector
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyData(text) }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("yoga-deck", text.trim())
    }
    onExited: function(code) {
      root.loading = false
      if (code !== 0) root.errorText = "Status query failed"
    }
  }

  Process {
    id: actionProcess
    objectName: "actionProcess"
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.refresh() }
    onExited: function(code) {
      root.refresh()
    }
  }

  Process {
    id: configCollector
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyConfig(text) }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("yoga-deck", text.trim())
    }
  }

  Process {
    id: configWriter
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyWriteResult(text) }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("yoga-deck", text.trim())
    }
  }

  Timer {
    interval: root.pollIntervalMs
    running: root.opened
    repeat: true
    onTriggered: root.refresh()
  }

  IpcHandler {
    target: root.ipcTarget
    function open() { root.open() }
    function close() { root.close() }
    function toggle() { root.toggle() }
    function refresh(): string { root.refresh(); root.refreshConfig(); return "ok" }
    function lock(): string { root.toggleLock(); return "ok" }
    function rotate(name: string): string { root.rotateTo(name); return "ok" }
    function recover(): string { root.recoverInputs(); return "ok" }
    function set(name: string, value: string): string { root.storeSetting(name, value); return "ok" }
    function unset(name: string): string { root.storeSetting(name, null); return "ok" }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.postureIcon()
    active: root.orientationLock || root.posture === "tablet"
    onPressed: root.toggle()
  }

  KeyboardPanel {
    id: popup
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: popup.fittedContentWidth(Style.space(380))
    contentHeight: popup.fittedContentHeight(contentColumn.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onActivateRequested: root.refresh()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      // The settings section grows with the runtime's settings table, so the panel scrolls
      // rather than being capped at whatever fits today.
      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: contentColumn
          width: panelFlick.width
          spacing: Style.space(14)

          // Header
          Item {
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroText.implicitHeight)

            Text {
              id: heroIcon
              text: root.postureIcon()
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: heroText
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(12)
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                text: "Yoga Deck"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
              }

              Text {
                text: "ThinkPad X390 Yoga"
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          PanelSeparator { foreground: root.foreground }

          // Posture & Mode Card
          Rectangle {
            width: parent.width
            implicitHeight: postureCol.implicitHeight + Style.space(20)
            color: Color.background
            radius: Style.cornerRadius
            border.color: Color.muted
            border.width: 1

            Column {
              id: postureCol
              anchors.fill: parent
              anchors.margins: Style.space(10)
              spacing: Style.space(8)

              RowLayout {
                width: parent.width
                Text {
                  text: "Convertible Posture:"
                  color: Color.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
                Item { Layout.fillWidth: true }
                Text {
                  text: !root.backendAvailable ? "Unavailable" : (root.posture === "tablet" ? "󰓹 Tablet Mode" : "󰌢 Laptop Mode")
                  color: root.foreground
                  font.family: root.fontFamily
                  font.bold: true
                  font.pixelSize: Style.font.body
                }
              }

              RowLayout {
                width: parent.width
                Text {
                  text: "Internal Inputs:"
                  color: Color.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
                Item { Layout.fillWidth: true }
                Text {
                  text: !root.backendAvailable ? "Unknown" : (root.inputsEnabled ? "Active" : "Inhibited")
                  color: root.inputsEnabled ? root.foreground : Color.urgent
                  font.family: root.fontFamily
                  font.bold: true
                  font.pixelSize: Style.font.body
                }
              }

              Text {
                width: parent.width
                visible: root.backendAvailable && root.degradedReason === "input_access_denied"
                text: "No input access: posture detection is off (see install guide)"
                color: Color.urgent
                wrapMode: Text.WordWrap
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
            }
          }

          // Orientation & Lock Controls Card
          Rectangle {
            width: parent.width
            implicitHeight: orientCol.implicitHeight + Style.space(20)
            color: Color.background
            radius: Style.cornerRadius
            border.color: Color.muted
            border.width: 1

            Column {
              id: orientCol
              anchors.fill: parent
              anchors.margins: Style.space(10)
              spacing: Style.space(10)

              RowLayout {
                width: parent.width
                Text {
                  text: "Current Rotation:"
                  color: Color.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
                Item { Layout.fillWidth: true }
                Text {
                  text: root.orientationLabel(root.orientation)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.bold: true
                  font.pixelSize: Style.font.body
                }
              }

              // Orientation Lock Toggle Button
              Button {
                objectName: "orientationLockControl"
                width: parent.width
                enabled: root.backendAvailable
                text: root.orientationLock ? "󰌾 Orientation Locked (Click to Unlock)" : "󰌿 Auto-Rotate Active (Click to Lock)"
                active: root.orientationLock
                bordered: true
                onClicked: root.toggleLock()
              }

              // Quick Manual Rotate Buttons
              Text {
                text: "Manual Rotation Override:"
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              RowLayout {
                width: parent.width
                spacing: Style.space(6)

                Button {
                  objectName: "rotateNormalControl"
                  Layout.fillWidth: true
                  enabled: root.backendAvailable
                  text: "Normal"
                  bordered: true
                  onClicked: root.rotateTo("normal")
                }
                Button {
                  objectName: "rotateRightControl"
                  Layout.fillWidth: true
                  enabled: root.backendAvailable
                  text: "Right"
                  bordered: true
                  onClicked: root.rotateTo("right")
                }
                Button {
                  objectName: "rotateInvertedControl"
                  Layout.fillWidth: true
                  enabled: root.backendAvailable
                  text: "Inverted"
                  bordered: true
                  onClicked: root.rotateTo("inverted")
                }
                Button {
                  objectName: "rotateLeftControl"
                  Layout.fillWidth: true
                  enabled: root.backendAvailable
                  text: "Left"
                  bordered: true
                  onClicked: root.rotateTo("left")
                }
              }
            }
          }

          // Emergency Recovery Button
          Button {
            objectName: "recoveryControl"
            width: parent.width
            enabled: root.backendAvailable
            text: "󰁯 Recover Internal Inputs"
            bordered: true
            onClicked: root.recoverInputs()
          }

          PanelSeparator { foreground: root.foreground }

          // Settings. This is the only way to change these values in the posture they are for:
          // folded, there is no keyboard to edit a config file with.
          Text {
            visible: !root.configLoaded
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.configMessage()
            color: Color.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Repeater {
            model: root.configLoaded ? root.configEntries : []

            delegate: Column {
              id: settingRow
              required property var modelData
              required property int index

              readonly property var entry: settingRow.modelData
              readonly property bool overridden: String(settingRow.entry.source || "") === root.overridesFile
              readonly property bool startsSection: settingRow.index === 0
                || root.configEntries[settingRow.index - 1].section !== settingRow.entry.section
              // Follows the drag so the readout is live, then the released value is what is
              // stored. Broken as a binding on the first move, which is the intent.
              property int shownNumber: Number(settingRow.entry.value === null
                ? settingRow.entry.slider_minimum : settingRow.entry.value)

              width: contentColumn.width
              spacing: Style.space(6)

              PanelSectionHeader {
                visible: settingRow.startsSection
                foreground: root.foreground
                fontFamily: root.fontFamily
                text: root.sectionLabel(settingRow.entry.section)
              }

              // A flag renders as one labelled switch row, which already carries its own name
              // and description, so the shared header above it would only repeat itself.
              RowLayout {
                visible: settingRow.entry.kind !== "flag"
                width: parent.width

                Text {
                  text: root.settingLabel(settingRow.entry)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
                Item { Layout.fillWidth: true }
                Text {
                  text: settingRow.entry.kind === "integer" && settingRow.entry.value !== null
                    ? String(settingRow.shownNumber)
                    : root.displayValue(settingRow.entry)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.bold: true
                  font.pixelSize: Style.font.body
                }
              }

              Text {
                visible: settingRow.entry.kind !== "flag"
                width: parent.width
                wrapMode: Text.WordWrap
                text: settingRow.entry.summary
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              PanelSlider {
                visible: settingRow.entry.kind === "integer"
                width: parent.width
                bar: root.bar
                step: 1
                integer: true
                // Widened to hold a value set by hand outside the span a slider sweeps, so a
                // control can show such a value rather than misreporting it at one end.
                minimum: Math.min(Number(settingRow.entry.slider_minimum), settingRow.shownNumber)
                maximum: Math.max(Number(settingRow.entry.slider_maximum), settingRow.shownNumber)
                value: settingRow.shownNumber
                onMoved: function(v) { settingRow.shownNumber = Math.round(v) }
                onReleased: function(v) {
                  root.storeSetting(settingRow.entry.name, Math.round(v))
                }
              }

              Toggle {
                visible: settingRow.entry.kind === "flag"
                width: parent.width
                label: root.settingLabel(settingRow.entry)
                description: settingRow.entry.summary
                checked: settingRow.entry.value === true
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.storeSetting(settingRow.entry.name,
                  settingRow.entry.value === true ? "false" : "true")
              }

              // Buttons rather than a dropdown: every choice stays one tap away, which is what
              // the folded posture is for, and there are few enough of them to show at once.
              Flow {
                visible: settingRow.entry.kind === "choice"
                width: parent.width
                spacing: Style.space(6)

                Repeater {
                  model: [""].concat(settingRow.entry.choices || [])

                  delegate: Button {
                    required property var modelData
                    text: modelData === "" ? "Unbound" : root.humanize(modelData)
                    active: String(settingRow.entry.value || "") === String(modelData)
                    bordered: true
                    enabled: root.backendAvailable
                    onClicked: root.storeSetting(settingRow.entry.name,
                      modelData === "" ? null : modelData)
                  }
                }
              }

              // Free text is deliberately read-only here. A field would summon the very
              // keyboard being configured, on top of this panel, and a Pango font description
              // is not something to type with a thumb.
              Text {
                visible: settingRow.entry.kind === "text"
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Change with  yoga-doctor config set " + settingRow.entry.name
                color: Color.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              RowLayout {
                width: parent.width

                Text {
                  text: root.sourceLabel(settingRow.entry)
                  color: Color.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
                Item { Layout.fillWidth: true }
                Button {
                  visible: settingRow.overridden
                  text: "Reset"
                  bordered: true
                  fontSize: Style.font.bodySmall
                  onClicked: root.storeSetting(settingRow.entry.name, null)
                }
              }
            }
          }

          Repeater {
            model: root.configIgnored

            delegate: Text {
              required property var modelData
              width: contentColumn.width
              wrapMode: Text.WordWrap
              text: "Ignored: " + String(modelData.source) + " sets " + String(modelData.name)
                + ", which is not a usable setting."
              color: Color.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          Text {
            visible: root.configLoaded
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Changes are written to " + root.overridesFile
              + " and take effect the next time the keyboard appears. Your hand-written"
              + " config.toml is never rewritten."
            color: Color.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }
      }
    }
  }
}
