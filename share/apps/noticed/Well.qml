import QtQuick
import QtQuick.Layouts
import Bombadil

// A quiet box under a row: what a preview showed, why something was found, or why a tap did not work.
Rectangle {
    id: root

    property string text
    property bool bad: false

    Layout.fillWidth: true
    visible: text !== ""
    implicitHeight: label.implicitHeight + 2 * Theme.gap
    radius: Theme.radiusSmall
    color: Theme.sunken
    border.color: Theme.border

    Caption {
        id: label
        x: Theme.gap
        y: Theme.gap
        width: root.width - 2 * Theme.gap
        text: root.text
        textFormat: Text.PlainText
        color: root.bad ? Theme.bad : Theme.fg
    }
}
