import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// ItemDelegate row with a Switch at the right, for settings lists.
T.SwitchDelegate {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)

    padding: 8
    horizontalPadding: 12
    spacing: 10
    opacity: enabled ? 1 : 0.4

    icon.width: 16
    icon.height: 16

    indicator: Rectangle {
        implicitWidth: 36
        implicitHeight: 20
        x: control.text ? (control.mirrored ? control.leftPadding : control.width - width - control.rightPadding)
                        : control.leftPadding + (control.availableWidth - width) / 2
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: height / 2
        color: control.checked ? (control.down ? Theme.accentPressed : Theme.accent)
                               : (control.hovered ? Qt.lighter(Theme.borderStrong, 1.2) : Theme.borderStrong)

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        Rectangle {
            x: 3 + Math.max(0, Math.min(1, control.visualPosition)) * (parent.width - width - 6)
            y: 3
            width: 14
            height: 14
            radius: 7
            color: control.checked ? Theme.accentFg : Theme.fg

            Behavior on x {
                enabled: !control.down
                NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic }
            }
        }
    }

    contentItem: IconLabel {
        leftPadding: control.mirrored ? control.indicator.width + control.spacing : 0
        rightPadding: !control.mirrored ? control.indicator.width + control.spacing : 0
        spacing: control.spacing
        mirrored: control.mirrored
        display: control.display
        alignment: control.display === IconLabel.IconOnly || control.display === IconLabel.TextUnderIcon
                   ? Qt.AlignCenter : Qt.AlignLeft
        icon: control.icon
        defaultIconColor: Theme.muted
        text: control.text
        font: control.font
        color: Theme.fg
    }

    background: Rectangle {
        implicitWidth: 120
        implicitHeight: Theme.rowHeight
        radius: Theme.radiusSmall
        color: control.down ? Theme.overlay
             : control.highlighted ? Theme.raised
             : control.hovered ? Theme.alpha(Theme.fg, 0.04) : Theme.alpha(Theme.fg, 0)

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        FocusRing {
            inset: 1
            visible: control.visualFocus
        }
    }
}
