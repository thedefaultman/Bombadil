import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// Flat square icon button for toolbars and headers: muted icon, a raised square on hover,
// accent when checked.
T.ToolButton {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 8
    horizontalPadding: text && display !== T.AbstractButton.IconOnly ? 10 : 8
    spacing: 6
    font.weight: Font.Medium
    opacity: enabled ? 1 : 0.4

    icon.width: 16
    icon.height: 16

    contentItem: IconLabel {
        spacing: control.spacing
        mirrored: control.mirrored
        display: control.display
        icon: control.icon
        defaultIconColor: control.checked || control.highlighted ? Theme.accent
                          : control.hovered || control.down || control.text ? Theme.fg : Theme.muted
        text: control.text
        font: control.font
        color: control.checked || control.highlighted ? Theme.accent : Theme.fg
    }

    background: Rectangle {
        implicitWidth: 32
        implicitHeight: 32
        radius: Theme.radiusSmall
        color: control.down ? Theme.overlay
             : control.checked ? Theme.accentSoft
             : control.hovered ? Theme.raised : Theme.alpha(Theme.raised, 0)

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        FocusRing { visible: control.visualFocus }
    }
}
