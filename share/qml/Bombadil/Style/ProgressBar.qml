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
        // Reduced motion: the segment holds still in the middle and pulses, as the stone does.
        readonly property real at: Theme.reducedMotion ? 0.5 : sweep

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
            id: segment
            readonly property real head: Math.min(1, track.at * 1.4) * track.width
            readonly property real tail: Math.max(0, track.at * 1.4 - 0.4) * track.width
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
            running: control.indeterminate && control.visible && !Theme.reducedMotion
        }
        SequentialAnimation {
            running: control.indeterminate && control.visible && Theme.reducedMotion
            loops: Animation.Infinite
            NumberAnimation { target: segment; property: "opacity"; to: Theme.pulseLow; duration: 500 }
            NumberAnimation { target: segment; property: "opacity"; to: 1; duration: 500 }
            onStopped: segment.opacity = 1
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
