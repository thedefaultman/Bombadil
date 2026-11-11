import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The body of a message card: the last few messages of the thread (who, what, when; the unread ones carry the kit's
// dot), the box to reply in, what to look at before pressing, and the button. The head (who it is about, where it
// came from, the ×) and the one sentence under it are the card host's.
//
// Everything in the thread is another person's words: drawn as plain text, never read as markup, and cut by
// cards.py before it gets here. The box and the button are Draft's and DraftBar's (docs/CONNECT.md, "Cards").
// A card whose reply is a copy (a notification) has the same box and a button that copies and opens.
FocusScope {
    id: face
    objectName: "messageFace"

    required property var pill
    property var card: null
    property int maxHeight: 440

    readonly property var reply: draft.reply
    readonly property bool hasBox: !!reply
    readonly property string via: card && card.via ? String(card.via) : ""

    // A thread that has not changed does not rebuild its rows (a draft that changes sends the card again, and a
    // thread that is being read would jump back to its end).
    property var _thread: []
    readonly property string _sig: JSON.stringify((card && Array.isArray(card.thread) ? card.thread : [])
        .map(m => [m.ref, m.from, m.text, m.when, m.mine, m.unread]))
    function _refresh() {
        _thread = card && Array.isArray(card.thread) ? card.thread : []
        Qt.callLater(scroll.toEnd)
    }
    on_SigChanged: _refresh()
    Component.onCompleted: _refresh()

    function focusBox() { if (hasBox) box.focusEnd() }

    implicitHeight: column.implicitHeight
    Keys.onEscapePressed: face.pill.dismissCard()

    Draft {
        id: draft
        pill: face.pill
        card: face.card
        current: box.text
        boxFocus: box.activeFocus
        onWrite: content => box.setText(content)
    }

    ColumnLayout {
        id: column
        anchors { left: parent.left; right: parent.right; top: parent.top }
        spacing: Kit.Theme.gap

        CardScroll {
            id: scroll
            objectName: "thread"
            Layout.fillWidth: true
            visible: face._thread.length > 0
            spacing: Kit.Theme.gap
            // What the box and the button need stays in view; the thread takes what is left.
            maxHeight: Math.max(Kit.Theme.rowHeight * 2,
                                face.maxHeight - (face.hasBox ? box.implicitHeight + bar.implicitHeight + 2 * Kit.Theme.gap : 0))

            Repeater {
                model: face._thread
                RowLayout {
                    id: row
                    required property var modelData
                    objectName: "threadRow"
                    Layout.fillWidth: true
                    spacing: Kit.Theme.gapSmall + 2
                    Accessible.role: Accessible.StaticText
                    Accessible.name: (modelData.mine ? "You" : modelData.from) + ", " + modelData.when
                                     + (modelData.unread ? ", unread" : "") + ": " + modelData.text

                    // The kit's marker for what has not been read: the same dot the mail window uses.
                    Item {
                        Layout.alignment: Qt.AlignTop
                        Layout.preferredWidth: Kit.Theme.gapSmall + 2
                        Layout.preferredHeight: Kit.Theme.textSize + 4
                        Rectangle {
                            objectName: "unreadDot"
                            visible: row.modelData.unread === true
                            anchors.centerIn: parent
                            width: Kit.Theme.gapSmall + 2; height: width; radius: width / 2
                            color: Kit.Theme.fg
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Kit.Theme.gapSmall
                            Text {
                                objectName: "threadFrom"
                                Layout.fillWidth: true
                                text: row.modelData.mine ? "You" : row.modelData.from
                                color: row.modelData.mine ? Kit.Theme.muted : Kit.Theme.fg
                                font.family: Kit.Theme.fontFamily
                                font.pixelSize: Kit.Theme.smallSize
                                font.weight: row.modelData.unread ? Font.DemiBold : Font.Medium
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                            }
                            Text {
                                objectName: "threadWhen"
                                text: row.modelData.when
                                color: Kit.Theme.muted
                                font.family: Kit.Theme.fontFamily
                                font.pixelSize: Kit.Theme.captionSize
                                textFormat: Text.PlainText
                            }
                        }
                        Text {
                            objectName: "threadText"
                            Layout.fillWidth: true
                            text: row.modelData.text
                            color: row.modelData.unread ? Kit.Theme.fg : Kit.Theme.muted
                            font.family: Kit.Theme.fontFamily
                            font.pixelSize: Kit.Theme.textSize
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }

        CardBox {
            id: box
            objectName: "replyBox"
            Layout.fillWidth: true
            visible: face.hasBox
            label: "Reply"
            placeholder: draft.copy ? "Your words, for " + (face.reply ? face.reply.where : "")
                                    : (face.reply && face.reply.where ? "Reply in " + face.reply.where : "Reply")
            readOnly: !draft.editable
            onEdited: draft.changed()
            onEscaped: face.pill.dismissCard()
            onTaken: face.pill.wantKeys()
        }

        DraftBar {
            id: bar
            Layout.fillWidth: true
            visible: face.hasBox
            draft: draft
            pill: face.pill
            card: face.card
            extra: [
                CardButton {
                    objectName: "openButton"
                    visible: !draft.copy && !!(face.card && face.card.open) && !bar.receiptOpens
                    compact: true
                    label: "Open in " + face.via
                    onActivated: face.pill.cardAction(face.card.id, "open")
                }
            ]
        }

        // A thread with nothing to answer: only its way out.
        CardButton {
            visible: !face.hasBox && !!(face.card && face.card.open)
            compact: true
            label: "Open in " + face.via
            onActivated: face.pill.cardAction(face.card.id, "open")
        }
    }
}
