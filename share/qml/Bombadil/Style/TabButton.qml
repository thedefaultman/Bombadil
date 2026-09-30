import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// A tab: muted label that turns bright when hovered or current (TabBar draws the underline).
T.TabButton {
    id: control

    readonly property color ink: checked || hovered || down ? Theme.fg : Theme.muted

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 8
    horizontalPadding: 14
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
        defaultIconColor: control.ink
        text: control.text
        font: control.font
        color: control.ink
    }

    background: Item {
        implicitHeight: 40

        FocusRing {
            inset: 4
            visible: control.visualFocus
        }
    }
}
