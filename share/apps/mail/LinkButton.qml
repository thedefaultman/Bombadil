import QtQuick
import Bombadil

// A quiet link that opens something on the web: white words, an arrow out, an underline under the pointer.
FocusScope {
    id: root

    property string text: ""
    property string icon: "external-link"

    signal clicked()

    implicitWidth: row.implicitWidth
    implicitHeight: 24
    activeFocusOnTab: true
    Accessible.role: Accessible.Link
    Accessible.name: text
    Accessible.onPressAction: root.clicked()

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: root.clicked() }

    Row {
        id: row
        anchors.verticalCenter: parent.verticalCenter
        spacing: 5
        PlainText {
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            font.pixelSize: Theme.smallSize
            font.underline: hover.hovered || root.activeFocus
            color: hover.hovered ? Theme.fg : Theme.muted
        }
        Icon {
            visible: root.icon !== ""
            anchors.verticalCenter: parent.verticalCenter
            name: root.icon
            size: 13
            color: hover.hovered ? Theme.fg : Theme.muted
        }
    }

    Keys.onReturnPressed: root.clicked()
    Keys.onEnterPressed: root.clicked()
    Keys.onSpacePressed: root.clicked()
}
