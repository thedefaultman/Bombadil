import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The line above the pill: what the agent is doing right now, in plain words, and how the
// turn ended. One line while it works; at most four when it is done. A step that touches the
// system gets an amber edge with the exact command under it, one no restore point can undo a
// red one; neither pauses anything. Clicking a finished line shows every command and its output.
// When no turn or setup line has it, the newest notice (new mail, a draft that is ready) does, with
// its chips on the right (under the words when the line is narrow, a window shares the stage).
Rectangle {
    id: bar
    required property var pill       // a PillState

    readonly property var notice: pill.noticeShown ? pill.notice : null
    readonly property bool shown: pill.mode !== "idle" || pill.flash !== "" || notice !== null
    readonly property color edge: pill.mode === "working" && pill.risk === "irreversible" ? Kit.Theme.bad
                                 : pill.mode === "working" && pill.risk === "system" ? Kit.Theme.warn
                                 : pill.source === "error" && pill.mode !== "working" ? Kit.Theme.bad
                                 : "transparent"
    // A notice's own look: a warning is red, news has no edge. (pill.source belongs to the turn line.)
    readonly property bool errored: notice ? notice.tone === "error" : pill.source === "error"
    readonly property color markEdge: notice ? (errored ? Kit.Theme.bad : "transparent") : edge
    property double now: Date.now()
    readonly property int seconds: Math.max(0, Math.floor((now - pill.startedAt) / 1000))
    readonly property bool hovered: hover.hovered
    readonly property bool narrow: width < 520

    implicitHeight: shown ? content.implicitHeight + 20 : 0
    radius: Kit.Theme.radiusLine
    color: Kit.Theme.glassLine
    border.width: 1
    border.color: Kit.Theme.border
    opacity: shown ? 1 : 0
    visible: opacity > 0
    clip: true
    Behavior on opacity { NumberAnimation { duration: Kit.Theme.normal } }
    Behavior on implicitHeight { NumberAnimation { duration: Kit.Theme.fast; easing.type: Easing.OutCubic } }

    // The marked edge: amber for system steps, red for ones that cannot be undone.
    Rectangle {
        // Inset, so it stays inside the rounded corners (clip does not follow the radius).
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom; leftMargin: 7; topMargin: 9; bottomMargin: 9 }
        width: 3
        radius: 1.5
        color: bar.markEdge
        visible: bar.markEdge !== "transparent"
    }

    Timer {
        // The seconds counter, and fading a finished line nobody is looking at.
        interval: 250; repeat: true; running: bar.shown && bar.notice === null   // a notice has nothing to count
        onTriggered: {
            bar.now = Date.now()
            if (bar.pill.flash && bar.now - bar.pill.flashAt > bar.pill.flashFor) bar.pill.flash = ""
            // The line shows on every screen; hovering it on any of them keeps it.
            const done = bar.pill.mode === "closing" || bar.pill.mode === "local"
            if (done && !bar.pill.sticky && bar.pill.hovers === 0 && bar.now - bar.pill.lineAt > bar.pill.fadeAfter)
                bar.pill.fade()
        }
    }

    HoverHandler {
        id: hover
        onHoveredChanged: bar.pill.hovers = Math.max(0, bar.pill.hovers + (hovered ? 1 : -1))
    }
    Component.onDestruction: if (hover.hovered) bar.pill.hovers = Math.max(0, bar.pill.hovers - 1)
    TapHandler {
        // A finished turn opens its details; anything else just stays while you read it.
        enabled: bar.pill.mode === "closing"
        onTapped: bar.pill.details()
    }

    ColumnLayout {
        id: content
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10; leftMargin: 18; rightMargin: 14 }
        spacing: 4

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Text {
                id: lineText
                objectName: "line"
                font.family: Kit.Theme.fontFamily
                Layout.fillWidth: true
                text: bar.pill.flash !== "" ? bar.pill.flash : bar.notice ? bar.notice.line : bar.pill.line
                color: bar.errored && bar.pill.flash === "" ? Kit.Theme.badInk : Kit.Theme.fg
                font.pixelSize: Kit.Theme.lineSize
                textFormat: Text.PlainText
                // Working: one line. The agent's own words show their newest end.
                wrapMode: bar.pill.mode === "working" && bar.pill.flash === "" ? Text.NoWrap : Text.Wrap
                // A flash (the answer to "why") may take two lines; the working line is one.
                maximumLineCount: bar.pill.flash !== "" ? 2 : bar.pill.mode === "working" ? 1 : 4
                elide: bar.pill.mode === "working" && bar.pill.source === "agent" ? Text.ElideLeft : Text.ElideRight
            }

            Text {
                objectName: "counter"
                font.family: Kit.Theme.fontFamily
                visible: bar.pill.mode === "working" && bar.seconds >= 1
                text: bar.seconds + "s"
                color: Kit.Theme.muted
                font.pixelSize: Kit.Theme.smallSize
                font.features: { "tnum": 1 }
            }

            NoticeChips {
                objectName: "noticeTail"
                visible: bar.notice !== null
                pill: bar.pill
                notice: bar.notice
                withChips: !bar.narrow
                Layout.alignment: Qt.AlignVCenter
            }
        }

        // The same chips under the words, when the line has no room beside them.
        NoticeChips {
            objectName: "noticeRow"
            visible: bar.notice !== null && bar.narrow && bar.notice.actions.length > 0
            pill: bar.pill
            notice: bar.notice
            withTail: false
            Layout.alignment: Qt.AlignRight
        }

        // The exact command of a marked step, while it runs.
        Text {
            objectName: "command"
            Layout.fillWidth: true
            visible: bar.pill.mode === "working" && bar.pill.command !== "" && bar.pill.flash === ""
            text: bar.pill.command
            color: bar.pill.risk === "irreversible" ? Kit.Theme.badInk : Kit.Theme.warnInk
            font.family: Kit.Theme.monoFamily
            font.pixelSize: Kit.Theme.captionSize
            textFormat: Text.PlainText
            elide: Text.ElideRight
            maximumLineCount: 1
        }

        // Why this step happens: the agent's own sentence from just before it acted. Quiet, and
        // there when you rest the mouse on the line; a step that touches the system always shows it,
        // because that is the one you would want it for while Esc is still in reach.
        Text {
            objectName: "because"
            Layout.fillWidth: true
            visible: bar.pill.mode === "working" && bar.pill.flash === "" && bar.pill.because !== ""
                     && (bar.hovered || bar.pill.risk !== "")
            text: bar.pill.because
            font.family: Kit.Theme.fontFamily
            color: Kit.Theme.muted
            font.pixelSize: Kit.Theme.smallSize
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }

        // Where the idea may have come from: the machine read something outside before this step.
        // Said by order, not guessed by cause.
        Text {
            objectName: "after"
            Layout.fillWidth: true
            visible: bar.pill.mode === "working" && bar.pill.flash === "" && bar.pill.after !== "" && bar.pill.risk !== ""
            text: bar.pill.after
            font.family: Kit.Theme.fontFamily
            color: Kit.Theme.warnInk
            opacity: 0.8
            font.pixelSize: Kit.Theme.captionSize
            textFormat: Text.PlainText
            elide: Text.ElideRight
            maximumLineCount: 1
        }

        // A turn that changed something: take it back, or see exactly what ran.
        RowLayout {
            Layout.fillWidth: true
            visible: bar.pill.mode === "closing" && (bar.pill.changed || bar.pill.irreversible)
            spacing: 8

            Text {
                font.family: Kit.Theme.fontFamily
                visible: bar.pill.irreversible
                text: "can’t be undone"
                color: Kit.Theme.badInk
                font.pixelSize: Kit.Theme.captionSize
            }
            Item { Layout.fillWidth: true }
            LineButton {
                objectName: "undoButton"
                visible: bar.pill.sticky
                label: "Undo"
                onClicked: bar.pill.undo()
            }
            LineButton {
                objectName: "detailsButton"
                label: "Details"
                onClicked: bar.pill.details()
            }
        }
    }
}
