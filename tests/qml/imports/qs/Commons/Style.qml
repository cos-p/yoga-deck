pragma Singleton
import QtQml

QtObject {
  readonly property int cornerRadius: 6
  readonly property var font: ({
    "family": "Sans",
    "display": 24,
    "title": 18,
    "body": 14,
    "bodySmall": 12
  })

  function space(value) { return value }
}
