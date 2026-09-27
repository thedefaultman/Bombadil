pragma Singleton
import QtQuick

QtObject {
    readonly property color bg: "#101214"
    readonly property color panel: "#1a1d21"
    readonly property color fg: "#e6e8eb"
    readonly property color muted: "#8b939c"
    readonly property color accent: "#d97757"
    readonly property int radius: 12
    readonly property int pad: 16
    readonly property font font: Qt.font({ family: "Inter, sans-serif", pixelSize: 15 })
}
