import QtQuick
import Bombadil as Kit

// A small text button for the line above the pill (Undo, Details) and for the Noticed card:
// `primary` is the one thing a row asks (the colours of the Stop dot), `quiet` is bare text.
Rectangle {
    id: button
    property string label: ""
    property bool primary: false
    property bool quiet: false
    signal clicked()

    implicitWidth: text.implicitWidth + (quiet ? 12 : 20)
    implicitHeight: quiet ? 22 : 24
    radius: Kit.Theme.radiusLineButton
    color: quiet ? "transparent"
         : primary ? (tap.pressed ? Kit.Theme.accentPressed : hover.hovered ? Kit.Theme.accentHover : Kit.Theme.accent)
         : tap.pressed ? Kit.Theme.borderStrong : hover.hovered ? Kit.Theme.border : Kit.Theme.raised
    border.width: quiet ? 0 : 1
    border.color: Kit.Theme.borderStrong
    opacity: enabled ? 1 : 0.5

    Text {
        id: text
        font.family: Kit.Theme.fontFamily
        anchors.centerIn: parent
        text: button.label
        color: button.primary ? Kit.Theme.accentFg : button.quiet ? (hover.hovered ? Kit.Theme.fg : Kit.Theme.muted) : Kit.Theme.fg
        font.pixelSize: Kit.Theme.captionSize
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: button.clicked() }
}
