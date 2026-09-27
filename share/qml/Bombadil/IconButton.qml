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
        // Empty (transparent) leaves the style's muted/hover/checked colors.
        icon.color: root.tone ? Theme.tone(root.tone) : "transparent"
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
