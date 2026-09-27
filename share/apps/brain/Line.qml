import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil
import "who.js" as Who

// One line in a slot: a dot for who, the thing's name, and why it is here with when.
// "builder on Bombadil, turn 12" / "“drop the old cleanup”, 13:02". Clicking it brings the
// thing to the middle.
Rectangle {
    id: row
    property var line: ({})
    readonly property bool amber: line.tone === "amber"
    readonly property bool clickable: !!line.ref
    signal picked(string ref)

    Layout.fillWidth: true
    implicitHeight: col.implicitHeight + 12
    radius: Theme.radiusSmall
    color: mouse.containsMouse && clickable ? Theme.raised : (amber ? Theme.alpha(Theme.warn, 0.08) : "transparent")
    border.width: amber ? 1 : 0
    border.color: Theme.alpha(Theme.warn, 0.35)

    Rectangle {
        id: dot
        width: 8; height: 8; radius: 4
        x: 8; y: 6 + (title.implicitHeight - height) / 2
        color: row.amber ? Theme.warn : Who.dot(row.line.actor, Theme)
    }

    ColumnLayout {
        id: col
        x: 24; y: 6
        width: row.width - 32
        spacing: 1
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gapSmall
            Text {
                id: title
                Layout.fillWidth: true
                text: row.line.title || ""
                color: Theme.fg
                font.pixelSize: Theme.textSize
                font.family: Theme.fontFamily
                elide: Text.ElideMiddle
            }
            Text {
                text: row.line.when || ""
                color: Theme.faint
                font.pixelSize: Theme.captionSize
                font.family: Theme.fontFamily
                visible: text !== ""
            }
        }
        Text {
            Layout.fillWidth: true
            text: row.line.why || ""
            visible: text !== ""
            color: row.amber ? Theme.warn : Theme.muted
            font.pixelSize: Theme.captionSize
            font.family: Theme.fontFamily
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        enabled: row.clickable
        cursorShape: row.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: row.picked(row.line.ref)
    }
}
