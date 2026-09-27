import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// 36x20 pill; the knob slides right and the track turns accent when on.
T.Switch {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)

    padding: 6
    spacing: 10
    opacity: enabled ? 1 : 0.4

    indicator: Rectangle {
        implicitWidth: 36
        implicitHeight: 20
        x: control.text ? (control.mirrored ? control.width - width - control.rightPadding : control.leftPadding)
                        : control.leftPadding + (control.availableWidth - width) / 2
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: height / 2
        color: control.checked ? (control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent)
                               : (control.hovered || control.down ? Qt.lighter(Theme.borderStrong, 1.2) : Theme.borderStrong)

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
        FocusRing {
            baseRadius: parent.radius
            visible: control.visualFocus
        }
    }

    contentItem: CheckLabel {
        leftPadding: control.indicator && !control.mirrored ? control.indicator.width + control.spacing : 0
        rightPadding: control.indicator && control.mirrored ? control.indicator.width + control.spacing : 0
        text: control.text
        font: control.font
        color: Theme.fg
        verticalAlignment: Text.AlignVCenter
    }
}
