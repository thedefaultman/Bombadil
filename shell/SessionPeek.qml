import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// Hovering a session's dot: its name, what it is doing and its last few plain lines, built
// from its hooks by agentd, never by a model. It opens nothing.
Rectangle {
    id: peek
    required property var dev
    readonly property var s: dev.peekSession

    objectName: "sessionPeek"
    visible: s !== null
    implicitWidth: Math.min(420, Math.max(220, body.implicitWidth + 28))
    implicitHeight: body.implicitHeight + 20
    radius: Kit.Theme.radius
    color: Kit.Theme.glassPill
    border.width: 1
    border.color: Kit.Theme.border

    ColumnLayout {
        id: body
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12; topMargin: 10 }
        spacing: 4
        RowLayout {
            spacing: 8
            Rectangle { width: 8; height: 8; radius: 4; color: peek.dev.color(peek.s) }
            Text {
                objectName: "peekTitle"
                text: peek.s ? peek.s.title : ""
                color: Kit.Theme.fg
                font.family: Kit.Theme.fontFamily
                font.pixelSize: Kit.Theme.smallSize
                font.weight: Font.Medium
                textFormat: Text.PlainText
            }
            Text {
                objectName: "peekState"
                text: peek.dev.stateWords(peek.s)
                color: Kit.Theme.muted
                font.family: Kit.Theme.fontFamily
                font.pixelSize: Kit.Theme.captionSize
                textFormat: Text.PlainText
            }
        }
        Text {
            objectName: "peekLines"
            Layout.fillWidth: true
            Layout.maximumWidth: 392
            visible: text !== ""
            text: peek.dev.peekLines(peek.s)
            color: Kit.Theme.muted
            font.family: Kit.Theme.fontFamily
            font.pixelSize: Kit.Theme.captionSize
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 5
            elide: Text.ElideRight
        }
    }
}
