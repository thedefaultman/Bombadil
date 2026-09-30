import QtQuick
import Bombadil

// A quiet line of text at the foot of the window that does something when pressed.
Text {
    id: root

    signal clicked()

    color: hover.hovered || activeFocus ? Theme.fg : Theme.muted
    font.family: Theme.fontFamily
    font.pixelSize: Theme.captionSize
    font.underline: hover.hovered || activeFocus
    textFormat: Text.PlainText
    opacity: enabled ? 1 : 0.4
    activeFocusOnTab: true
    padding: 2

    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: root.clicked()

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: root.clicked() }
    Keys.onReturnPressed: root.clicked()
    Keys.onEnterPressed: root.clicked()
    Keys.onSpacePressed: root.clicked()
}
