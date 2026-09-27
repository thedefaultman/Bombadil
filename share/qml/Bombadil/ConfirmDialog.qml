import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// "Are you sure?" in one line: set title/text/confirmText, handle onConfirmed, call open().
// With `danger` the confirm button is red and Cancel has the keyboard focus.
Dialog {
    id: root

    property string text
    property string confirmText: "Confirm"
    property string cancelText: "Cancel"
    property bool danger: false

    signal confirmed()

    modal: true
    width: Math.min(420, (parent ? parent.width : 420) - 2 * Theme.pad)

    contentItem: Text {
        text: root.text
        visible: text !== ""
        color: Theme.muted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.textSize
        wrapMode: Text.Wrap
    }

    // A plain row rather than a DialogButtonBox, which would un-highlight a custom button.
    footer: Item {
        implicitWidth: buttons.implicitWidth + 40
        implicitHeight: buttons.implicitHeight + 20
        RowLayout {
            id: buttons
            anchors { right: parent.right; rightMargin: 20; top: parent.top }
            spacing: 8
            Button {
                id: cancelButton
                text: root.cancelText
                onClicked: root.reject()
            }
            Button {
                id: confirmButton
                text: root.confirmText
                highlighted: true
                danger: root.danger
                onClicked: root.accept()
            }
        }
    }

    onOpened: (danger ? cancelButton : confirmButton).forceActiveFocus()
    onAccepted: confirmed()
}
