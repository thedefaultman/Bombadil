import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// Under the box of a card: what to look at before pressing (the warnings, in plain sentences), what became of the
// last press, and the button.
//
// The button is the person's. While it can be pressed (Draft.live: agentd says the draft is ready, the box holds
// exactly it, and it has been so for a moment) it has the orange ring and the label "Yours: Send" beside it; when
// it cannot it is dimmed and nothing explains why. After a press whose outcome is not known it says so, in the
// card's own words, and comes back only when the person has ticked that they looked where it would have gone.
// A notification's card has a different button, "Copy and open", which is no send: no ring and no "Yours".
//
// `extra` is where a face puts the buttons beside it (Open in Slack).
ColumnLayout {
    id: bar
    objectName: "draftBar"

    required property var draft
    required property var pill
    property var card: null
    property alias extra: extraRow.data

    readonly property var reply: draft.reply
    readonly property string via: card && card.via ? String(card.via) : ""
    readonly property var lines: card && card.lines ? card.lines : ({})
    readonly property string button: reply && reply.button ? String(reply.button) : ""
    readonly property string phase: draft.phase
    readonly property var receipt: reply && reply.receipt && typeof reply.receipt === "object" ? reply.receipt : null
    // The receipt of a press that went has a page of its own to open (the sent message), and then the card's own
    // "Open in Slack" would be a second button of the same name.
    readonly property bool receiptOpens: phase === "sent" && !!(receipt && receipt.web && receipt.web.url)
    readonly property var warnings: reply && reply.warnings && reply.warnings.length !== undefined
                                    ? Array.prototype.slice.call(reply.warnings) : []
    readonly property string pressLine: pill.pressLine !== "" && pill.pressLineFor === draft.proposalId ? pill.pressLine : ""
    property bool copied: false      // a notification's words were just taken: not again for a moment

    spacing: Kit.Theme.gapSmall

    function activate() {
        if (draft.copy) {
            if (!draft.hasText || copied) return
            copied = true
            copyTimer.restart()
            pill.copyOpen(card.id, draft.current)
            return
        }
        draft.press()
    }

    Timer { id: copyTimer; interval: 1500; onTriggered: bar.copied = false }

    // Warnings: a plain sentence each. Amber, since they are the system's words and not the sender's.
    Rectangle {
        objectName: "warnings"
        visible: bar.warnings.length > 0
        Layout.fillWidth: true
        implicitHeight: warnColumn.implicitHeight + 2 * Kit.Theme.gapSmall
        radius: Kit.Theme.radiusSmall
        color: Kit.Theme.alpha(Kit.Theme.warn, 0.12)
        border.width: 1
        border.color: Kit.Theme.alpha(Kit.Theme.warn, 0.5)
        ColumnLayout {
            id: warnColumn
            anchors { fill: parent; margins: Kit.Theme.gapSmall }
            spacing: Kit.Theme.gapSmall
            Repeater {
                model: bar.warnings
                RowLayout {
                    id: warning
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: Kit.Theme.gapSmall + 2
                    Kit.Icon { Layout.alignment: Qt.AlignTop; name: "alert-triangle"; size: Kit.Theme.textSize + 2; color: Kit.Theme.warn }
                    Text {
                        objectName: "warningText"
                        Layout.fillWidth: true
                        text: warning.modelData.text
                        color: Kit.Theme.warnInk
                        font.family: Kit.Theme.fontFamily
                        font.pixelSize: Kit.Theme.smallSize
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }

    // What became of the press: sending, the receipt, or that it cannot be told. Nothing while the draft is open.
    Text {
        objectName: "sendingLine"
        visible: bar.phase === "sending"
        Layout.fillWidth: true
        text: bar.lines.sending || "Sending…"
        color: Kit.Theme.muted
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.smallSize
        textFormat: Text.PlainText
    }
    RowLayout {
        visible: bar.phase === "sent"
        Layout.fillWidth: true
        spacing: Kit.Theme.gap
        Kit.Icon { Layout.alignment: Qt.AlignVCenter; name: "check"; size: Kit.Theme.textSize + 2; color: Kit.Theme.good }
        Text {
            objectName: "receiptLine"
            Layout.fillWidth: true
            text: bar.receipt && bar.receipt.line ? bar.receipt.line : "Sent."
            color: Kit.Theme.fg
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.smallSize
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
        }
        CardButton {
            objectName: "receiptLink"
            visible: !!(bar.receipt && bar.receipt.web && bar.receipt.web.url)
            compact: true
            label: "Open in " + (bar.receipt && bar.receipt.web ? bar.receipt.web.name : "")
            onActivated: bar.pill.openThing({ kind: "url", value: bar.receipt.web.url })
        }
    }
    Text {
        objectName: "unknownLine"
        visible: bar.phase === "unknown"
        Layout.fillWidth: true
        text: bar.lines.unknown || ""
        color: Kit.Theme.warnInk
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.smallSize
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    // The person's own word that they looked, before the button comes back.
    FocusScope {
        id: tick
        objectName: "lookedTick"
        visible: bar.phase === "unknown"
        Layout.preferredHeight: tickRow.implicitHeight
        Layout.preferredWidth: tickRow.implicitWidth
        activeFocusOnTab: true
        Accessible.role: Accessible.CheckBox
        Accessible.name: "I looked in " + bar.via
        Accessible.checked: bar.draft.looked
        function toggle() { bar.draft.looked = !bar.draft.looked }
        Row {
            id: tickRow
            spacing: Kit.Theme.gapSmall + 2
            Rectangle {
                width: Kit.Theme.textSize + 2; height: width
                radius: Kit.Theme.gapSmall / 2
                color: bar.draft.looked ? Kit.Theme.raised : "transparent"
                border.width: tick.activeFocus ? Kit.Theme.focusRing : 1
                border.color: tick.activeFocus ? Kit.Theme.borderActive : Kit.Theme.borderStrong
                Kit.Icon { anchors.centerIn: parent; visible: bar.draft.looked; name: "check"; size: parent.width - 4; color: Kit.Theme.fg }
            }
            Text {
                text: "I looked in " + bar.via
                color: Kit.Theme.fg
                font.family: Kit.Theme.fontFamily
                font.pixelSize: Kit.Theme.smallSize
                textFormat: Text.PlainText
            }
        }
        TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: tick.toggle() }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        Keys.onSpacePressed: tick.toggle()
        Keys.onReturnPressed: tick.toggle()
    }
    Text {
        objectName: "noteLine"
        visible: text !== ""
        Layout.fillWidth: true
        text: bar.reply && bar.reply.note ? bar.reply.note : ""
        color: Kit.Theme.muted
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.smallSize
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    Text {
        objectName: "pressLine"
        visible: text !== ""
        Layout.fillWidth: true
        text: bar.pressLine
        color: Kit.Theme.badInk
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.smallSize
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Kit.Theme.gap
        visible: bar.phase !== "sent" || extraRow.visibleChildren.length > 0

        CardButton {
            id: send
            objectName: "sendButton"
            visible: bar.phase !== "sent"
            tone: bar.draft.copy ? "quiet" : "send"
            label: bar.button
            live: bar.draft.copy ? bar.draft.hasText && !bar.copied : bar.draft.live
            onActivated: bar.activate()
        }
        Text {
            objectName: "yoursLabel"
            visible: send.ringed
            text: "Yours: " + bar.button
            color: Kit.Theme.accentInk
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.captionSize
            font.weight: Font.Medium
            textFormat: Text.PlainText
        }
        Item { Layout.fillWidth: true }
        Row { id: extraRow; spacing: Kit.Theme.gapSmall }
    }
}
