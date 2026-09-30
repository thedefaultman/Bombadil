import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// ItemDelegate row with a RadioButton indicator at the right.
T.RadioDelegate {
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

    indicator: Rectangle {
        implicitWidth: 18
        implicitHeight: 18
        x: control.mirrored ? control.leftPadding : control.width - width - control.rightPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: width / 2
        color: control.checked ? (control.down ? Theme.accentPressed : Theme.accent) : Theme.raised
        border.width: control.checked ? 0 : 1.5
        border.color: control.hovered ? Theme.muted : Theme.faint

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        Rectangle {
            anchors.centerIn: parent
            width: 6
            height: 6
            radius: 3
            color: Theme.accentFg
            scale: control.checked ? 1 : 0
            Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
        }
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
