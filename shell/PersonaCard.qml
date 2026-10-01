import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import Bombadil as Kit

// The card that asks what to call you and in which voice Bombadil should greet you: one name
// field and a row per voice, each reading back how it would welcome you with the name typed so
// far. Enter answers with the highlighted voice, Esc skips (agentd already saved the defaults),
// Up and Down move the highlight, a click on a row picks it. Plain QtQuick like StatusLine:
// shell.qml hands it the keyboard through takeKeys() and listens for wantKeys() and closed().
Rectangle {
    id: card
    required property var pill       // a PillState
    // The card shows on one screen only, the one that had the focus when it came up.
    property bool here: true

    readonly property var ask: pill.personaAsk
    readonly property bool shown: ask !== null && ask !== undefined && here
    readonly property var voices: ask && ask.voices ? ask.voices : []
    // What was typed, as agentd will read it: trimmed, inner spaces collapsed.
    readonly property string typed: field.text.trim().replace(/\s+/g, " ")
    readonly property string chosen: picked < voices.length ? String(voices[picked].name || voices[picked].id || "") : ""
    readonly property bool focused: field.activeFocus
    property int picked: 0
    property bool problem: false     // Enter was refused: the name is more than three words

    // The card was clicked: the shell gives it the keyboard.
    signal wantKeys()
    // Answered or skipped: the shell gives the keyboard back.
    signal closed()

    function takeKeys() { field.forceActiveFocus() }

    // How a voice would greet you, with the name typed so far ("{n}" is ", Name" or nothing).
    function sample(voice) {
        // A name ending in a dot ("Daniel Z.") meets the template's own full stop: one is enough.
        return String(voice && voice.card || "").split("{n}").join(typed !== "" ? ", " + typed : "")
            .replace(/\.\.(?!\.)/g, ".")
    }

    function move(step) {
        if (voices.length > 0) picked = Math.max(0, Math.min(voices.length - 1, picked + step))
    }

    function pick(i) {
        if (i >= 0 && i < voices.length) picked = i
    }

    function answer() {
        if (!shown) return
        if (typed.split(" ").length > 3) { problem = true; return }
        pill.personaAnswer(typed, picked < voices.length ? voices[picked].id : (ask.voice || "merry"))
        closed()
    }

    function skip() {
        if (!shown) return
        pill.personaSkip()
        closed()
    }

    // A new question starts from what agentd sent: empty, or the current name and voice.
    function reset() {
        field.text = ask.name
        problem = false
        // From ask itself: the voices binding may not have caught up with it yet.
        const vs = ask.voices || []
        let at = 0
        for (let i = 0; i < vs.length; i++)
            if (vs[i].id === ask.voice) { at = i; break }
        picked = at
    }
    onAskChanged: if (ask) reset()
    Component.onCompleted: if (ask) reset()

    implicitHeight: shown ? content.implicitHeight + 24 : 0
    radius: 14
    color: Kit.Theme.glassLine
    border.width: 1
    border.color: Kit.Theme.border
    opacity: shown ? 1 : 0
    // Visible the moment it is shown, so the field can take the keyboard before the fade-in starts.
    visible: shown || opacity > 0
    clip: true
    Behavior on opacity { NumberAnimation { duration: 200 } }
    Behavior on implicitHeight { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

    ColumnLayout {
        id: content
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12; leftMargin: 14; rightMargin: 14 }
        spacing: 8

        Text {
            objectName: "cardLine"
            Layout.fillWidth: true
            text: card.ask && card.ask.line !== "" ? card.ask.line : "What should I call you?"
            color: Kit.Theme.fg
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.lineSize
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }

        Rectangle {
            objectName: "nameBox"
            Layout.fillWidth: true
            implicitHeight: 38
            radius: 10
            color: Kit.Theme.raised
            border.width: 1
            border.color: card.problem ? Kit.Theme.bad : (field.activeFocus ? Kit.Theme.borderActive : Kit.Theme.borderStrong)

            // Not focus: true, it would compete with the pill's own field; takeKeys() is how it gets the keys.
            TextField {
                id: field
                objectName: "nameField"
                anchors.fill: parent
                leftPadding: 12
                rightPadding: 12
                placeholderText: "Your name"
                placeholderTextColor: Kit.Theme.muted
                color: Kit.Theme.fg
                font.family: Kit.Theme.fontFamily
                font.pixelSize: Kit.Theme.lineSize
                background: null
                maximumLength: 24
                // What agentd accepts in a name: a letter first, then letters, marks, spaces, hyphens,
                // apostrophes and dots. It is also what reaches the system prompt, so nothing else gets in.
                validator: RegularExpressionValidator { regularExpression: /^(\p{L}[\p{L}\p{M} '’.-]*)?$/ }
                onTextChanged: card.problem = false
                TapHandler { onTapped: card.wantKeys() }
                Keys.onUpPressed: card.move(-1)
                Keys.onDownPressed: card.move(1)
                // Tab is "next": the voices. Left alone it would take the focus to the pill behind the card.
                Keys.onTabPressed: card.move(1)
                Keys.onBacktabPressed: card.move(-1)
                Keys.onReturnPressed: card.answer()
                Keys.onEnterPressed: card.answer()
                Keys.onEscapePressed: card.skip()
            }
        }

        Repeater {
            model: card.voices

            Rectangle {
                id: row
                required property var modelData
                required property int index
                objectName: "voiceRow"
                readonly property string voiceId: String(modelData.id || "")
                readonly property bool on: card.picked === index
                Layout.fillWidth: true
                implicitHeight: rowContent.implicitHeight + 14
                radius: 10
                color: on ? Kit.Theme.raised : "transparent"
                border.width: 1
                border.color: on ? Kit.Theme.accent : (rowHover.hovered ? Kit.Theme.borderStrong : Kit.Theme.border)

                HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        card.pick(row.index)
                        card.wantKeys()
                        field.forceActiveFocus()
                    }
                }

                RowLayout {
                    id: rowContent
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 12; rightMargin: 12 }
                    spacing: 10

                    // A radio button: a ring, with a dot in the chosen row.
                    Rectangle {
                        Layout.alignment: Qt.AlignVCenter
                        implicitWidth: 14
                        implicitHeight: 14
                        radius: 7
                        color: "transparent"
                        border.width: 1.5
                        border.color: row.on ? Kit.Theme.accent : Kit.Theme.faint
                        Rectangle {
                            anchors.centerIn: parent
                            width: 6; height: 6; radius: 3
                            color: Kit.Theme.accent
                            visible: row.on
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1

                        Text {
                            objectName: "voiceName"
                            text: String(row.modelData.name || row.voiceId)
                            color: Kit.Theme.fg
                            font.family: Kit.Theme.fontFamily
                            font.pixelSize: Kit.Theme.textSize
                            font.bold: true
                            textFormat: Text.PlainText
                        }
                        Text {
                            objectName: "voiceSample"
                            Layout.fillWidth: true
                            text: card.sample(row.modelData)
                            color: row.on ? Kit.Theme.muted : Kit.Theme.muted
                            font.family: Kit.Theme.fontFamily
                            font.pixelSize: Kit.Theme.smallSize
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                        // How it ends a reply: the other half of what a voice is.
                        Text {
                            objectName: "voiceReply"
                            Layout.fillWidth: true
                            visible: text !== ""
                            text: String(row.modelData.reply || "")
                            color: row.on ? Kit.Theme.muted : Kit.Theme.faint
                            font.family: Kit.Theme.fontFamily
                            font.pixelSize: Kit.Theme.captionSize
                            font.italic: true
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }

        Text {
            objectName: "cardHint"
            Layout.fillWidth: true
            text: card.problem ? "A name is up to three words"
                : card.voices.length > 0 ? "Enter keeps " + card.chosen + " · Up and Down to change · Esc skips"
                : "Enter saves · Esc skips"
            color: card.problem ? Kit.Theme.badInk : Kit.Theme.muted
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.captionSize
            textFormat: Text.PlainText
            elide: Text.ElideRight
        }
    }
}
