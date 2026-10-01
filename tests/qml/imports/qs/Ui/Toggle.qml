import QtQuick

Item {
  property string label: ""
  property string description: ""
  property bool checked: false
  property color foreground: "white"
  property string fontFamily: "Sans"
  signal clicked()
}
