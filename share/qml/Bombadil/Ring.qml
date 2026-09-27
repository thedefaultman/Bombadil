import QtQuick

// A 0..1 fraction as a circular gauge with the percentage in the middle and the label
// under it. Tones follow Meter: accent, warn above `warnAt`, bad above `badAt`.
Item {
    id: root

    property real value: 0
    property string label: ""
    property string detail: ""
    property string tone: ""
    property real warnAt: 0.75
    property real badAt: 0.9
    property int size: 96
    property int thickness: 8

    readonly property real _v: Math.max(0, Math.min(1, isNaN(value) ? 0 : value))
    readonly property color _fill: tone !== "" ? Theme.tone(tone)
                                              : _v > badAt ? Theme.bad : _v > warnAt ? Theme.warn : Theme.accent
    property real _shown: _v
    Behavior on _shown { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
    on_ShownChanged: canvas.requestPaint()
    on_FillChanged: canvas.requestPaint()

    implicitWidth: Math.max(size, nameText.implicitWidth, detailText.implicitWidth)
    implicitHeight: col.height

    Column {
        id: col
        width: parent.width
        spacing: 2

        Item {
            width: parent.width
            height: root.size + 6

            Canvas {
                id: canvas
                width: root.size
                height: root.size
                anchors.horizontalCenter: parent.horizontalCenter
                onWidthChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    ctx.clearRect(0, 0, width, height)
                    var t = Math.min(root.thickness, width / 4), c = width / 2, r = c - t / 2
                    ctx.lineWidth = t
                    ctx.strokeStyle = Theme.alpha(root._fill, 0.2)
                    ctx.beginPath()
                    ctx.arc(c, c, r, 0, Math.PI * 2)
                    ctx.stroke()
                    if (root._shown <= 0)
                        return
                    // Square where it starts (12 o'clock), rounded at the value end.
                    var a0 = -Math.PI / 2, a1 = a0 + Math.PI * 2 * root._shown
                    ctx.strokeStyle = root._fill
                    ctx.lineCap = "butt"
                    ctx.beginPath()
                    ctx.arc(c, c, r, a0, a1, false)
                    ctx.stroke()
                    if (root._shown < 1) {
                        ctx.fillStyle = root._fill
                        ctx.beginPath()
                        ctx.arc(c + r * Math.cos(a1), c + r * Math.sin(a1), t / 2, 0, Math.PI * 2)
                        ctx.fill()
                    }
                }
            }
            Text {
                anchors.centerIn: canvas
                text: Fmt.percent(root._v)
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Math.max(Theme.captionSize, Math.round(root.size * 0.2))
                font.weight: Font.DemiBold
            }
        }
        Text {
            id: nameText
            width: parent.width
            visible: text !== ""
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            text: root.label
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
        }
        Text {
            id: detailText
            width: parent.width
            visible: text !== ""
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            text: root.detail
            color: Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
        }
    }
}
