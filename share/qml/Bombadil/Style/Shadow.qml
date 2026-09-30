import QtQuick
import Bombadil

// A soft drop shadow for popups, made of two translucent rounded rectangles (no shaders).
Item {
    property real radius: Theme.radius

    anchors.fill: parent
    z: -1

    Rectangle {
        anchors.fill: parent
        anchors.margins: -6
        anchors.topMargin: -2
        anchors.bottomMargin: -12
        radius: parent.radius + 6
        color: Theme.alpha(Theme.sunken, 0.16)
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: -1
        anchors.bottomMargin: -4
        radius: parent.radius + 1
        color: Theme.alpha(Theme.sunken, 0.3)
    }
}
