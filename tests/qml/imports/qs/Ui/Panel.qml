import QtQuick

Item {
  property string moduleName: ""
  property string ipcTarget: ""
  property bool manageIpc: false
  property var bar: null
  property var settings: ({})
  property bool opened: false

  function open() { opened = true }
  function close() { opened = false }
  function toggle() { opened = !opened }
  function switchPanel(direction) { }
}
