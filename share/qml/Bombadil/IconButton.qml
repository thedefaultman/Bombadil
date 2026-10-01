import QtQuick
import QtQuick.Controls

// A square flat button with only an icon; the tooltip says what it does.
Item {
    id: root

    property string icon
    property string tooltip
    property string tone: ""
    property int size: 32
    property bool checkable: false
    property bool checked: false
    property alias hovered: button.hovered
    property alias pressed: button.pressed

    signal clicked()

    implicitWidth: size
    implicitHeight: size
    activeFocusOnTab: false

    ToolButton {
        id: button
        anchors.fill: parent
        padding: 0
        focusPolicy: Qt.TabFocus
        display: AbstractButton.IconOnly
        checked: root.checked
        icon.source: root.icon ? Theme.icon(root.icon) : ""
        icon.width: Math.max(14, Math.round(root.size / 2))
        icon.height: Math.max(14, Math.round(root.size / 2))
        // The style's colors, spelled out: an explicitly set transparent color draws the SVG black.
        icon.color: root.tone ? Theme.tone(root.tone)
                  : checked ? Theme.accent
                  : hovered || down ? Theme.fg : Theme.muted
        Accessible.name: root.tooltip
        ToolTip.visible: hovered && root.tooltip !== ""
        ToolTip.text: root.tooltip
        ToolTip.delay: 600
        onClicked: {
            if (root.checkable)
                root.checked = !root.checked
            root.clicked()
        }
    }
}
