import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// The mail that is open: who, to whom, when, what it says as plain text, its files, and what can be done with it.
// The text is a stranger's. It is drawn as text and nothing else: no markup is read and no link in it is live.
ColumnLayout {
    id: root

    readonly property var o: backend.opened || ({})
    readonly property var m: backend.mail
    // With a reply box open under it the mail takes less room: no buttons, no To and Cc.
    readonly property bool compact: !!backend.draft
    // A long list of recipients is two lines and a way to the rest, not half the window.
    property string allFor: ""
    readonly property bool allShown: allFor !== "" && allFor === (root.o.id || "")
    readonly property int listLines: allShown ? 1000 : 2

    spacing: root.compact ? 8 : Theme.gap

    PlainText {
        objectName: "mailSubject"
        Layout.fillWidth: true
        text: root.o.subject || ""
        wrapMode: Text.Wrap
        font.pixelSize: root.compact ? Theme.textSize + 1 : Theme.headingSize + 1
        font.weight: Font.DemiBold
    }

    GridLayout {
        Layout.fillWidth: true
        columns: 2
        columnSpacing: 12
        rowSpacing: 3

        PlainText { text: "From"; color: Theme.muted; font.pixelSize: Theme.smallSize; Layout.alignment: Qt.AlignTop }
        PlainText {
            objectName: "mailFrom"
            Layout.fillWidth: true
            text: root.o.from || (root.o.sender ? root.o.sender + (root.o.address ? " <" + root.o.address + ">" : "") : "")
            wrapMode: Text.Wrap
            font.pixelSize: Theme.smallSize
        }
        PlainText { visible: !!root.o.to && !root.compact; text: "To"; color: Theme.muted; font.pixelSize: Theme.smallSize; Layout.alignment: Qt.AlignTop }
        PlainText {
            id: mailTo
            objectName: "mailTo"
            visible: !!root.o.to && !root.compact
            Layout.fillWidth: true
            text: root.o.to || ""
            wrapMode: Text.Wrap
            maximumLineCount: root.listLines
            elide: Text.ElideRight
            font.pixelSize: Theme.smallSize
        }
        PlainText { visible: !!root.o.cc && !root.compact; text: "Cc"; color: Theme.muted; font.pixelSize: Theme.smallSize; Layout.alignment: Qt.AlignTop }
        PlainText {
            id: mailCc
            objectName: "mailCc"
            visible: !!root.o.cc && !root.compact
            Layout.fillWidth: true
            text: root.o.cc || ""
            wrapMode: Text.Wrap
            maximumLineCount: root.listLines
            elide: Text.ElideRight
            font.pixelSize: Theme.smallSize
        }
        PlainText { text: "Time"; color: Theme.muted; font.pixelSize: Theme.smallSize; Layout.alignment: Qt.AlignVCenter }
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            PlainText {
                objectName: "mailTime"
                Layout.fillWidth: true
                text: root.o.full_when || root.o.when || ""
                font.pixelSize: Theme.smallSize
            }
            LinkButton {
                objectName: "webLink"
                visible: !!root.m && !!root.m.webUrl
                text: root.m && root.m.webName ? "Open in " + root.m.webName : "Open on the web"
                onClicked: backend.openWeb(root.m.webUrl)
            }
        }
    }

    LinkButton {
        objectName: "allRecipients"
        visible: !root.compact && (root.allShown || mailTo.truncated || mailCc.truncated)
        text: root.allShown ? "Fewer recipients" : "All recipients"
        icon: ""
        onClicked: root.allFor = root.allShown ? "" : (root.o.id || "")
    }

    Flow {
        id: actions
        objectName: "mailActions"
        visible: !root.compact
        Layout.fillWidth: true
        spacing: 6

        QuietButton {
            objectName: "replyButton"
            text: "Reply"
            icon: "reply"
            onClicked: backend.reply("reply")
        }
        QuietButton {
            objectName: "replyAllButton"
            text: "Reply all"
            icon: "reply-all"
            onClicked: backend.reply("reply_all")
        }
        QuietButton {
            objectName: "forwardButton"
            text: "Forward"
            icon: "forward"
            onClicked: backend.reply("forward")
        }
        // the four that are only a picture stay together when the line wraps
        Row {
            spacing: 6
            QuietButton {
                objectName: "archiveButton"
                icon: "archive"
                tooltip: "Archive (A)"
                onClicked: backend.archive()
            }
            QuietButton {
                objectName: "deleteButton"
                icon: "trash"
                tooltip: "Delete (#)"
                onClicked: backend.trash()
            }
            QuietButton {
                objectName: "flagButton"
                icon: "flag"
                checked: !!root.o.flagged
                tooltip: root.o.flagged ? "Take the flag off" : "Flag"
                onClicked: backend.setFlagged(!root.o.flagged)
            }
            QuietButton {
                objectName: "unreadButton"
                icon: root.o.unread ? "mail-open" : "mail"
                tooltip: root.o.unread ? "Mark as read" : "Mark as unread"
                onClicked: backend.setUnread(!root.o.unread)
            }
        }
        QuietButton {
            objectName: "needsReplyButton"
            text: "Needs a reply"
            checked: !!root.o.needsReply
            onClicked: backend.markReply(!root.o.needsReply)
        }
    }

    RowLayout {
        visible: !!root.o.needsReply && !!root.o.why && !root.compact
        Layout.fillWidth: true
        spacing: 8
        Icon { Layout.alignment: Qt.AlignTop; Layout.topMargin: 1; name: "reply"; size: 14; color: Theme.muted }
        PlainText {
            objectName: "mailWhy"
            Layout.fillWidth: true
            text: root.o.why || ""
            wrapMode: Text.Wrap
            font.pixelSize: Theme.smallSize
            color: Theme.muted
        }
    }

    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

    PlainText {
        visible: backend.mailState === "loading"
        text: "Opening"
        color: Theme.muted
    }
    PlainText {
        objectName: "mailError"
        visible: backend.mailState === "error"
        Layout.fillWidth: true
        text: backend.mailError
        wrapMode: Text.Wrap
        color: Theme.muted
    }

    MailText {
        objectName: "mailBody"
        visible: backend.mailState === "ready"
        Layout.fillWidth: true
        text: root.m ? (root.m.text !== "" ? root.m.text : "This mail has no text.") : ""
        color: root.m && root.m.text === "" ? Theme.muted : Theme.fg
        // The kit's selection and link colours are the accent; this one is the ink's own.
        font.pixelSize: Theme.textSize + 1
    }

    PlainText {
        objectName: "mailNote"
        visible: !!root.m && (root.m.htmlOnly || root.m.truncated)
        Layout.fillWidth: true
        text: root.m && root.m.truncated ? "This is the start of a long mail. The rest is on the web."
            : "This mail came as a web page. Only its words are shown."
        wrapMode: Text.Wrap
        font.pixelSize: Theme.smallSize
        color: Theme.muted
    }

    ColumnLayout {
        objectName: "attachments"
        visible: !!root.m && root.m.attachments.length > 0
        Layout.fillWidth: true
        spacing: 4
        Repeater {
            model: root.m ? root.m.attachments : []
            delegate: RowLayout {
                id: att
                required property var modelData
                visible: !att.modelData.inline
                Layout.fillWidth: true
                spacing: 8
                Icon { name: "paperclip"; size: 15; color: Theme.muted }
                PlainText {
                    Layout.fillWidth: true
                    text: att.modelData.name
                    elide: Text.ElideMiddle
                    font.pixelSize: Theme.smallSize
                }
                PlainText {
                    text: att.modelData.size
                    font.pixelSize: Theme.captionSize
                    color: Theme.muted
                }
                QuietButton {
                    objectName: "saveAttachment"
                    text: "Save"
                    icon: "download"
                    compact: true
                    onClicked: backend.saveAttachment(att.modelData.part)
                }
            }
        }
    }
}
