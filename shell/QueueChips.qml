import QtQuick
import QtQuick.Layouts

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
            implicitHeight: 28
            radius: 14
            color: "#d91a1d21"
            border.width: 1
            border.color: "#2a2f36"

            RowLayout {
                id: row
                anchors.centerIn: parent
                spacing: 8
                Text {
                    text: "next"
                    color: "#6b737c"
                    font.pixelSize: 12
                }
                Text {
                    Layout.maximumWidth: 260
                    text: chip.modelData.prompt
                    color: "#a9b0b8"
                    font.pixelSize: 13
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                }
                Text {
                    text: "×"
                    color: remove.hovered ? "#e6e8eb" : "#8b939c"
                    font.pixelSize: 15
                    HoverHandler { id: remove; cursorShape: Qt.PointingHandCursor; margin: 6 }
                    TapHandler { margin: 6; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: chips.pill.unqueue(chip.modelData.turn) }
                }
            }
        }
    }
}
