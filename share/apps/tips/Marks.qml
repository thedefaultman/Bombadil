import QtQuick
import Bombadil

// What a tip with no button shows in its place: the keys or the words that make sense in the middle of a turn
// (Esc, why, undo), drawn as marks. Nothing here presses.
Row {
    id: root

    property var marks: []

    spacing: Theme.gapSmall

    Repeater {
        model: root.marks
        delegate: Rectangle {
            id: mark
            required property string modelData
            height: 28
            width: label.implicitWidth + 24
            radius: Theme.radiusSmall
            color: Theme.raised
            border.width: 1
            border.color: Theme.borderStrong
            Text {
                id: label
                anchors.centerIn: parent
                text: mark.modelData
                textFormat: Text.PlainText
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.smallSize
                font.weight: Font.Medium
            }
        }
    }
}
