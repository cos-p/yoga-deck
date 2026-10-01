import QtQuick

Item {
  property var bar
  property real step: 1
  property bool integer: false
  property real minimum: 0
  property real maximum: 100
  property real value: 0
  signal moved(real value)
  signal released(real value)
}
