import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// Prompts typed while a turn runs wait here as grey "next" chips; the x drops one.
RowLayout {
    id: chips
    required property var pill

    visible: pill.queue.length > 0
    spacing: 6

    Repeater {
        model: chips.pill.queue
        Rectangle {
            id: chip
            required property var modelData
            objectName: "queuedChip"
            implicitWidth: row.implicitWidth + 24
            implicitHeight: Kit.Theme.chipHeight
            radius: implicitHeight / 2
            color: Kit.Theme.glassChip
            border.width: 1
            border.color: Kit.Theme.border

            RowLayout {
                id: row
                anchors.centerIn: parent
                spacing: 8
                Text {
                    font.family: Kit.Theme.fontFamily
                    text: "next"
                    color: Kit.Theme.faint
                    font.pixelSize: Kit.Theme.captionSize
                }
                Text {
                    font.family: Kit.Theme.fontFamily
                    Layout.maximumWidth: 260
                    text: chip.modelData.prompt
                    color: Kit.Theme.muted
                    font.pixelSize: Kit.Theme.smallSize
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                }
                Text {
                    font.family: Kit.Theme.fontFamily
                    text: "×"
                    color: remove.hovered ? Kit.Theme.fg : Kit.Theme.muted
                    font.pixelSize: 15
                    HoverHandler { id: remove; cursorShape: Qt.PointingHandCursor; margin: 6 }
                    TapHandler { margin: 6; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: chips.pill.unqueue(chip.modelData.turn) }
                }
            }
        }
    }
}
