import QtQuick
import QtQuick.Layouts
import Bombadil

// Before there is an account to read: one field for the person's email address. When the service has found where to
// sign in it says so in its own words, and Thunderbird's window (which does the signing in) is brought up on request
// and put away again with Done.
Item {
    id: root

    property bool inPlace: false        // opened from "Add an account", so there is a way back

    signal closed()

    // The card is as big as what is in it, in the middle of the window.
    Rectangle {
        anchors.centerIn: parent
        width: Math.min(parent.width, 520)
        height: Math.min(parent.height, content.implicitHeight + 64)
        radius: Theme.radius
        color: Theme.panel
        border.color: Theme.border
    }

    QuietButton {
        visible: root.inPlace
        anchors { left: parent.left; top: parent.top; margins: 14 }
        text: "Back"
        icon: "chevron-left"
        compact: true
        flat: true
        onClicked: root.closed()
    }

    ColumnLayout {
        id: content
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 440)
        spacing: 12

        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 48
            implicitHeight: 48
            radius: 24
            color: Theme.raised
            border.color: Theme.border
            Icon {
                anchors.centerIn: parent
                name: "mail"
                size: 22
                color: Theme.muted
            }
        }

        // an account is signing in
        ColumnLayout {
            visible: !!backend.signingIn
            Layout.fillWidth: true
            spacing: 10
            PlainText {
                objectName: "signinTitle"
                Layout.fillWidth: true
                text: backend.signingIn ? "Signing in to " + backend.signingIn.email : ""
                wrapMode: Text.Wrap
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
            }
            PlainText {
                objectName: "signinNote"
                Layout.fillWidth: true
                text: backend.signingIn && backend.signingIn.note !== "" ? backend.signingIn.note
                    : "Finish signing in in the browser window that opened."
                wrapMode: Text.Wrap
                color: Theme.muted
            }
            RowLayout {
                spacing: 8
                QuietButton {
                    objectName: "showEngine"
                    visible: !backend.staged
                    text: "Show Thunderbird's window"
                    onClicked: backend.showEngine()
                }
                QuietButton {
                    objectName: "hideEngine"
                    visible: backend.staged
                    text: "Done"
                    flat: true
                    onClicked: backend.hideEngine()
                }
            }
            PlainText {
                visible: backend.addError !== ""
                Layout.fillWidth: true
                text: backend.addError
                wrapMode: Text.Wrap
                font.pixelSize: Theme.smallSize
                color: Theme.badInk
            }
        }

        // no account yet
        ColumnLayout {
            visible: !backend.signingIn
            Layout.fillWidth: true
            spacing: 10
            PlainText {
                Layout.fillWidth: true
                text: "Mail for all your accounts, in one list"
                wrapMode: Text.Wrap
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
            }
            PlainText {
                Layout.fillWidth: true
                text: "Your mail stays with your provider. Nothing is sent until you press Send."
                wrapMode: Text.Wrap
                color: Theme.muted
            }
            PlainText {
                Layout.topMargin: 6
                text: "Your email address"
                font.pixelSize: Theme.smallSize
                color: Theme.muted
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                QuietInput {
                    id: address
                    objectName: "addressField"
                    Layout.fillWidth: true
                    label: "Your email address"
                    enabled: !backend.adding
                    onAccepted: root.add()
                }
                QuietButton {
                    objectName: "addButton"
                    text: backend.adding ? "Adding" : "Add"
                    enabled: !backend.adding && address.text.trim() !== ""
                    onClicked: root.add()
                }
            }
            PlainText {
                objectName: "addError"
                visible: backend.addError !== ""
                Layout.fillWidth: true
                text: backend.addError
                wrapMode: Text.Wrap
                font.pixelSize: Theme.smallSize
                color: Theme.badInk
            }
        }
    }

    function add() {
        if (address.text.trim() !== "" && !backend.adding)
            backend.addAccount(address.text.trim())
    }
    onVisibleChanged: if (visible && !backend.signingIn) address.forceActiveFocus()
}
