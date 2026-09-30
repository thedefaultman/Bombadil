import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// A 44 px list row: muted icon, text; hover wash, `highlighted` (selected) is a raised fill
// with an accent bar at the left.
T.ItemDelegate {
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
        spacing: control.spacing
        mirrored: control.mirrored
        display: control.display
        alignment: control.display === IconLabel.IconOnly || control.display === IconLabel.TextUnderIcon
                   ? Qt.AlignCenter : Qt.AlignLeft
        icon: control.icon
        defaultIconColor: control.highlighted ? Theme.fg : Theme.muted
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

        Rectangle {
            x: 0
            anchors.verticalCenter: parent.verticalCenter
            width: 3
            height: parent.height - 20
            radius: 1.5
            color: Theme.accent
            visible: control.highlighted
        }
        FocusRing {
            inset: 1
            visible: control.visualFocus
        }
    }
}
