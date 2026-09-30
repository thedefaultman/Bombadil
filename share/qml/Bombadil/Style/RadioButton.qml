import QtQuick
import QtQuick.Templates as T
import QtQuick.Controls.impl
import Bombadil

// 18 px circle; checked is an accent disc with a white dot.
T.RadioButton {
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
        implicitWidth: 18
        implicitHeight: 18
        x: control.text ? (control.mirrored ? control.width - width - control.rightPadding : control.leftPadding)
                        : control.leftPadding + (control.availableWidth - width) / 2
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: width / 2
        color: control.checked ? (control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent)
                               : (control.down ? Theme.overlay : Theme.raised)
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
