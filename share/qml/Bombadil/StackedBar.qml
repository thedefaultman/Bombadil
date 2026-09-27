import QtQuick

// One bar split into parts (memory: apps, cache, ...) with the rest of `total` as free
// track, and a legend with each part's value below. Hovering a segment (or setting
// `hoverIndex`) shows its value and share.
Item {
    id: root

    property var parts: []
    property real total: NaN
    property var format: function (v) { return Math.abs(v) >= 1000 ? Fmt.compact(v) : String(Number(Number(v).toPrecision(3))) }
    property bool legend: true
    property string freeLabel: "Free"
    property int hoverIndex: -1

    readonly property real _sum: {
        var sum = 0, list = parts || []
        for (var i = 0; i < list.length; i++)
            sum += Math.max(0, Number(list[i] && list[i].value) || 0)
        return sum
    }
    readonly property real _whole: total > _sum ? total : _sum
    readonly property real _free: _whole - _sum
    // [{ label, value, color, start, end }] with start/end as fractions of the whole.
    readonly property var _parts: {
        var out = [], list = parts || [], at = 0
        for (var i = 0; i < list.length; i++) {
            var p = list[i] || {}, v = Math.max(0, Number(p.value) || 0)
            out.push({
                label: String(p.label === undefined ? "" : p.label),
                value: v,
                color: p.color ? p.color : (i < Theme.series.length ? Theme.series[i] : Theme.muted),
                start: _whole > 0 ? at / _whole : 0,
                end: _whole > 0 ? (at + v) / _whole : 0
            })
            at += v
        }
        return out
    }
    readonly property color _track: Theme.raised
    readonly property int _barHeight: 12

    on_PartsChanged: canvas.requestPaint()
    onHoverIndexChanged: canvas.requestPaint()

    implicitWidth: 320
    implicitHeight: _barHeight + (legend ? 10 + legendFlow.height : 0)

    Canvas {
        id: canvas
        width: parent.width
        height: root._barHeight
        onWidthChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.clearRect(0, 0, width, height)
            var w = width, h = height, list = root._parts
            ctx.beginPath()
            ctx.roundedRect(0, 0, w, h, 4, 4)
            ctx.clip()
            // 2 px gaps (left unpainted) between neighbours, and before the free track.
            var end = 0
            for (var i = 0; i < list.length; i++) {
                var a = Math.round(list[i].start * w), b = Math.round(list[i].end * w)
                var x0 = a + (a > 0 ? 1 : 0), x1 = b - (b < w ? 1 : 0)
                if (x1 - x0 < 1)
                    continue
                ctx.fillStyle = i === root.hoverIndex ? Qt.lighter(list[i].color, 1.25) : list[i].color
                ctx.fillRect(x0, 0, x1 - x0, h)
                end = b
            }
            if (end < w) {
                ctx.fillStyle = root._track
                ctx.fillRect(end + (end > 0 ? 1 : 0), 0, w, h)
            }
        }
    }

    MouseArea {
        width: parent.width
        height: root._barHeight + 8
        y: -4
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: mouse => {
            var f = mouse.x / Math.max(1, width), hit = -1
            for (var i = 0; i < root._parts.length; i++)
                if (f >= root._parts[i].start && f < root._parts[i].end)
                    hit = i
            root.hoverIndex = hit
        }
        onExited: root.hoverIndex = -1
    }

    Flow {
        id: legendFlow
        visible: root.legend
        y: root._barHeight + 10
        width: parent.width
        spacing: 16
        Repeater {
            model: root.legend ? root._parts.length + (root._free > 0 && root.freeLabel !== "" ? 1 : 0) : 0
            delegate: Row {
                required property int index
                readonly property bool isFree: index >= root._parts.length
                readonly property var part: isFree ? null : root._parts[index]
                spacing: 6
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 10; height: 10; radius: 2
                    color: parent.isFree ? root._track : parent.part.color
                    border.color: parent.isFree ? Theme.borderStrong : "transparent"
                }
                Text {
                    text: parent.isFree ? root.freeLabel : parent.part.label
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                }
                Text {
                    text: String(root.format(parent.isFree ? root._free : parent.part.value))
                    color: Theme.fg
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                    font.weight: Font.Medium
                    font.features: { "tnum": 1 }
                }
            }
        }
    }

    Rectangle {
        id: tip
        readonly property var part: root.hoverIndex >= 0 && root.hoverIndex < root._parts.length ? root._parts[root.hoverIndex] : null
        visible: part !== null
        z: 10
        width: tipRow.width + 20
        height: tipRow.height + 14
        radius: Theme.radiusSmall
        color: Theme.overlay
        border.color: Theme.borderStrong
        x: part ? Math.max(0, Math.min(root.width - width, (part.start + part.end) / 2 * root.width - width / 2)) : 0
        y: -height - 6

        Row {
            id: tipRow
            x: 10
            y: 7
            spacing: 8
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 10; height: 2; radius: 1
                color: tip.part ? tip.part.color : "transparent"
            }
            Text {
                text: tip.part ? String(root.format(tip.part.value)) : ""
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
                font.weight: Font.Medium
                font.features: { "tnum": 1 }
            }
            Text {
                text: tip.part ? tip.part.label : ""
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
            }
            Text {
                text: tip.part ? Fmt.percent(tip.part.value / root._whole) : ""
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
                font.features: { "tnum": 1 }
            }
        }
    }
}
