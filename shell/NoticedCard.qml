import QtQuick
import QtQuick.Layouts

// What the loop has noticed, as a card that rises above the chip: 300 px wide, titled "Noticed"
// with one line of why, at most three rows, then the quiet lines. A row is the offer: his own
// words, how often, one line of what would happen, and one button. Pressing it IS the ask: there
// is no yes/no question after it. "Other ways" shows the other forms as small choices; Not now
// and Never are bare text. Plain Qt Quick: it draws what a LoopState holds and tells it what was
// pressed. Zero-sized while hidden, so the bar's input mask takes no room for it.
Item {
    id: card
    objectName: "noticedCard"
    required property var loop        // a LoopState
    required property var chip        // the NoticedChip it rises from
    property string screenName: ""

    readonly property bool shown: loop.cardOpen && loop.cardScreen === screenName
    // Above the chip; beside the pill the chip is lower than the pill's top, so above the pill.
    readonly property real bottomGap: chip.beside ? chip.edge + chip.pillHeight + 6 : chip.bottomGap + chip.implicitHeight + 8
    // How far above the window's bottom edge the card reaches: the layer must grow to hold it.
    readonly property real reach: shown ? bottomGap + implicitHeight + 12 : 0

    implicitWidth: 300
    implicitHeight: body.implicitHeight + 26
    width: shown ? implicitWidth : 0
    height: shown ? implicitHeight : 0
    visible: shown
    // Its right edge is the chip's.
    x: Math.max(chip.gap, chip.x + chip.implicitWidth - implicitWidth)
    anchors.bottom: parent.bottom
    anchors.bottomMargin: bottomGap

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: "#f51a1d21"
        border.width: 1
        border.color: "#2a2f36"
    }

    // The pointer may cross the gap between the chip and the card: the loop waits a moment.
    HoverHandler { onHoveredChanged: card.loop.hoverCard(card.screenName, hovered) }

    ColumnLayout {
        id: body
        x: 14
        y: 12
        width: card.implicitWidth - 28
        spacing: 10

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                objectName: "noticedTitle"
                text: "Noticed"
                color: "#e6e8eb"
                font.pixelSize: 16
                font.weight: Font.Medium
            }
            Text {
                objectName: "noticedWhy"
                Layout.fillWidth: true
                text: card.loop.why()
                color: "#8b939c"
                font.pixelSize: 12
                textFormat: Text.PlainText
                elide: Text.ElideRight
                maximumLineCount: 1
            }
        }

        Repeater {
            model: card.loop.shownRows

            ColumnLayout {
                id: row
                required property var modelData
                required property int index
                readonly property var prim: card.loop.primary(modelData)
                readonly property var ways: card.loop.others(modelData)
                readonly property var quiet: card.loop.quiet(modelData)
                readonly property bool waysOpen: card.loop.focusedRow === String(modelData.id)
                readonly property bool waiting: card.loop.pending === String(modelData.id)
                readonly property var answer: {
                    const r = card.loop.result
                    return r !== null && r.id === String(modelData.id) && (!r.ok || r.preview !== "") ? r : null
                }
                Layout.fillWidth: true
                spacing: 3

                Rectangle {
                    visible: row.index > 0
                    Layout.fillWidth: true
                    Layout.bottomMargin: 7
                    implicitHeight: 1
                    color: "#2a2f36"
                }

                // His own words for an offer.
                Text {
                    objectName: "noticedRowTitle"
                    Layout.fillWidth: true
                    text: String(row.modelData.title || "")
                    color: "#e6e8eb"
                    font.pixelSize: 13
                    font.weight: Font.Medium
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }
                // How often, with the ways of saying no at the right end: they are for the whole row.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        objectName: "noticedRowMeta"
                        Layout.fillWidth: true
                        text: String(row.modelData.meta || "")
                        color: "#8b939c"
                        font.pixelSize: 12
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        maximumLineCount: 1
                    }
                    Repeater {
                        model: row.quiet
                        LineButton {
                            required property var modelData
                            objectName: modelData.op === "never" ? "noticedNever" : "noticedNotNow"
                            quiet: true
                            label: modelData.label
                            enabled: !row.waiting
                            onClicked: modelData.op === "never" ? card.loop.never(row.modelData) : card.loop.notNow(row.modelData)
                        }
                    }
                }
                // One line of what would happen.
                Text {
                    objectName: "noticedRowWhat"
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: String(row.modelData.what || "")
                    color: "#c5cad0"
                    font.pixelSize: 12
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                }

                // What a preview showed, or why a tap did not work.
                Rectangle {
                    objectName: "noticedResult"
                    Layout.fillWidth: true
                    Layout.topMargin: 3
                    visible: row.answer !== null
                    implicitHeight: answerText.implicitHeight + 14
                    radius: 8
                    color: "#14171a"
                    border.width: 1
                    border.color: "#2a2f36"
                    Text {
                        id: answerText
                        objectName: "noticedResultText"
                        x: 10
                        y: 7
                        width: parent.width - 20
                        text: row.answer === null ? "" : (row.answer.ok ? row.answer.preview : (row.answer.text || "That did not work."))
                        color: row.answer !== null && !row.answer.ok ? "#f0a0a0" : "#c5cad0"
                        font.pixelSize: 12
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    Layout.topMargin: 5
                    spacing: 4

                    LineButton {
                        objectName: "noticedPrimary"
                        label: row.prim.label
                        primary: true
                        enabled: !row.waiting
                        onClicked: card.loop.press(row.modelData)
                    }
                    LineButton {
                        objectName: "noticedOtherWays"
                        visible: row.ways.length > 0
                        quiet: true
                        label: "Other ways"
                        onClicked: card.loop.toggleWays(row.modelData)
                    }
                }

                // The other forms, as small choices: a tap on one is the ask for that form.
                Flow {
                    objectName: "noticedWays"
                    Layout.fillWidth: true
                    visible: row.waysOpen && row.ways.length > 0
                    spacing: 6
                    Repeater {
                        model: row.ways
                        LineButton {
                            required property var modelData
                            objectName: "noticedWay"
                            label: modelData.label
                            enabled: !row.waiting
                            onClicked: card.loop.choose(row.modelData, modelData)
                        }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            visible: restingText.text !== "" || latelyText.text !== ""

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: "#2a2f36"
            }
            Text {
                id: restingText
                objectName: "noticedResting"
                Layout.fillWidth: true
                visible: text !== ""
                text: card.loop.restingLine()
                color: "#8b939c"
                font.pixelSize: 12
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                visible: latelyText.text !== ""
                spacing: 0
                Text {
                    id: latelyText
                    objectName: "noticedLately"
                    Layout.fillWidth: true
                    text: card.loop.latelyLine()
                    color: "#8b939c"
                    font.pixelSize: 12
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }
                LineButton {
                    objectName: "noticedSeeAll"
                    quiet: true
                    label: "See all"
                    onClicked: card.loop.openWindow()
                }
            }
        }
    }
}
