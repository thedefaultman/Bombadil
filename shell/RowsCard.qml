pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import "DeskTheme.js" as T

// A card of rows: Watching (what is counting) and Needs you (what is waiting for you). A row is a
// meter (a label, what it says, a percent and a track filled in the colour of who is using it, and
// a small x when it may be removed), a dot row (a title, one line under it, a button or a small x)
// or a plain one without the dot.
// A button never starts work by itself: it reports the press and the shell decides.
// model is DeskState's watchModel or needsModel:
// {title, why, rows: [{key, kind, title, sub, meter, meterText, tone, pulse, button, remove}]}.
DeskCard {
    id: card
    objectName: "rowsCard"
    namePrefix: "rows"
    property var model: ({})
    signal rowAction(string key, string action)
    signal rowRemove(string key)

    readonly property var m: model || ({})
    readonly property var rowList: m.rows || []
    property var seen: null      // the keys of the rows on the card before the model last changed

    // Colour says who: white is you, orange the machine's turn, blue a coding session.
    function toneColor(tone) {
        return tone === "machine" ? T.machine
             : tone === "sessions" ? T.sessions
             : tone === "you" ? T.you
             : tone === "ok" ? T.ok
             : tone === "red" ? T.red
             : T.muted
    }

    title: m.title || ""
    why: m.why || ""

    // A row that was not there a moment ago washes the card's edge in its own colour. Rows that
    // come with the card, or onto a card that had none, are the card arriving.
    onRowListChanged: {
        const before = seen
        seen = rowList.map(r => r.key)
        if (before === null || before.length === 0) return
        for (const r of rowList)
            if (before.indexOf(r.key) < 0) {
                wash(toneColor(r.tone))
                break
            }
    }
    Component.onCompleted: seen = rowList.map(r => r.key)

    Column {
        Layout.fillWidth: true

        // One item per row, reading its row by place: a model that changes every second (a
        // progress meter) then updates the rows in place instead of building them again.
        Repeater {
            model: card.rowList.length

            Item {
                id: row
                objectName: "rowsRow"
                required property int index

                readonly property var spec: card.rowList[index] || ({})
                readonly property string key: spec.key || ""
                readonly property string kind: spec.kind === "meter" || spec.kind === "plain" ? spec.kind : "dot"
                readonly property string tone: spec.tone || ""
                readonly property color toneColor: card.toneColor(tone)
                readonly property string sub: spec.sub || ""
                readonly property string button: spec.button || ""
                readonly property bool removable: spec.remove === true
                readonly property bool hasMeter: spec.meter !== undefined && spec.meter !== null
                readonly property real fraction: hasMeter ? Math.max(0, Math.min(1, spec.meter)) : 0
                // Only a live dot in the machine's or a session's colour breathes.
                readonly property bool pulsing: spec.pulse === true && (tone === "machine" || tone === "sessions")
                // Where the words must stop: short of the buttons, when there are any.
                readonly property real textRight: buttons.visible ? buttons.x - 8 : card.width - 14
                // A meter row with a small x gives it this much of its right edge; the percent and the
                // words beside it move left by it. A meter row without one does not move.
                readonly property real meterRoom: kind === "meter" && removable ? 22 : 0

                width: card.width
                height: kind === "meter" ? 34 : T.rowHeight

                // The title: a meter row's label, or the first line of a dot or plain row.
                Text {
                    objectName: "rowsRowTitle"
                    font.family: T.fontFamily
                    x: row.kind === "dot" ? 32 : 14
                    y: Math.round((row.kind === "meter" ? 22 : 26) - baselineOffset)
                    width: Math.max(0, (row.kind === "meter" ? meta.x - 8 : row.textRight) - x)
                    text: row.spec.title || ""
                    color: T.fg
                    font.pixelSize: 13
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }
                Text {
                    id: meta
                    objectName: "rowsMeta"
                    font.family: T.fontFamily
                    visible: row.kind === "meter"
                    x: 250 - row.meterRoom - Math.min(implicitWidth, 150)
                    y: Math.round(22 - baselineOffset)
                    width: Math.min(implicitWidth, 150)
                    text: row.spec.meterText || ""
                    color: T.muted
                    font.pixelSize: 12
                    textFormat: Text.PlainText
                    elide: Text.ElideLeft
                    maximumLineCount: 1
                }
                Text {
                    objectName: "rowsPercent"
                    font.family: T.fontFamily
                    visible: row.kind === "meter" && row.hasMeter
                    x: 286 - row.meterRoom - implicitWidth
                    y: Math.round(22 - baselineOffset)
                    text: Math.round(row.fraction * 100) + "%"
                    color: T.muted
                    font.pixelSize: 12
                }
                Rectangle {
                    id: track
                    objectName: "rowsMeter"
                    visible: row.kind === "meter"
                    x: 14; y: 28
                    width: 272; height: 6; radius: 3
                    color: T.raised
                    readonly property real fillWidth: fill.width
                    readonly property color fillColor: row.toneColor
                    Rectangle {
                        id: fill
                        width: row.fraction * parent.width
                        height: parent.height
                        radius: 3
                        color: row.toneColor
                        visible: width > 0
                    }
                }

                // A dot row's dot, and its ring when the row is live.
                Rectangle {
                    objectName: "rowsDot"
                    visible: row.kind === "dot"
                    x: 19 - 4.5; y: 22 - 4.5
                    width: 9; height: 9; radius: 4.5
                    color: row.toneColor
                }
                DeskCard.Ring {
                    objectName: "rowsRing"
                    x: 19 - r; y: 22 - r
                    r: 8
                    tone: row.toneColor
                    live: row.kind === "dot" && row.pulsing
                }
                Text {
                    objectName: "rowsRowSub"
                    font.family: T.fontFamily
                    visible: row.kind !== "meter" && row.sub !== ""
                    x: row.kind === "dot" ? 32 : 14
                    y: Math.round(42 - baselineOffset)
                    width: Math.max(0, row.textRight - x)
                    text: row.sub
                    color: T.muted
                    font.pixelSize: 11
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }

                // The button, and the small x that stops or drops the row. Right edge at 286. A meter
                // row has no button, only the x, up by the label's line because the track is under it.
                Row {
                    id: buttons
                    visible: row.kind === "meter" ? row.removable : (row.button !== "" || row.removable)
                    x: 286 - width
                    y: row.kind === "meter" ? 6 : 12
                    spacing: 6

                    Rectangle {
                        id: pressButton
                        objectName: "rowsButton"
                        visible: row.kind !== "meter" && row.button !== ""
                        readonly property string text: row.button
                        readonly property bool primary: row.button === "Do it"
                        // What was under the finger at the press: the rows may shift before the release.
                        property string armedKey: ""
                        property string armedText: ""
                        width: Math.max(primary ? 53 : 46, label.implicitWidth + 16)
                        height: 22; radius: 11
                        color: primary ? (tap.pressed ? Qt.darker(T.machine, 1.15) : hover.hovered ? Qt.lighter(T.machine, 1.1) : T.machine)
                                       : (tap.pressed ? T.borderStrong : hover.hovered ? T.border : T.raised)
                        border.width: primary ? 0 : 1
                        border.color: T.border
                        Text {
                            id: label
                            font.family: T.fontFamily
                            anchors.centerIn: parent
                            text: pressButton.text
                            // Do it is orange with white on it; the theme's white is the you-colour, a shade short.
                            color: pressButton.primary ? T.onAccent : T.fg
                            font.pixelSize: 12
                            textFormat: Text.PlainText
                        }
                        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            id: tap
                            gesturePolicy: TapHandler.ReleaseWithinBounds
                            onPressedChanged: if (pressed) {
                                pressButton.armedKey = row.key
                                pressButton.armedText = pressButton.text
                            }
                            onTapped: card.rowAction(pressButton.armedKey, pressButton.armedText)
                        }
                    }
                    Item {
                        id: drop
                        objectName: "rowsRemove"
                        visible: row.removable
                        property string armedKey: ""
                        width: 22; height: 22
                        Text {
                            font.family: T.fontFamily
                            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                            text: "×"
                            color: dropHover.hovered ? T.fg : T.muted
                            font.pixelSize: 12
                        }
                        HoverHandler { id: dropHover; cursorShape: Qt.PointingHandCursor; margin: 4 }
                        TapHandler {
                            margin: 4
                            gesturePolicy: TapHandler.ReleaseWithinBounds
                            onPressedChanged: if (pressed) drop.armedKey = row.key
                            onTapped: card.rowRemove(drop.armedKey)
                        }
                    }
                }
            }
        }
    }
}
