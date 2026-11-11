import QtQuick
import QtQuick.Layouts
import Bombadil

// What a tip with no button shows in its place: the keys or the words that make sense in the middle of a turn
// (Esc, why, undo), drawn as marks. Nothing here presses. A mark is as wide as its word, and when the marks
// together are wider than the room they are given (Replay sits beside them) they shrink and elide their words
// rather than push the window's column out.
RowLayout {
    id: root

    property var marks: []

    spacing: Theme.gapSmall

    Repeater {
        model: root.marks
        delegate: Rectangle {
            id: mark
            required property string modelData
            readonly property real natural: label.implicitWidth + 24
            Layout.preferredHeight: 28
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.preferredWidth: natural
            Layout.maximumWidth: natural
            radius: Theme.radiusSmall
            color: Theme.raised
            border.width: 1
            border.color: Theme.borderStrong
            Text {
                id: label
                anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                text: mark.modelData
                textFormat: Text.PlainText
                elide: Text.ElideRight
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.smallSize
                font.weight: Font.Medium
            }
        }
    }
}
