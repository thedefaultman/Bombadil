import QtQuick
import QtQuick.Layouts

// The line above the pill: what the agent is doing right now, in plain words, and how the
// turn ended. One line while it works; at most four when it is done. A step that touches the
// system gets an amber edge with the exact command under it, one no restore point can undo a
// red one; neither pauses anything. Clicking a finished line shows every command and its output.
Rectangle {
    id: bar
    required property var pill       // a PillState
    // A welcome shows on one screen only, the one that had the focus when it arrived.
    property bool here: true

    readonly property bool shown: (pill.mode !== "idle" && (pill.mode !== "welcome" || here)) || pill.flash !== ""
    readonly property color edge: pill.mode === "working" && pill.risk === "irreversible" ? "#e05252"
                                 : pill.mode === "working" && pill.risk === "system" ? "#e8a33d"
                                 : pill.source === "error" && pill.mode !== "working" ? "#c04a4a"
                                 : "transparent"
    property double now: Date.now()
    readonly property int seconds: Math.max(0, Math.floor((now - pill.startedAt) / 1000))
    readonly property bool hovered: hover.hovered

    implicitHeight: shown ? content.implicitHeight + 20 : 0
    radius: 14
    color: "#e61a1d21"
    border.width: 1
    border.color: "#2a2f36"
    opacity: shown ? 1 : 0
    visible: opacity > 0
    clip: true
    Behavior on opacity { NumberAnimation { duration: 200 } }
    Behavior on implicitHeight { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

    // The marked edge: amber for system steps, red for ones that cannot be undone.
    Rectangle {
        // Inset, so it stays inside the rounded corners (clip does not follow the radius).
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom; leftMargin: 7; topMargin: 9; bottomMargin: 9 }
        width: 3
        radius: 1.5
        color: bar.edge
        visible: bar.edge !== "transparent"
    }

    Timer {
        // The seconds counter, and fading a finished line nobody is looking at.
        interval: 250; repeat: true; running: bar.shown
        onTriggered: {
            bar.now = Date.now()
            if (bar.pill.flash && bar.now - bar.pill.flashAt > 3500) bar.pill.flash = ""
            // The line shows on every screen; hovering it on any of them keeps it.
            const done = bar.pill.mode === "closing" || bar.pill.mode === "local"
            if (done && !bar.pill.sticky && bar.pill.hovers === 0 && bar.now - bar.pill.lineAt > bar.pill.fadeAfter)
                bar.pill.dismiss()
            if (bar.pill.welcomeDone(bar.now)) bar.pill.dismiss()
        }
    }

    HoverHandler {
        id: hover
        onHoveredChanged: {
            bar.pill.hovers = Math.max(0, bar.pill.hovers + (hovered ? 1 : -1))
            if (hovered) bar.pill.touched()
        }
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
                Layout.fillWidth: true
                text: bar.pill.flash !== "" ? bar.pill.flash : bar.pill.line
                color: bar.pill.source === "error" && bar.pill.flash === "" ? "#f0a0a0" : "#e6e8eb"
                font.pixelSize: 15
                textFormat: Text.PlainText
                // Working: one line. The agent's own words show their newest end.
                wrapMode: bar.pill.mode === "working" || bar.pill.flash !== "" ? Text.NoWrap : Text.Wrap
                maximumLineCount: bar.pill.mode === "working" || bar.pill.flash !== "" ? 1 : 4
                elide: bar.pill.mode === "working" && bar.pill.source === "agent" ? Text.ElideLeft : Text.ElideRight
            }

            Text {
                objectName: "counter"
                visible: bar.pill.mode === "working" && bar.seconds >= 1
                text: bar.seconds + "s"
                color: "#8b939c"
                font.pixelSize: 13
                font.features: { "tnum": 1 }
            }
        }

        // The exact command of a marked step, while it runs.
        Text {
            objectName: "command"
            Layout.fillWidth: true
            visible: bar.pill.mode === "working" && bar.pill.command !== "" && bar.pill.flash === ""
            text: bar.pill.command
            color: bar.pill.risk === "irreversible" ? "#f0a0a0" : "#e8c38d"
            font.family: "monospace"
            font.pixelSize: 12
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
                visible: bar.pill.irreversible
                text: "can’t be undone"
                color: "#f0a0a0"
                font.pixelSize: 12
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
