import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// A field of several lines in the window's own neutral colours and its own font: the body of a reply, and (with
// `maxLines`) an address list or a subject that wraps instead of hiding its end. The kit's Editor is not used for
// this: it draws its selection in the accent and its words in the mono font, and in this window the accent is Send's.
//
// `readOnly` is real: a box that cannot take typing (the draft is being sent) takes none, rather than showing words
// the draft does not have.
FocusScope {
    id: root

    property alias text: area.text
    property alias readOnly: area.readOnly
    property alias field: area
    property string placeholder: ""
    property string label: ""
    property int maxLines: 0            // 0: as tall as it is given; n: as tall as its words, at most n lines
    property bool lineBreaks: true      // false: Return does not start a line (it is an address list, a subject)
    readonly property real lineHeight: fontMetrics.height

    signal edited()                     // the words changed (a text set from outside changes them too: the owner says)
    signal accepted()                   // Return, in a field with no line breaks

    function clear() {
        area.clear()
    }

    implicitWidth: 220
    implicitHeight: maxLines > 0
        ? Math.max(1, Math.min(Math.round(area.contentHeight / lineHeight), maxLines)) * lineHeight + 16
        : 96
    activeFocusOnTab: true
    Accessible.role: Accessible.EditableText
    Accessible.name: root.label || root.placeholder

    FontMetrics {
        id: fontMetrics
        font.family: Theme.fontFamily
        font.pixelSize: Theme.textSize
    }
    HoverHandler { id: hover }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: Theme.raised
        border.width: area.activeFocus ? 1.5 : 1
        border.color: area.activeFocus ? Theme.borderActive : hover.hovered ? Theme.borderStrong : Theme.border
        opacity: root.enabled ? 1 : 0.5
    }

    Flickable {
        id: flick
        anchors { fill: parent; margins: 1 }
        contentWidth: width
        contentHeight: Math.max(height, area.contentHeight + 16)
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        function follow(r) {
            if (r.y < contentY + 8)
                contentY = Math.max(0, r.y - 8)
            else if (r.y + r.height > contentY + height - 8)
                contentY = r.y + r.height - height + 8
        }

        TextEdit {
            id: area
            x: 11
            y: 8
            width: flick.width - 22
            height: Math.max(contentHeight, flick.height - 16)
            wrapMode: TextEdit.Wrap
            textFormat: TextEdit.PlainText
            selectByMouse: true
            color: Theme.fg
            // The selection is a light wash of the ink: the accent in this window belongs to Send.
            selectionColor: Theme.alpha(Theme.fg, 0.28)
            selectedTextColor: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
            focus: true
            enabled: root.enabled
            Accessible.name: root.label || root.placeholder

            onCursorRectangleChanged: flick.follow(cursorRectangle)
            onTextChanged: {
                if (!root.lineBreaks && text.indexOf("\n") >= 0) {   // pasted lines are one line
                    const at = cursorPosition
                    text = text.replace(/\s*\n+\s*/g, " ")
                    cursorPosition = Math.min(at, length)
                    return
                }
                root.edited()
            }
            Keys.onReturnPressed: event => {
                if (root.lineBreaks)
                    event.accepted = false
                else
                    root.accepted()
            }
            Keys.onEnterPressed: event => {
                if (root.lineBreaks)
                    event.accepted = false
                else
                    root.accepted()
            }
            // Tab moves on, as in a form; it is not a character of a mail.
            Keys.onTabPressed: event => {
                root.nextItemInFocusChain(true).forceActiveFocus()
                event.accepted = true
            }
            Keys.onBacktabPressed: event => {
                root.nextItemInFocusChain(false).forceActiveFocus()
                event.accepted = true
            }
        }
    }

    PlainText {
        visible: area.text === "" && area.preeditText === ""
        x: 12
        y: 8
        width: parent.width - 24
        text: root.placeholder
        color: Theme.faint
        elide: Text.ElideRight
    }
}
