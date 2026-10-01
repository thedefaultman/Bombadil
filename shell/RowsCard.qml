pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import "DeskTheme.js" as T

// A card of rows: Watching (what is counting), Needs you (what is waiting for you) and Machine
// (what the machine is using). A row is a meter (a label, what it says, a percent and a track filled
// in the colour of who is using it, and a small x when it may be removed), a stack (a meter whose
// track is cut into the pieces of who uses it, in order, so it has no single fill), a dot row (a
// title, one line under it, a button or a small x) or a plain one without the dot.
// A button never starts work by itself: it reports the press and the shell decides. A row that says
// what it opens reports a tap anywhere on it the same way; a button or the x on it still wins.
// model is DeskState's watchModel, needsModel or machineModel:
// {title, why, rows: [{key, kind, title, sub, meter, meterText, tone, parts: [{tone, fraction}],
// pulse, button, remove, opens}]}.
DeskCard {
    id: card
    objectName: "rowsCard"
    namePrefix: "rows"
    property var model: ({})
    signal rowAction(string key, string action)
    signal rowRemove(string key)
    signal rowOpen(string key, string opens)

    readonly property var m: model || ({})
    readonly property var rowList: m.rows || []
    property var seen: null      // the keys of the rows on the card before the model last changed

    // Colour says who: white is you, orange the machine's turn, blue a coding session.
    function toneColor(tone) {
        return tone === "machine" ? T.machine
             : tone === "sessions" ? T.sessions
             : tone === "you" ? T.you
             : tone === "ok" ? T.ok
             : tone === "amber" ? T.amber
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
                readonly property string kind: spec.kind === "meter" || spec.kind === "stack" || spec.kind === "plain"
                                               ? spec.kind : "dot"
                // A stack is laid out as a meter; only what fills its track differs.
                readonly property bool meterLike: kind === "meter" || kind === "stack"
                readonly property string tone: spec.tone || ""
                readonly property color toneColor: card.toneColor(tone)
                readonly property string sub: spec.sub || ""
                readonly property string button: spec.button || ""
                readonly property bool removable: spec.remove === true
                readonly property string opens: spec.opens || ""
                readonly property bool hasMeter: spec.meter !== undefined && spec.meter !== null
                readonly property real fraction: hasMeter ? Math.max(0, Math.min(1, spec.meter)) : 0
                // Only a live dot in the machine's or a session's colour breathes.
                readonly property bool pulsing: spec.pulse === true && (tone === "machine" || tone === "sessions")
                // Where the words must stop: short of the buttons, when there are any.
                readonly property real textRight: buttons.visible ? buttons.x - 8 : card.width - 14
                // A meter row with a small x gives it this much of its right edge; the percent and the
                // words beside it move left by it. A meter row without one does not move.
                readonly property real meterRoom: meterLike && removable ? 22 : 0
                // The pieces of a stack as {tone, x, w} on the track, left to right from its edge. Each
                // stops where the track does, so they never overflow it, and one under a pixel is not
                // drawn (the next still starts where it would have).
                readonly property var pieces: {
                    const list = kind === "stack" && spec.parts ? spec.parts : []
                    const out = []
                    let x = 0
                    for (let i = 0; i < list.length; i++) {
                        const p = list[i]
                        const w = Math.min(Number(p ? p.fraction : NaN) * track.width, track.width - x)
                        if (!(w > 0)) continue
                        if (w >= 1) out.push({ tone: p.tone, x: x, w: w })
                        x += w
                    }
                    return out
                }

                width: card.width
                height: meterLike ? 34 : T.rowHeight

                // A row that opens something takes a tap anywhere on it. A button or the x is a child of
                // the row, so it is asked first, and its exclusive grab leaves the row's tap out.
                HoverHandler { enabled: row.opens !== ""; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    // What was under the finger at the press, as for the buttons.
                    property string armedKey: ""
                    property string armedOpens: ""
                    enabled: row.opens !== ""
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onPressedChanged: if (pressed) {
                        armedKey = row.key
                        armedOpens = row.opens
                    }
                    onTapped: if (armedOpens !== "") card.rowOpen(armedKey, armedOpens)
                }

                // The title: a meter row's label, or the first line of a dot or plain row.
                Text {
                    objectName: "rowsRowTitle"
                    font.family: T.fontFamily
                    x: row.kind === "dot" ? 32 : 14
                    y: Math.round((row.meterLike ? 22 : 26) - baselineOffset)
                    width: Math.max(0, (row.meterLike ? meta.x - 8 : row.textRight) - x)
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
                    visible: row.meterLike
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
                    visible: row.meterLike && row.hasMeter
                    x: 286 - row.meterRoom - implicitWidth
                    y: Math.round(22 - baselineOffset)
                    text: Math.round(row.fraction * 100) + "%"
                    // A line crossed says so here: a stack has no single fill to say it.
                    color: row.tone === "amber" || row.tone === "red" ? row.toneColor : T.muted
                    font.pixelSize: 12
                }
                Rectangle {
                    id: track
                    objectName: "rowsMeter"
                    visible: row.meterLike
                    x: 14; y: 28
                    width: 272; height: 6; radius: 3
                    color: T.raised
                    readonly property real fillWidth: fill.width
                    readonly property color fillColor: row.toneColor
                    Rectangle {
                        id: fill
                        width: row.kind === "meter" ? row.fraction * parent.width : 0
                        height: parent.height
                        radius: 3
                        color: row.toneColor
                        visible: width > 0
                    }

                    // A stack's pieces. By place, like the rows, so one that grows is not built again.
                    Repeater {
                        model: row.pieces.length

                        Rectangle {
                            id: part
                            required property int index
                            objectName: "rowsPart"
                            readonly property var piece: row.pieces[index] || ({ tone: "", x: 0, w: 0 })
                            readonly property bool last: index === row.pieces.length - 1
                            x: piece.x
                            width: piece.w
                            height: parent.height
                            color: card.toneColor(piece.tone)
                            // Only the ends of the whole stack are round, as the track's are.
                            topLeftRadius: piece.x === 0 ? 3 : 0
                            bottomLeftRadius: piece.x === 0 ? 3 : 0
                            topRightRadius: last ? 3 : 0
                            bottomRightRadius: last ? 3 : 0
                        }
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
                    visible: !row.meterLike && row.sub !== ""
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
                    visible: row.meterLike ? row.removable : (row.button !== "" || row.removable)
                    x: 286 - width
                    y: row.meterLike ? 6 : 12
                    spacing: 6

                    Rectangle {
                        id: pressButton
                        objectName: "rowsButton"
                        visible: !row.meterLike && row.button !== ""
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
