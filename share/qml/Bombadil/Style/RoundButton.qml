import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// A circular button, usually icon-only. Colors follow Button (raised, highlighted, flat).
T.RoundButton {
    id: control

    readonly property color ink: highlighted ? Theme.accentFg : Theme.fg
    readonly property color fill: {
        if (highlighted)
            return down ? Theme.accentPressed : hovered ? Theme.accentHover : Theme.accent
        if (checked)
            return Theme.accentSoft
        if (flat)
            return down ? Theme.overlay : hovered ? Theme.raised : Theme.alpha(Theme.raised, 0)
        return down ? Theme.panel : hovered ? Theme.overlay : Theme.raised
    }

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 10
    spacing: 8
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

    background: Rectangle {
        implicitWidth: Theme.controlHeight
        implicitHeight: Theme.controlHeight
        radius: control.radius
        color: control.fill
        border.width: control.highlighted || control.flat ? 0 : 1
        border.color: control.checked ? Theme.alpha(Theme.accent, 0.6) : Theme.borderStrong

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        FocusRing {
            baseRadius: control.radius
            visible: control.visualFocus
        }
    }
}
