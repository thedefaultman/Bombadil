import QtQuick
import QtQuick.Templates as T
import Bombadil

// Slider with two handles; the accent fill spans the selected range.
T.RangeSlider {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            first.implicitHandleWidth + leftPadding + rightPadding,
                            second.implicitHandleWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             first.implicitHandleHeight + topPadding + bottomPadding,
                             second.implicitHandleHeight + topPadding + bottomPadding)

    padding: 6
    opacity: enabled ? 1 : 0.4

    first.handle: Rectangle {
        x: control.leftPadding + (control.horizontal ? control.first.visualPosition * (control.availableWidth - width)
                                                     : (control.availableWidth - width) / 2)
        y: control.topPadding + (control.horizontal ? (control.availableHeight - height) / 2
                                                    : control.first.visualPosition * (control.availableHeight - height))
        implicitWidth: 16
        implicitHeight: 16
        radius: 8
        color: control.first.pressed ? Qt.darker(Theme.fg, 1.08) : Theme.fg
        border.width: 3
        border.color: Theme.accent

        Rectangle {
            anchors.fill: parent
            anchors.margins: -4
            radius: width / 2
            z: -1
            color: Theme.accentSoft
            opacity: control.first.pressed || control.first.hovered ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
        FocusRing {
            baseRadius: 8
            visible: control.visualFocus && parent.activeFocus
        }
    }

    second.handle: Rectangle {
        x: control.leftPadding + (control.horizontal ? control.second.visualPosition * (control.availableWidth - width)
                                                     : (control.availableWidth - width) / 2)
        y: control.topPadding + (control.horizontal ? (control.availableHeight - height) / 2
                                                    : control.second.visualPosition * (control.availableHeight - height))
        implicitWidth: 16
        implicitHeight: 16
        radius: 8
        color: control.second.pressed ? Qt.darker(Theme.fg, 1.08) : Theme.fg
        border.width: 3
        border.color: Theme.accent

        Rectangle {
            anchors.fill: parent
            anchors.margins: -4
            radius: width / 2
            z: -1
            color: Theme.accentSoft
            opacity: control.second.pressed || control.second.hovered ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
        FocusRing {
            baseRadius: 8
            visible: control.visualFocus && parent.activeFocus
        }
    }

    // The track stops at the handles' edges, so nothing shows through them at reduced opacity.
    background: Item {
        readonly property real a: control.horizontal ? control.first.handle.x - x : control.first.handle.y - y
        readonly property real b: control.horizontal ? control.second.handle.x - x : control.second.handle.y - y
        readonly property real size: control.horizontal ? control.first.handle.width : control.first.handle.height
        readonly property real lo: Math.min(a, b)
        readonly property real hi: Math.max(a, b)

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
            color: Theme.alpha(Theme.fg, 0.12)
        }
        Rectangle {
            x: control.horizontal ? parent.lo + parent.size : 0
            y: control.horizontal ? 0 : parent.lo + parent.size
            width: control.horizontal ? Math.max(0, parent.hi - parent.lo - parent.size) : parent.width
            height: control.horizontal ? parent.height : Math.max(0, parent.hi - parent.lo - parent.size)
            color: Theme.accent
        }
        Rectangle {
            x: control.horizontal ? parent.hi + parent.size : 0
            y: control.horizontal ? 0 : parent.hi + parent.size
            width: control.horizontal ? Math.max(0, parent.width - parent.hi - parent.size) : parent.width
            height: control.horizontal ? parent.height : Math.max(0, parent.height - parent.hi - parent.size)
            radius: 2
            color: Theme.alpha(Theme.fg, 0.12)
        }
    }
}
