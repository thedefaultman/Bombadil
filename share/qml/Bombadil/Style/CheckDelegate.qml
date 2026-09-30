import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// ItemDelegate row with a CheckBox indicator at the right.
T.CheckDelegate {
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
        readonly property bool on: control.checkState !== Qt.Unchecked

        implicitWidth: 18
        implicitHeight: 18
        x: control.mirrored ? control.leftPadding : control.width - width - control.rightPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: 5
        color: on ? (control.down ? Theme.accentPressed : Theme.accent) : Theme.raised
        border.width: on ? 0 : 1.5
        border.color: control.hovered ? Theme.muted : Theme.faint

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        IconImage {
            anchors.centerIn: parent
            width: 14
            height: 14
            sourceSize: Qt.size(14, 14)
            source: Theme.icon("check")
            color: Theme.accentFg
            visible: control.checkState === Qt.Checked
        }
        Rectangle {
            anchors.centerIn: parent
            width: 10
            height: 2
            radius: 1
            color: Theme.accentFg
            visible: control.checkState === Qt.PartiallyChecked
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
