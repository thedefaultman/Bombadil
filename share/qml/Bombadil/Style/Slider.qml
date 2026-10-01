import QtQuick
import QtQuick.Templates as T
import Bombadil

// 4 px track filled with accent up to a light round handle.
T.Slider {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitHandleWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitHandleHeight + topPadding + bottomPadding)

    padding: 6
    opacity: enabled ? 1 : 0.4

    handle: Rectangle {
        x: control.leftPadding + (control.horizontal ? control.visualPosition * (control.availableWidth - width)
                                                     : (control.availableWidth - width) / 2)
        y: control.topPadding + (control.horizontal ? (control.availableHeight - height) / 2
                                                    : control.visualPosition * (control.availableHeight - height))
        implicitWidth: 16
        implicitHeight: 16
        radius: 8
        color: control.pressed ? Qt.darker(Theme.fg, 1.08) : Theme.fg
        border.width: 3
        border.color: Theme.accent

        Rectangle {
            anchors.fill: parent
            anchors.margins: -4
            radius: width / 2
            z: -1
            color: Theme.accentSoft
            opacity: control.pressed || control.hovered ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
        FocusRing {
            baseRadius: 8
            visible: control.visualFocus
        }
    }

    // The track stops at the handle's edges, so nothing shows through it at reduced opacity.
    background: Item {
        readonly property real lo: control.horizontal ? control.handle.x - x : control.handle.y - y
        readonly property real hi: lo + (control.horizontal ? control.handle.width : control.handle.height)
        // Horizontal fills from the left (right when mirrored); vertical fills from the bottom.
        readonly property bool fillLow: control.horizontal && !control.mirrored

        x: control.leftPadding + (control.horizontal ? 0 : (control.availableWidth - width) / 2)
        y: control.topPadding + (control.horizontal ? (control.availableHeight - height) / 2 : 0)
        implicitWidth: control.horizontal ? 200 : 4
        implicitHeight: control.horizontal ? 4 : 200
        width: control.horizontal ? control.availableWidth : implicitWidth
        height: control.horizontal ? implicitHeight : control.availableHeight

        Rectangle {
            width: control.horizontal ? Math.max(0, parent.lo) : parent.width
            height: control.horizontal ? parent.height : Math.max(0, parent.lo)
            radius: 2
            color: parent.fillLow ? Theme.accent : Theme.alpha(Theme.fg, 0.12)
        }
        Rectangle {
            x: control.horizontal ? parent.hi : 0
            y: control.horizontal ? 0 : parent.hi
            width: control.horizontal ? Math.max(0, parent.width - parent.hi) : parent.width
            height: control.horizontal ? parent.height : Math.max(0, parent.height - parent.hi)
            radius: 2
            color: parent.fillLow ? Theme.alpha(Theme.fg, 0.12) : Theme.accent
        }
    }
}
