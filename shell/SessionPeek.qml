import QtQuick
import QtQuick.Layouts

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
    radius: 12
    color: "#f01a1d21"
    border.width: 1
    border.color: "#2a2f36"

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
                color: "#e6e8eb"
                font.pixelSize: 13
                font.weight: Font.Medium
                textFormat: Text.PlainText
            }
            Text {
                objectName: "peekState"
                text: peek.dev.stateWords(peek.s)
                color: "#8b939c"
                font.pixelSize: 12
                textFormat: Text.PlainText
            }
        }
        Text {
            objectName: "peekLines"
            Layout.fillWidth: true
            Layout.maximumWidth: 392
            visible: text !== ""
            text: peek.dev.peekLines(peek.s)
            color: "#a9b0b8"
            font.pixelSize: 12
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 5
            elide: Text.ElideRight
        }
    }
}
