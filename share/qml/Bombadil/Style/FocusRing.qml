import QtQuick
import Bombadil

// Keyboard focus outline, drawn just outside the item it fills (or inside with a positive inset).
Rectangle {
    property real baseRadius: Theme.radiusSmall
    property real inset: -3

    anchors.fill: parent
    anchors.margins: inset
    radius: Math.max(0, baseRadius - inset)
    color: "transparent"
    border.width: 2
    border.color: Theme.accent
}
