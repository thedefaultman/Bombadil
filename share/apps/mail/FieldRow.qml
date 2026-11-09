import QtQuick
import QtQuick.Layouts
import Bombadil

// A label and a field of the reply box that wraps and grows (up to a few lines) rather than hiding the end of what
// is in it, with the service's sentence under it when it would not take what was typed, and a quiet count when the
// list is long.
ColumnLayout {
    id: root

    property string label: ""
    property string placeholder: ""
    property string error: ""
    property string note: ""
    property int maxLines: 4
    property alias text: input.text
    property alias readOnly: input.readOnly
    property alias input: input

    signal edited()
    signal accepted()

    spacing: 2

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        PlainText {
            Layout.preferredWidth: 58
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 8
            text: root.label
            color: Theme.muted
            font.pixelSize: Theme.smallSize
        }
        QuietArea {
            id: input
            Layout.fillWidth: true
            label: root.label
            placeholder: root.placeholder
            maxLines: root.maxLines
            lineBreaks: false
            onEdited: root.edited()
            onAccepted: root.accepted()
        }
    }
    PlainText {
        visible: root.error !== ""
        Layout.fillWidth: true
        Layout.leftMargin: 66
        text: root.error
        wrapMode: Text.Wrap
        font.pixelSize: Theme.captionSize
        color: Theme.badInk
    }
    PlainText {
        objectName: "fieldNote"
        visible: root.note !== ""
        Layout.fillWidth: true
        Layout.leftMargin: 66
        text: root.note
        font.pixelSize: Theme.captionSize
        color: Theme.muted
    }
}
