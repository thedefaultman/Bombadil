import QtQuick
import QtQuick.Layouts
import Bombadil

// A 36 px button for this window, round ended. It is neutral, never orange (a tip is nobody's colour: orange
// means the machine is acting), and it never takes the keyboard, so Space and Enter stay the window's own
// keys. `quiet` leaves it without a fill until the pointer is over it.
Item {
    id: root

    property string text: ""
    property string icon: ""
    property bool quiet: false
    property real maxWidth: 560

    signal clicked()

    readonly property color ink: quiet && !hover.hovered ? Theme.muted : Theme.fg

    implicitHeight: Theme.controlHeight
    // A whole number of pixels: a layout that shares a row hands out whole pixels, and a width a fraction short of
    // the text's own elides a short word ("desk" as "de...").
    implicitWidth: Math.min(Math.ceil(label.implicitWidth) + (icon !== "" ? 22 : 0) + 32, maxWidth)
    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: root.clicked()

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: tap.pressed ? Theme.panel : hover.hovered ? Theme.overlay : quiet ? Theme.alpha(Theme.raised, 0) : Theme.raised
        border.width: 1
        border.color: hover.hovered ? Theme.borderActive : Theme.borderStrong
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }

    RowLayout {
        anchors { fill: parent; leftMargin: 16; rightMargin: 16 }
        spacing: 8
        Icon {
            visible: root.icon !== ""
            name: root.icon
            size: 14
            color: root.ink
        }
        Text {
            id: label
            Layout.fillWidth: true
            text: root.text
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: root.ink
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
            font.weight: Font.Medium
            verticalAlignment: Text.AlignVCenter
        }
    }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { id: tap; onTapped: root.clicked() }
}
