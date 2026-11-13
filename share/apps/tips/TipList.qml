import QtQuick
import QtQuick.Controls
import Bombadil

// The list on the right: Start here, Work, Code, This machine and, when an update brought tips the person has
// not seen, New. It shows what the backend's model holds, in the order it holds it. A tick that arrives (a tip
// was tried) changes one row and leaves the list where it was.
ListView {
    id: view

    property int current: -1       // the row of the selected tip

    signal picked(string id)

    // The selected row comes into view, and the heading above the first tip of a section comes with it. Every
    // row exists (cacheBuffer), so the positions are exact; positionViewAtIndex twice in one turn is not.
    function reveal() {
        if (current < 0)
            return
        const row = itemAtIndex(current)
        if (!row) {
            positionViewAtIndex(current, ListView.Contain)
            return
        }
        const above = itemAtIndex(current - 1)
        const top = above && above.kind === "section" ? above.y : row.y
        const bottom = row.y + row.height
        if (top < contentY)
            contentY = top
        else if (bottom > contentY + height)
            contentY = bottom - height
    }

    clip: true
    spacing: 2
    cacheBuffer: 4000                // about thirty short rows: all of them exist, so every position is exact
    boundsBehavior: Flickable.StopAtBounds
    keyNavigationEnabled: false      // Up and Down are the window's: they select a tip and play it
    activeFocusOnTab: false
    model: backend.model
    ScrollBar.vertical: ScrollBar {}

    onCurrentChanged: Qt.callLater(reveal)
    Connections {
        target: view.model
        function onModelReset() { Qt.callLater(view.reveal) }
    }

    delegate: Item {
        id: cell
        required property int index
        required property string kind
        required property string key
        required property string title
        required property string line
        required property string words
        required property bool tried

        width: ListView.view.width
        height: kind === "section" ? 34 : 52

        Text {
            objectName: "section"
            visible: cell.kind === "section"
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 12; bottomMargin: 6 }
            text: cell.title
            textFormat: Text.PlainText
            color: Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
            font.weight: Font.DemiBold
        }
        TipRow {
            objectName: "tipRow"
            visible: cell.kind === "tip"
            anchors.fill: parent
            line: cell.line
            words: cell.words
            tried: cell.tried
            selected: cell.index === view.current
            onPicked: view.picked(cell.key)
        }
    }
}
