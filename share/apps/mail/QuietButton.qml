import QtQuick
import QtQuick.Controls
import Bombadil

// A button in the window's neutral colours: raised, a hairline, white ink. `tone: "bad"` draws the words red, for
// what cannot be taken back. A toggle that is on (`checked`) has a stronger outline and a tick. Nothing here is
// orange; that is Send's, and only Send's.
FocusScope {
    id: root

    property string text: ""
    property string icon: ""
    property string tone: ""
    property string tooltip: ""
    property bool checked: false
    property bool flat: false
    property bool compact: false
    readonly property bool hovered: hover.hovered
    readonly property bool down: tap.pressed
    readonly property color ink: tone === "bad" ? Theme.badInk : Theme.fg

    signal clicked()

    implicitHeight: compact ? 28 : 32
    implicitWidth: row.implicitWidth + (text !== "" ? (compact ? 20 : 28) : 14)
    activeFocusOnTab: enabled
    opacity: enabled ? 1 : 0.4
    Accessible.role: Accessible.Button
    Accessible.name: text || tooltip
    Accessible.onPressAction: root.clicked()

    HoverHandler { id: hover }
    TapHandler {
        id: tap
        enabled: root.enabled
        onTapped: root.clicked()
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: root.down ? Theme.panel : root.hovered ? Theme.overlay : root.flat && !root.checked ? "transparent" : Theme.raised
        border.width: root.activeFocus ? 1.5 : 1
        border.color: root.activeFocus ? Theme.fg : root.checked ? Theme.muted
                    : root.flat ? "transparent" : Theme.borderStrong
    }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Icon {
            visible: name !== ""
            anchors.verticalCenter: parent.verticalCenter
            name: root.checked && root.text !== "" ? "check" : root.icon
            size: 15
            color: root.ink
        }
        PlainText {
            visible: root.text !== ""
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            color: root.ink
            font.weight: Font.Medium
        }
    }

    Keys.onReturnPressed: root.clicked()
    Keys.onEnterPressed: root.clicked()
    Keys.onSpacePressed: root.clicked()

    ToolTip.visible: hover.hovered && root.tooltip !== ""
    ToolTip.text: root.tooltip
    ToolTip.delay: 600
}
