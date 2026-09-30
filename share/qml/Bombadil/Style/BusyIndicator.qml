import QtQuick
import QtQuick.Templates as T
import Bombadil

// A spinning accent arc on a faint ring (Canvas, no shaders). 24 px; size it with width/height.
T.BusyIndicator {
    id: control

    implicitWidth: implicitContentWidth + leftPadding + rightPadding
    implicitHeight: implicitContentHeight + topPadding + bottomPadding

    padding: 2

    contentItem: Item {
        implicitWidth: 24
        implicitHeight: 24
        opacity: control.running ? 1 : 0
        visible: opacity > 0

        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

        Canvas {
            id: arc
            anchors.centerIn: parent
            width: Math.min(parent.width, parent.height)
            height: width
            antialiasing: true
            onWidthChanged: requestPaint()
            onPaint: {
                const ctx = getContext("2d")
                const w = Math.max(2, width / 10)
                const r = width / 2 - w / 2
                ctx.reset()
                ctx.lineWidth = w
                ctx.lineCap = "round"
                ctx.strokeStyle = Theme.alpha(Theme.fg, 0.12)
                ctx.beginPath()
                ctx.arc(width / 2, height / 2, r, 0, 2 * Math.PI)
                ctx.stroke()
                ctx.strokeStyle = Theme.accent
                ctx.beginPath()
                ctx.arc(width / 2, height / 2, r, -Math.PI / 2, Math.PI * 0.25)
                ctx.stroke()
            }

            NumberAnimation on rotation {
                from: 0
                to: 360
                duration: 900
                loops: Animation.Infinite
                running: control.running && control.visible
            }
        }
    }
}
