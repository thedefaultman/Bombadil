import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// The left column: All inboxes, each account under it, then Needs a reply and Drafts, with their counts.
FocusScope {
    id: root

    signal addAccount()

    implicitWidth: 196

    Flickable {
        anchors.fill: parent
        contentHeight: col.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        ColumnLayout {
            id: col
            width: parent.width
            spacing: 0

            Repeater {
                model: backend.sidebar
                delegate: ColumnLayout {
                    id: item
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: 0
                    Rectangle {
                        // a hairline between the accounts and the two views under them
                        visible: item.modelData.id === "needs_reply"
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        Layout.bottomMargin: 6
                        height: 1
                        color: Theme.border
                    }
                    SideRow {
                        Layout.fillWidth: true
                        info: item.modelData
                        selected: backend.view === item.modelData.id
                        onPicked: backend.setView(item.modelData.id)
                        onWebRequested: url => backend.openWeb(url)
                    }
                }
            }

            Item { Layout.preferredHeight: 8 }
            QuietButton {
                objectName: "addAccount"
                Layout.leftMargin: 12
                text: "Add an account"
                icon: "plus"
                flat: true
                compact: true
                onClicked: root.addAccount()
            }
        }
    }
}
