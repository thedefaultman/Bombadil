import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The body of a list card (the unread messages, "Connected here"): a row each, with a mark for how it is, a title
// and a line under it, when, and up to two buttons. A row that opens something opens it on a click or on Enter.
//
// The mark is in the kit's colours and is never the only sign (each has a shape): ok a tick in green, warn a triangle
// in amber, bad a cross in red, new a dot in orange (something that just appeared), gone a dash, dim and struck
// through, active a ring in blue. A button asks agentd for its action on that row (`card_action`); a row that
// opens a card or a page asks to open it. Nothing here changes anything itself.
FocusScope {
    id: face
    objectName: "listFace"

    required property var pill
    property var card: null
    property int maxHeight: 440

    // A list from JSON, or one a model handed on (a row's own `actions` arrive as a sequence, not an Array).
    function _list(v) {
        return v && typeof v !== "string" && v.length !== undefined ? Array.prototype.slice.call(v) : []
    }

    property var _rows: []
    readonly property string _sig: JSON.stringify(card ? _list(card.rows) : [])
    function _refresh() { _rows = card ? _list(card.rows) : [] }
    on_SigChanged: _refresh()
    Component.onCompleted: _refresh()

    function _tone(state) {
        switch (state) {
        case "warn": return Kit.Theme.warn
        case "bad": return Kit.Theme.bad
        case "new": return Kit.Theme.accent
        case "active": return Kit.Theme.info
        case "gone": return Kit.Theme.faint
        default: return Kit.Theme.good
        }
    }
    function _mark(state) {
        switch (state) {
        case "warn": return "alert-triangle"
        case "bad": return "x"
        case "gone": return "minus"
        case "active": return "circle"
        case "new": return ""
        default: return "check"
        }
    }
    // What a click on the row does: a card is opened by agentd (it knows what the row stands for); a page or a
    // file is opened as a box of a picture opens it.
    function open(row) {
        if (!row.opens) return
        if (row.opens.kind === "card") face.pill.cardAction(face.card.id, "open", row.id)
        else face.pill.openThing(row.opens)
    }

    implicitHeight: scroll.implicitHeight
    Keys.onEscapePressed: face.pill.dismissCard()

    CardScroll {
        id: scroll
        anchors { left: parent.left; right: parent.right; top: parent.top }
        maxHeight: face.maxHeight
        spacing: 2

        Repeater {
            model: face._rows
            Rectangle {
                id: row
                required property var modelData
                readonly property bool openable: !!modelData.opens
                readonly property string rowState: modelData.state || "ok"
                objectName: "listRow"
                Layout.fillWidth: true
                implicitHeight: Math.max(Kit.Theme.rowHeight, line.implicitHeight + 2 * Kit.Theme.gapSmall)
                radius: Kit.Theme.radiusSmall
                color: area.hovered && openable ? Kit.Theme.raised : "transparent"
                border.width: activeFocus ? Kit.Theme.focusRing : 0
                border.color: Kit.Theme.borderActive
                activeFocusOnTab: openable
                Accessible.role: openable ? Accessible.Button : Accessible.StaticText
                Accessible.name: modelData.title + (modelData.sub ? ", " + modelData.sub : "")
                              + (modelData.when ? ", " + modelData.when : "")
                opacity: rowState === "gone" ? 0.7 : 1

                HoverHandler { id: area; cursorShape: row.openable ? Qt.PointingHandCursor : Qt.ArrowCursor }
                TapHandler {
                    enabled: row.openable
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onTapped: face.open(row.modelData)
                }
                Keys.onReturnPressed: if (openable) face.open(modelData)
                Keys.onEnterPressed: if (openable) face.open(modelData)
                Keys.onSpacePressed: if (openable) face.open(modelData)

                RowLayout {
                    id: line
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                              leftMargin: Kit.Theme.gap; rightMargin: Kit.Theme.gap }
                    spacing: Kit.Theme.gap

                    // The mark: a shape and a colour.
                    Item {
                        Layout.preferredWidth: Kit.Theme.textSize + 2
                        Layout.preferredHeight: Kit.Theme.textSize + 2
                        Layout.alignment: Qt.AlignVCenter
                        Rectangle {
                            objectName: "stateDot"
                            visible: row.rowState === "new"
                            anchors.centerIn: parent
                            width: Kit.Theme.gapSmall + 2; height: width; radius: width / 2
                            color: face._tone(row.rowState)
                        }
                        Kit.Icon {
                            objectName: "stateMark"
                            visible: row.rowState !== "new"
                            anchors.centerIn: parent
                            name: face._mark(row.rowState)
                            size: parent.width
                            color: face._tone(row.rowState)
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            objectName: "rowTitle"
                            Layout.fillWidth: true
                            text: row.modelData.title
                            color: row.rowState === "gone" ? Kit.Theme.muted : Kit.Theme.fg
                            font.family: Kit.Theme.fontFamily
                            font.pixelSize: Kit.Theme.textSize
                            font.weight: row.rowState === "new" ? Font.DemiBold : Font.Medium
                            font.strikeout: row.rowState === "gone"
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                        }
                        Text {
                            objectName: "rowSub"
                            visible: text !== ""
                            Layout.fillWidth: true
                            text: row.modelData.sub || ""
                            color: Kit.Theme.muted
                            font.family: Kit.Theme.fontFamily
                            font.pixelSize: Kit.Theme.smallSize
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                    }
                    Text {
                        objectName: "rowWhen"
                        visible: text !== ""
                        Layout.alignment: Qt.AlignVCenter
                        text: row.modelData.when || ""
                        color: Kit.Theme.muted
                        font.family: Kit.Theme.fontFamily
                        font.pixelSize: Kit.Theme.captionSize
                        textFormat: Text.PlainText
                    }
                    Repeater {
                        model: face._list(row.modelData.actions)
                        CardButton {
                            required property var modelData
                            objectName: "rowAction"
                            Layout.alignment: Qt.AlignVCenter
                            compact: true
                            tone: modelData.style === "primary" ? "primary" : "quiet"
                            label: modelData.label
                            onActivated: face.pill.cardAction(face.card.id, modelData.id, row.modelData.id)
                        }
                    }
                }
            }
        }
    }
}
