import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// 18 px rounded box; filled accent with a white check (or dash when partially checked).
T.CheckBox {
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
        readonly property bool on: control.checkState !== Qt.Unchecked

        implicitWidth: 18
        implicitHeight: 18
        x: control.text ? (control.mirrored ? control.width - width - control.rightPadding : control.leftPadding)
                        : control.leftPadding + (control.availableWidth - width) / 2
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: 5
        color: on ? (control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent)
                  : (control.down ? Theme.overlay : Theme.raised)
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
        FocusRing {
            baseRadius: 5
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
