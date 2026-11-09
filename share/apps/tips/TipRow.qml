import QtQuick
import QtQuick.Layouts
import Bombadil

// One tip in the list: its line, and under it the words that ask it in muted text (or the keys, for a tip
// with no button). A tip that has been tried carries a tick and "tried". The selected row is raised, not
// orange: a tip is nobody's colour.
Item {
    id: root

    property string line: ""
    property string words: ""
    property bool tried: false
    property bool selected: false

    signal picked()

    implicitHeight: 52
    Accessible.role: Accessible.ListItem
    Accessible.name: line + (tried ? ", tried" : "")
    Accessible.selected: selected

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: root.selected ? Theme.raised : hover.hovered ? Theme.alpha(Theme.raised, 0.5) : Theme.alpha(Theme.raised, 0)
        border.width: 1
        border.color: root.selected ? Theme.borderStrong : Theme.alpha(Theme.borderStrong, 0)
    }

    ColumnLayout {
        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                  leftMargin: 12; rightMargin: 12 }
        spacing: 2
        Text {
            objectName: "rowLine"
            Layout.fillWidth: true
            text: root.line
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.smallSize
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                objectName: "rowWords"
                Layout.fillWidth: true
                text: root.words
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
            }
            Row {
                objectName: "rowTick"
                visible: root.tried
                spacing: 4
                Icon { anchors.verticalCenter: parent.verticalCenter; name: "check"; size: 12; color: Theme.muted }
                Text {
                    text: "tried"
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                }
            }
        }
    }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: root.picked() }
}
