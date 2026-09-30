import QtQuick
import "DeskTheme.js" as T

// A widget's small face: a 28 px chip beside the pill, the same shape as the queue chips. A
// leading dot says who (orange the machine's turn, blue a session, white you), its ring that it
// is working, and the white mark says something waits for you behind a dot that has no room for
// a face of its own. The text elides at a fixed width the way the queue chips do.
Rectangle {
    id: strip
    objectName: "deskStrip"
    property string text: ""
    property string dot: ""            // a colour, or "" for no dot
    property bool ring: false
    property bool mark: false
    property color textColor: T.muted
    property bool outlined: false
    property real maxTextWidth: 220
    signal clicked()
    signal hoverChanged(bool over)

    readonly property bool hovered: hover.hovered
    readonly property color borderColor: outlined || hovered ? T.borderStrong : T.border

    implicitWidth: Math.min(label.implicitWidth, maxTextWidth) + 24 + (dot !== "" ? 14 : 0)
    implicitHeight: T.stripHeight
    width: implicitWidth
    height: implicitHeight
    radius: height / 2
    color: T.strip
    border.width: 1
    border.color: borderColor

    Rectangle {
        objectName: "deskStripDot"
        visible: strip.dot !== ""
        x: 16 - 4; y: strip.height / 2 - 4
        width: 8; height: 8; radius: 4
        color: strip.dot !== "" ? strip.dot : "transparent"

        // The white mark on the dot: something waits for you.
        Rectangle {
            objectName: "deskStripMark"
            visible: strip.mark
            x: 4 + 5 - 3; y: 4 - 5 - 3
            width: 6; height: 6; radius: 3
            color: T.you
        }
    }
    DeskCard.Ring {
        objectName: "deskStripRing"
        x: 16 - r; y: strip.height / 2 - r
        r: 7
        tone: strip.dot !== "" ? strip.dot : T.machine
        live: strip.dot !== "" && strip.ring
    }

    Text {
        id: label
        objectName: "deskStripText"
        x: strip.dot !== "" ? 26 : 12
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(implicitWidth, strip.maxTextWidth)
        text: strip.text
        color: strip.textColor
        font.pixelSize: 13
        textFormat: Text.PlainText
        elide: Text.ElideRight
        maximumLineCount: 1
    }

    HoverHandler {
        id: hover
        cursorShape: Qt.PointingHandCursor
        onHoveredChanged: strip.hoverChanged(hovered)
    }
    TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: strip.clicked() }
}
