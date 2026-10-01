import QtQml

QtObject {
  property var command: []
  property bool running: false
  property QtObject stdout
  property QtObject stderr
  signal exited(int code)
}
