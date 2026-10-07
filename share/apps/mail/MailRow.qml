import QtQuick
import QtQuick.Layouts
import Bombadil

// One mail in the list: who it is from, the subject, when, a dot while it is unread, a clip when it has files, and,
// in Needs a reply, the one line of why. All of it is a stranger's words, drawn as plain text.
Item {
    id: root

    required property var info          // a row from backend.messages
    property bool open: false           // the mail on the right
    property bool current: false        // where the keyboard is in the list
    property bool showWhy: false

    signal clicked()

    width: ListView.view ? ListView.view.width : 320
    implicitHeight: col.implicitHeight + 20
    height: implicitHeight
    Accessible.role: Accessible.ListItem
    Accessible.name: info.sender + ", " + info.subject + (info.unread ? ", unread" : "")

    HoverHandler { id: hover }
    TapHandler { onTapped: root.clicked() }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: root.open ? Theme.raised : hover.hovered ? Theme.alpha(Theme.raised, 0.6) : "transparent"
        border.width: root.current && !root.open ? 1.5 : 0
        border.color: Theme.borderActive
    }
    Rectangle {
        visible: root.open
        anchors { left: parent.left; verticalCenter: parent.verticalCenter }
        width: 2
        height: parent.height - 24
        radius: 1
        color: Theme.fg
    }

    RowLayout {
        anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 8; rightMargin: 12; topMargin: 10 }
        spacing: 6

        Item {
            Layout.alignment: Qt.AlignTop
            Layout.preferredWidth: 12
            Layout.preferredHeight: 20
            Rectangle {
                objectName: "unreadDot"
                visible: root.info.unread
                anchors.centerIn: parent
                width: 8
                height: 8
                radius: 4
                color: Theme.fg
            }
        }

        ColumnLayout {
            id: col
            Layout.fillWidth: true
            spacing: 2

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                PlainText {
                    Layout.fillWidth: true
                    text: root.info.sender
                    elide: Text.ElideRight
                    font.weight: root.info.unread ? Font.DemiBold : Font.Normal
                }
                Icon {
                    visible: root.info.flagged
                    name: "flag"
                    size: 13
                    color: Theme.fg
                }
                PlainText {
                    text: root.info.when
                    font.pixelSize: Theme.captionSize
                    color: Theme.muted
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                PlainText {
                    Layout.fillWidth: true
                    text: root.info.subject
                    elide: Text.ElideRight
                    font.pixelSize: Theme.smallSize
                    color: root.info.unread ? Theme.fg : Theme.muted
                }
                Icon {
                    objectName: "clip"
                    visible: root.info.attachments
                    name: "paperclip"
                    size: 13
                    color: Theme.muted
                }
            }
            PlainText {
                visible: root.showWhy && !!root.info.why
                Layout.fillWidth: true
                text: root.info.why || ""
                elide: Text.ElideRight
                font.pixelSize: Theme.captionSize
                color: Theme.muted
            }
        }
    }
}
