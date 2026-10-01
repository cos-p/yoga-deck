import QtQuick

Item {
  property var anchorItem
  property var owner
  property var bar
  property bool open: false
  property var focusTarget
  property real contentWidth: 0
  property real contentHeight: 0

  function fittedContentWidth(value) { return value }
  function fittedContentHeight(value, maximum) { return Math.min(value, maximum) }
}
