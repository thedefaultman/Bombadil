import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// The right pane: the mail that is open and, under it, the reply box with Send pinned at the bottom (so the warnings
// and the button are always in view however long the mail or the draft is); once a draft is sent, one line with
// what was sent and a link to it on the web in place of the box.
Item {
    id: root

    property bool back: false           // one pane at a time: a way back to the list

    signal backRequested()

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: Theme.panel
        border.color: Theme.border
    }

    ColumnLayout {
        anchors { fill: parent; margins: 14 }
        spacing: 10

        QuietButton {
            visible: root.back
            text: "Back"
            icon: "chevron-left"
            compact: true
            flat: true
            onClicked: root.backRequested()
        }

        Item {
            id: area
            Layout.fillWidth: true
            Layout.fillHeight: true

            PlainText {
                objectName: "readerHint"
                visible: !backend.opened && !backend.draft
                anchors.centerIn: parent
                width: Math.min(parent.width - 24, 320)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: backend.view === "drafts" ? "Pick a draft to go on with it." : "Pick a mail to read it."
                color: Theme.faint
            }

            // The mail on top and, when a draft is open, the box under it: each scrolls by itself, and the mail takes
            // no more than a share of the pane so that the box is always in reach.
            ColumnLayout {
                anchors.fill: parent
                spacing: 8

                Flickable {
                    id: mailFlick
                    objectName: "mailFlick"
                    visible: !!backend.opened
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: backend.draft ? 80 : 0
                    // while a draft is open the mail has what the box does not need, and never less than a share
                    Layout.maximumHeight: backend.draft
                        ? Math.max(80, Math.min(contentHeight, Math.max(area.height * 0.28,
                                                                        area.height - replyBox.implicitHeight - 17)))
                        : 1e9
                    contentWidth: width
                    contentHeight: mailView.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    MailView {
                        id: mailView
                        width: mailFlick.width - 12
                    }
                }

                Rectangle {
                    visible: !!backend.opened && !!backend.draft
                    Layout.fillWidth: true
                    height: 1
                    color: Theme.border
                }

                Flickable {
                    id: boxFlick
                    objectName: "boxFlick"
                    visible: !!backend.draft
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 120
                    contentWidth: width
                    contentHeight: replyBox.height
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    ReplyBox {
                        id: replyBox
                        width: boxFlick.width - 12
                        height: Math.max(boxFlick.height, implicitHeight)
                    }
                }
            }
        }

        Rectangle {
            visible: !!backend.draft
            Layout.fillWidth: true
            height: 1
            color: Theme.border
        }
        SendBar {
            visible: !!backend.draft
            Layout.fillWidth: true
            Layout.leftMargin: 2
            Layout.rightMargin: 2
        }

        RowLayout {
            objectName: "receipt"
            visible: !!backend.receipt && !backend.draft
            Layout.fillWidth: true
            spacing: 8
            Icon { name: "check"; size: 16; color: Theme.good }
            PlainText {
                objectName: "receiptLine"
                Layout.fillWidth: true
                text: backend.receipt ? backend.receipt.line : ""
                elide: Text.ElideRight
            }
            LinkButton {
                objectName: "receiptLink"
                visible: !!backend.receipt && backend.receipt.webUrl !== ""
                text: backend.receipt && backend.receipt.webName ? "Open in " + backend.receipt.webName : "Open on the web"
                onClicked: backend.openWeb(backend.receipt.webUrl)
            }
            IconButton {
                icon: "x"
                size: 26
                tooltip: "Dismiss"
                onClicked: backend.dismissReceipt()
            }
        }
    }
}
