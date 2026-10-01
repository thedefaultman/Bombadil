import QtQuick
import QtQuick.Layouts
import Bombadil

// A label and a one-line field of the reply box, with the service's sentence under it when it would not take
// what was typed.
ColumnLayout {
    id: root

    property string label: ""
    property string placeholder: ""
    property string error: ""
    property alias text: input.text
    property alias input: input

    signal edited()

    spacing: 2

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        PlainText {
            Layout.preferredWidth: 58
            text: root.label
            color: Theme.muted
            font.pixelSize: Theme.smallSize
        }
        QuietInput {
            id: input
            Layout.fillWidth: true
            Layout.preferredHeight: 32
            label: root.label
            placeholder: root.placeholder
            onEdited: root.edited()
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
}
