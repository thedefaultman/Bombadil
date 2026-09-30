pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import "DeskTheme.js" as T

// Now: what is it doing, and why? The turn's route as a card: your ask for the title, a tick per
// finished step, the current one pulsing orange in its own words, the rest grey. A step that
// touches the system carries its exact command under it in the StatusLine's amber, one no restore
// point can undo carries the red edge and, while it runs, the words that explain the colour. The
// line above the pill keeps the verb, the seconds and Undo; none of them is repeated here.
// model is DeskState's nowModel: {title, why, steps: [{id, label, status}], edge, command, caption, done}.
DeskCard {
    id: card
    objectName: "nowCard"
    namePrefix: "now"
    property var model: ({})

    readonly property var m: model || ({})
    readonly property var stepList: m.steps || []
    readonly property bool hasCommand: !m.done && (m.command || "") !== ""
    readonly property bool hasCaption: !m.done && (m.caption || "") !== ""
    readonly property int commandHeight: hasCommand ? 20 : 0
    readonly property int captionHeight: hasCaption ? 18 : 0
    // The command sits under the current step; with none (a system step outside any plan) under the last.
    readonly property int hostIndex: !hasCommand && !hasCaption ? -1
                                   : currentIndex >= 0 ? currentIndex : stepList.length - 1
    readonly property int currentIndex: {
        for (let i = 0; i < stepList.length; i++)
            if (phaseOf(stepList[i]) === "current") return i
        return -1
    }
    readonly property bool red: m.edge === "red"

    // done, current or pending: agentd's plan statuses and the plain words both read the same.
    function phaseOf(step) {
        if (m.done) return "done"
        const s = step.status
        return s === "completed" || s === "done" ? "done"
             : s === "in_progress" || s === "current" ? "current"
             : "pending"
    }

    title: m.title || ""
    why: m.why || ""
    edge: m.edge || ""

    Item {
        Layout.fillWidth: true
        implicitHeight: 26 * card.stepList.length + card.commandHeight + card.captionHeight

        // The first disc sits 8 px under the header's own 50.
        Item {
            y: 8
            width: parent.width

            // One item per step, reading its step by place: a plan that changes with every status
            // then updates the rows in place, and the pulsing ring keeps its beat.
            Repeater {
                model: card.stepList.length

                Item {
                    id: row
                    objectName: "nowRow"
                    required property int index

                    readonly property var step: card.stepList[index] || ({})
                    readonly property string status: step.status || ""
                    readonly property string label: step.label || ""
                    readonly property string phase: card.phaseOf(step)
                    readonly property bool ticked: phase === "done"
                    readonly property bool pulsing: phase === "current"
                    readonly property color discColor: ticked ? T.ok : pulsing ? T.machine : "transparent"
                    readonly property color labelColor: phase === "pending" ? T.muted : T.fg

                    y: 26 * index + (card.hostIndex >= 0 && index > card.hostIndex
                                     ? card.commandHeight + card.captionHeight : 0)
                    width: card.width
                    height: 26

                    // Done, current: a disc at x 22, y 13 of the row; pending: a grey ring.
                    Rectangle {
                        x: 16; y: 7
                        width: 12; height: 12; radius: 6
                        color: row.discColor
                        visible: row.phase !== "pending"

                        // The tick: two short bars, the shorter one falling, the longer one rising.
                        Rectangle {
                            visible: row.ticked
                            x: 6 - 2 - width / 2; y: 6 + 1 - height / 2
                            width: 4.4; height: 1.6; radius: 0.8
                            rotation: 45
                            color: T.sunken
                            antialiasing: true
                        }
                        Rectangle {
                            visible: row.ticked
                            x: 6 + 1 - width / 2; y: 6 - height / 2
                            width: 7.3; height: 1.6; radius: 0.8
                            rotation: -45
                            color: T.sunken
                            antialiasing: true
                        }
                    }
                    Rectangle {
                        x: 16.25; y: 7.25
                        width: 11.5; height: 11.5; radius: 5.75
                        color: "transparent"
                        border.width: 1.5
                        border.color: T.faint
                        visible: row.phase === "pending"
                    }
                    DeskCard.Ring {
                        objectName: "nowRing"
                        x: 22 - r; y: 13 - r
                        r: 9.5
                        tone: T.machine
                        live: row.pulsing
                    }

                    Text {
                        objectName: "nowLabel"
                        font.family: T.fontFamily
                        x: 38
                        y: Math.round(17 - baselineOffset)
                        width: card.width - x - 14
                        text: row.label
                        color: row.labelColor
                        font.pixelSize: 13
                        font.weight: row.pulsing ? Font.Medium : Font.Normal
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        maximumLineCount: 1
                    }
                }
            }

            // What the current step runs, and, when it cannot be undone, why the edge is red.
            Item {
                y: 26 * (card.hostIndex + 1)
                width: parent.width
                visible: card.hasCommand || card.hasCaption

                Rectangle {
                    objectName: "nowCommandBar"
                    x: 34; y: -2
                    width: 3; height: 16; radius: 1.5
                    color: card.red ? T.red : T.amber
                    visible: card.hasCommand
                }
                Text {
                    objectName: "nowCommand"
                    x: 44
                    y: Math.round(10 - baselineOffset)
                    width: card.width - x - 14
                    text: card.m.command || ""
                    color: card.red ? T.redText : T.amberText
                    font.family: T.monoFamily
                    font.pixelSize: 11
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    visible: card.hasCommand
                }
                Text {
                    objectName: "nowCaption"
                    font.family: T.fontFamily
                    x: 44
                    y: card.commandHeight + 1
                    width: card.width - x - 14
                    text: card.m.caption || ""
                    color: card.red ? T.redText : T.muted
                    font.pixelSize: 11
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    visible: card.hasCaption
                }
            }
        }
    }
}
