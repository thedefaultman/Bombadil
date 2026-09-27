import QtQuick

// A tiny trend line with no axes, for a Stat or a table cell. The range is the data's own
// min..max unless `min`/`max` are set. Hovering shows the value under the pointer.
Item {
    id: root

    property var values: []
    property color color: Theme.series[0]
    property bool area: true
    property real min: NaN
    property real max: NaN
    property var format: function (v) { return Math.abs(v) >= 1000 ? Fmt.compact(v) : String(Number(Number(v).toPrecision(3))) }
    property int hoverIndex: -1

    implicitWidth: 120
    implicitHeight: 32

    readonly property var _ys: {
        var out = [], list = values || []
        for (var i = 0; i < list.length; i++) {
            var v = list[i]
            out.push(v === null || v === undefined ? NaN : Number(typeof v === "object" ? v.y : v))
        }
        return out
    }
    readonly property var _range: {
        var lo = Infinity, hi = -Infinity
        for (var i = 0; i < _ys.length; i++)
            if (isFinite(_ys[i])) {
                lo = Math.min(lo, _ys[i])
                hi = Math.max(hi, _ys[i])
            }
        if (!isNaN(min))
            lo = min
        if (!isNaN(max))
            hi = max
        if (!(hi >= lo))
            return { lo: 0, hi: 1 }
        if (hi === lo)
            return { lo: lo - 1, hi: hi + 1 }
        return { lo: lo, hi: hi }
    }
    // The end dot (r 4 plus its 2 px ring) needs room inside the item.
    function _x(i) { return _ys.length > 1 ? 1 + i / (_ys.length - 1) * (width - 8) : width - 7 }
    function _y(v) { return height - 5 - (v - _range.lo) / (_range.hi - _range.lo) * (height - 10) }

    on_YsChanged: canvas.requestPaint()
    on_RangeChanged: canvas.requestPaint()
    onColorChanged: canvas.requestPaint()
    onAreaChanged: canvas.requestPaint()
    onHoverIndexChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.clearRect(0, 0, width, height)
            var ys = root._ys, n = ys.length, i, pen = false, first = -1, last = -1
            if (!n)
                return
            ctx.save()
            ctx.beginPath()
            ctx.rect(0, 0, width, height)
            ctx.clip()
            if (root.area) {
                var by = height
                ctx.fillStyle = root.color
                ctx.globalAlpha = 0.1
                ctx.beginPath()
                for (i = 0; i <= n; i++) {
                    if (i < n && isFinite(ys[i])) {
                        if (!pen) ctx.moveTo(root._x(i), by)
                        ctx.lineTo(root._x(i), root._y(ys[i]))
                        last = i
                        pen = true
                    } else if (pen) {
                        ctx.lineTo(root._x(last), by)
                        ctx.closePath()
                        pen = false
                    }
                }
                ctx.fill()
                ctx.globalAlpha = 1
            }
            ctx.strokeStyle = root.color
            ctx.lineWidth = 2
            ctx.lineJoin = "round"
            ctx.lineCap = "round"
            ctx.beginPath()
            pen = false
            for (i = 0; i < n; i++) {
                if (!isFinite(ys[i])) { pen = false; continue }
                if (pen) ctx.lineTo(root._x(i), root._y(ys[i])); else ctx.moveTo(root._x(i), root._y(ys[i]))
                pen = true
                last = i
            }
            ctx.stroke()
            ctx.restore()

            var at = root.hoverIndex >= 0 && root.hoverIndex < n && isFinite(ys[root.hoverIndex]) ? root.hoverIndex : last
            if (at >= 0) {
                var x = root._x(at), y = Math.max(4, Math.min(height - 4, root._y(ys[at])))
                ctx.globalCompositeOperation = "destination-out"
                ctx.beginPath()
                ctx.arc(x, y, 6, 0, Math.PI * 2)
                ctx.fill()
                ctx.globalCompositeOperation = "source-over"
                ctx.fillStyle = root.color
                ctx.beginPath()
                ctx.arc(x, y, 4, 0, Math.PI * 2)
                ctx.fill()
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: mouse => {
            var n = root._ys.length
            root.hoverIndex = n ? Math.max(0, Math.min(n - 1, Math.round((mouse.x - 1) / Math.max(1, root.width - 8) * (n - 1)))) : -1
        }
        onExited: root.hoverIndex = -1
    }

    Rectangle {
        readonly property bool shown: root.hoverIndex >= 0 && root.hoverIndex < root._ys.length && isFinite(root._ys[root.hoverIndex])
        visible: shown
        z: 10
        width: label.width + 16
        height: label.height + 10
        radius: Theme.radiusSmall
        color: Theme.overlay
        border.color: Theme.borderStrong
        x: shown ? Math.max(0, Math.min(root.width - width, root._x(root.hoverIndex) - width / 2)) : 0
        y: -height - 4

        Text {
            id: label
            x: 8
            y: 5
            text: parent.shown ? String(root.format(root._ys[root.hoverIndex])) : ""
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
            font.weight: Font.Medium
            font.features: { "tnum": 1 }
        }
    }
}
