import QtQuick
import Bombadil as Kit

// A small text button for the line above the pill (Undo, Details).
Rectangle {
    id: button
    property string label: ""
    signal clicked()

    implicitWidth: text.implicitWidth + 20
    implicitHeight: 24
    radius: Kit.Theme.radiusLineButton
    color: tap.pressed ? Kit.Theme.borderStrong : hover.hovered ? Kit.Theme.border : Kit.Theme.raised
    border.width: 1
    border.color: Kit.Theme.borderStrong

    Text {
        id: text
        font.family: Kit.Theme.fontFamily
        anchors.centerIn: parent
        text: button.label
        color: Kit.Theme.fg
        font.pixelSize: Kit.Theme.captionSize
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: button.clicked() }
}
