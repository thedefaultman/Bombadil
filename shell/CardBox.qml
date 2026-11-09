import QtQuick
import Bombadil as Kit

// A box the person writes in, on a card: the reply, or one field of a task. Plain text, in the kit's own colours (the
// orange belongs to the button that sends, so the selection is a light wash of the ink and not the accent).
//
// Enter puts a new line in a box of several lines and does nothing in a box of one: nothing here sends, only the
// card's button does. Tab goes on to the next thing on the card (it is not a character of a message). Esc closes
// the card (`escaped`). A click asks for the keyboard (`taken`): the bar does not hold it for a card until asked.
//
// `setText` puts words in from outside and is not an edit. `edited` is the person typing, pasting or deleting.
// A box that cannot take an edit (the draft is being sent, or was) is read-only, and shows what is there.
FocusScope {
    id: box
    objectName: "cardBox"

    property alias text: area.text
    property alias field: area
    property bool multiline: true
    property string label: ""
    property string placeholder: ""
    property bool readOnly: false
    property int maxLines: 8
    property int minLines: multiline ? 3 : 1
    property bool loading: false
    readonly property real lineHeight: metrics.height

    signal edited()
    signal escaped()
    signal taken()

    function setText(value) {
        loading = true
        area.text = value
        loading = false
    }
    // A reply starts where the words end, not above them.
    function focusEnd() {
        area.forceActiveFocus()
        area.cursorPosition = area.length
    }

    implicitWidth: 220
    implicitHeight: Math.max(minLines, Math.min(maxLines, Math.round(area.contentHeight / lineHeight))) * lineHeight
                    + 2 * Kit.Theme.gapSmall + Kit.Theme.gapSmall
    activeFocusOnTab: true
    Accessible.role: Accessible.EditableText
    Accessible.name: box.label || box.placeholder

    FontMetrics {
        id: metrics
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.textSize
    }
    HoverHandler { id: hover }
    TapHandler { onTapped: box.taken() }

    Rectangle {
        anchors.fill: parent
        radius: Kit.Theme.radiusSmall
        color: Kit.Theme.sunken
        border.width: area.activeFocus ? 1.5 : 1
        border.color: area.activeFocus ? Kit.Theme.borderActive : hover.hovered ? Kit.Theme.borderStrong : Kit.Theme.border
        opacity: box.readOnly ? 0.7 : 1
    }

    Flickable {
        id: flick
        anchors { fill: parent; margins: 1 }
        contentWidth: width
        contentHeight: Math.max(height, area.contentHeight + 2 * Kit.Theme.gapSmall)
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        function follow(r) {
            if (r.y < contentY + Kit.Theme.gapSmall)
                contentY = Math.max(0, r.y - Kit.Theme.gapSmall)
            else if (r.y + r.height > contentY + height - Kit.Theme.gapSmall)
                contentY = r.y + r.height - height + Kit.Theme.gapSmall
        }

        TextEdit {
            id: area
            x: Kit.Theme.gap - 1
            y: Kit.Theme.gapSmall
            width: flick.width - 2 * Kit.Theme.gap + 2
            height: Math.max(contentHeight, flick.height - 2 * Kit.Theme.gapSmall)
            wrapMode: TextEdit.Wrap
            textFormat: TextEdit.PlainText
            selectByMouse: true
            readOnly: box.readOnly
            color: Kit.Theme.fg
            selectionColor: Kit.Theme.alpha(Kit.Theme.fg, 0.28)
            selectedTextColor: Kit.Theme.fg
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.textSize
            focus: true
            Accessible.name: box.label || box.placeholder

            onCursorRectangleChanged: flick.follow(cursorRectangle)
            onTextChanged: {
                if (!box.multiline && text.indexOf("\n") >= 0) {   // pasted lines are one line
                    const at = cursorPosition
                    text = text.replace(/\s*\n+\s*/g, " ")
                    cursorPosition = Math.min(at, length)
                    return
                }
                if (!box.loading) box.edited()
            }
            Keys.onReturnPressed: event => { event.accepted = !box.multiline }
            Keys.onEnterPressed: event => { event.accepted = !box.multiline }
            Keys.onEscapePressed: box.escaped()
            Keys.onTabPressed: event => {
                box.nextItemInFocusChain(true).forceActiveFocus()
                event.accepted = true
            }
            Keys.onBacktabPressed: event => {
                box.nextItemInFocusChain(false).forceActiveFocus()
                event.accepted = true
            }
        }
    }
    ScrollThumb { flick: flick }

    Text {
        objectName: "boxPlaceholder"
        visible: area.text === "" && area.preeditText === ""
        x: Kit.Theme.gap
        y: Kit.Theme.gapSmall + 1
        width: parent.width - 2 * Kit.Theme.gap
        text: box.placeholder
        color: Kit.Theme.faint
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.textSize
        textFormat: Text.PlainText
        elide: Text.ElideRight
    }
}
