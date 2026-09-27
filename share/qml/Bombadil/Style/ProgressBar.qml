import QtQuick
import QtQuick.Templates as T
import Bombadil

// 6 px rounded track with an accent fill; indeterminate sweeps a short segment across.
T.ProgressBar {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    opacity: enabled ? 1 : 0.4

    contentItem: Item {
        id: track

        property real sweep: 0

        implicitWidth: 200
        implicitHeight: 6
        scale: control.mirrored ? -1 : 1

        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: height / 2
            color: Theme.accent
            visible: !control.indeterminate && width > 0

            Behavior on width {
                enabled: control.visible
                NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic }
            }
        }
        Rectangle {
            readonly property real head: Math.min(1, track.sweep * 1.4) * track.width
            readonly property real tail: Math.max(0, track.sweep * 1.4 - 0.4) * track.width
            x: tail
            width: Math.max(0, head - tail)
            height: parent.height
            radius: height / 2
            color: Theme.accent
            visible: control.indeterminate
        }

        NumberAnimation on sweep {
            from: 0
            to: 1
            duration: 1400
            loops: Animation.Infinite
            easing.type: Easing.InOutQuad
            running: control.indeterminate && control.visible
        }
    }

    background: Rectangle {
        implicitWidth: 200
        implicitHeight: 6
        y: (control.height - height) / 2
        height: 6
        radius: 3
        color: Theme.alpha(Theme.fg, 0.1)
    }
}
