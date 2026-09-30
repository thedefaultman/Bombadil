import QtQuick
import Bombadil as Kit

// "noticed 2": a small calm chip beside the pill, there only while something waits. It is
// something that can wait, so it has no dot, no colour and no motion, and the same hairline the
// pill has. Hovering it peeks the card; a click keeps the card up and opens the Noticed window.
// Right of the pill when there is room, else above whatever sits above the pill, at its right end.
// Zero-sized while hidden, so the bar's input mask takes no room for it.
Rectangle {
    id: chip
    objectName: "noticedChip"
    required property var loop        // a LoopState
    property string screenName: ""
    // The bar's own measures, in the window's pixels: the pill's right edge and height, the height
    // of everything above the window's bottom margin, and the window's width.
    property real pillRight: 0
    property real pillHeight: 52
    property real columnHeight: 52
    property real windowWidth: 0
    // Room other strips already use right of the pill, with the gap after them (the desk's, once it
    // lands): the chip sits past them, or above the pill when that leaves no room.
    property real taken: 0
    readonly property real edge: 12   // the column's margin from the window's bottom
    readonly property real gap: 12
    signal clicked()

    readonly property bool shown: loop.chipVisible
    readonly property bool beside: windowWidth - pillRight - taken - 2 * gap >= implicitWidth
    // Distance of the chip's bottom edge from the window's bottom edge.
    readonly property real bottomGap: beside ? edge + (pillHeight - implicitHeight) / 2 : edge + columnHeight + 8
    // How far above the window's bottom edge the chip reaches when it sits above the pill: the
    // layer must grow to hold it. (Beside the pill it is inside the bar already.)
    readonly property real reach: shown && !beside ? bottomGap + implicitHeight + 12 : 0

    implicitWidth: label.implicitWidth + 24
    implicitHeight: Kit.Theme.chipHeight
    width: shown ? implicitWidth : 0
    height: shown ? implicitHeight : 0
    visible: shown
    x: beside ? pillRight + taken + gap : Math.max(gap, pillRight - implicitWidth)
    anchors.bottom: parent.bottom
    anchors.bottomMargin: bottomGap
    radius: height / 2
    color: Kit.Theme.glassChip
    border.width: 1
    border.color: hover.hovered || (loop.cardOpen && loop.cardScreen === screenName) ? Kit.Theme.borderStrong : Kit.Theme.border

    Text {
        id: label
        objectName: "noticedChipText"
        anchors.centerIn: parent
        text: "noticed " + chip.loop.count
        color: Kit.Theme.muted
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.smallSize
        textFormat: Text.PlainText
    }

    HoverHandler {
        id: hover
        cursorShape: Qt.PointingHandCursor
        onHoveredChanged: chip.loop.hoverChip(chip.screenName, hovered)
    }
    TapHandler {
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: { chip.loop.chipClicked(chip.screenName); chip.clicked() }
    }
}
