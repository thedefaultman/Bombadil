import QtQuick

// Lines over time on one y axis. Values are numbers (equally spaced) or {x, y} points.
// Drawn with one Canvas; axis labels, legend and tooltip are plain Text so they match
// the rest of the kit. Hovering (or setting `hoverIndex`) shows a crosshair and a
// tooltip with every series at that x.
Item {
    id: root

    property var series: []
    property real yMin: NaN
    property real yMax: NaN
    property var format: function (v) { return Math.abs(v) >= 1000 ? Fmt.compact(v) : String(Number(Number(v).toPrecision(3))) }
    property var xFormat: null
    property bool area: series.length === 1
    property bool legend: series.length >= 2
    property int gridLines: 4
    // Number of x slots for plain values (e.g. a Series' capacity), so a filling live
    // chart grows in from the right instead of stretching. 0 = the longest series.
    property int capacity: 0
    // The hovered x (a slot for plain values, a point of the longest series for {x, y});
    // -1 when nothing is hovered. Settable, e.g. to link two charts.
    property int hoverIndex: -1

    implicitWidth: 320
    implicitHeight: 180

    // Normalized data: [{ name, color, xs, ys }], xs in slots or x units.
    property var _data: []
    property var _refXs: null   // x of each hover position for {x, y} data; null = slots
    property int _firstSlot: 0  // plain values: the first slot holding data
    property real _x0: 0
    property real _x1: 1
    property real _lo: 0
    property real _hi: 1
    property var _ticks: []
    property var _keys: []      // legend entries; replaced only when they change
    property bool _empty: true
    property bool _live: false
    property real _alo: _lo
    property real _ahi: _hi
    Behavior on _alo { enabled: root._live; NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
    Behavior on _ahi { enabled: root._live; NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }

    readonly property int _gutter: {
        var w = 0
        for (var i = 0; i < _ticks.length; i++)
            w = Math.max(w, metrics.advanceWidth(_label(_ticks[i])))
        return Math.ceil(w) + (w > 0 ? 8 : 0)
    }
    readonly property bool _xLabels: typeof xFormat === "function" && !_empty
    // Plot rectangle inside `plot` (the dot at the last point needs 6 px on the right).
    readonly property real _px0: _gutter
    readonly property real _px1: plot.width - 6
    readonly property real _py0: 8
    readonly property real _py1: plot.height - (_xLabels ? 24 : 8)

    function _label(v) { return String(format(v)) }
    function _colorOf(s, i) { return s && s.color ? s.color : (i < Theme.series.length ? Theme.series[i] : Theme.muted) }
    function _xPix(x) { return _px0 + (x - _x0) / (_x1 - _x0) * (_px1 - _px0) }
    function _yPix(v) { return _py1 - (v - _alo) / (_ahi - _alo) * (_py1 - _py0) }

    // Round tick steps (1, 2, 5 x 10^k, so no label needs a third significant digit): the
    // one that fits the data tightest in at most gridLines + 1 intervals, ties going to the
    // count nearest gridLines. Byte-like formats also try multiples of 1024^k so an axis
    // reads "5 GiB, 10 GiB" rather than "4.7 GiB, 9.3 GiB"; _update keeps whichever
    // family gives labels with fewer significant digits.
    function _steps(span, base) {
        var marks = base === 1024 ? [1, 2, 5, 10, 20, 50, 100, 200, 500] : [1, 2, 5]
        var target = span / (Math.max(1, gridLines) + 1)
        var unit = Math.pow(base, Math.floor(Math.log(target) / Math.log(base)))
        var out = []
        for (var k = 0; k < 3 && out.length < 4; k++, unit *= base)
            for (var i = 0; i < marks.length && out.length < 4; i++)
                if (marks[i] * unit >= target * (1 - 1e-9))
                    out.push(marks[i] * unit)
        return out
    }
    function _digits(s) {
        var m = String(s).match(/[\d.,]+/)
        return m ? m[0].replace(/[.,]/g, "").replace(/^0+/, "").replace(/0+$/, "").length : 0
    }
    function _range(dmin, dmax, base) {
        var fixedLo = !isNaN(yMin), fixedHi = !isNaN(yMax)
        var lo0 = fixedLo ? yMin : (dmin >= 0 ? 0 : dmin)
        var hi0 = fixedHi ? yMax : dmax
        if (hi0 <= lo0)
            hi0 = lo0 + (lo0 === 0 ? 1 : Math.abs(lo0))
        var steps = _steps(hi0 - lo0, base), best = null
        for (var c = 0; c < steps.length; c++) {
            var step = steps[c]
            var lo = fixedLo ? lo0 : Math.floor(lo0 / step + 1e-9) * step
            var hi = fixedHi ? hi0 : Math.ceil(hi0 / step - 1e-9) * step
            var off = Math.abs(Math.round((hi - lo) / step) - gridLines)
            if (!best || hi - lo < best.hi - best.lo - step * 1e-9
                    || (hi - lo <= best.hi - best.lo + step * 1e-9 && off < best.off))
                best = { lo: lo, hi: hi, step: step, off: off }
        }
        var ticks = []
        for (var t = Math.ceil(best.lo / best.step - 1e-9) * best.step; t <= best.hi + best.step * 1e-6; t += best.step)
            ticks.push(Math.abs(t) < best.step * 1e-9 ? 0 : t)
        var score = 0
        for (var i = 0; i < ticks.length; i++)
            score += _digits(format(ticks[i]))
        return { lo: best.lo, hi: best.hi, ticks: ticks, score: score }
    }

    function _update() {
        var data = [], n = 0, xy = false, dmin = Infinity, dmax = -Infinity
        var list = series || []
        for (var i = 0; i < list.length; i++) {
            var vals = (list[i] && list[i].values) || []
            if (vals.length && typeof vals[0] === "object" && vals[0] !== null)
                xy = true
            n = Math.max(n, vals.length)
        }
        var slots = Math.max(n, capacity), xmin = Infinity, xmax = -Infinity, ref = null
        for (i = 0; i < list.length; i++) {
            vals = (list[i] && list[i].values) || []
            var xs = [], ys = []
            for (var j = 0; j < vals.length; j++) {
                var p = vals[j], x, y
                if (xy) {
                    if (p === null || typeof p !== "object")
                        continue
                    x = Number(p.x); y = Number(p.y)
                    xmin = Math.min(xmin, x); xmax = Math.max(xmax, x)
                } else {
                    x = slots - vals.length + j; y = p === null ? NaN : Number(p)
                }
                if (isFinite(y)) {
                    dmin = Math.min(dmin, y); dmax = Math.max(dmax, y)
                }
                xs.push(x); ys.push(y)
            }
            if (xy && (!ref || xs.length > ref.length))
                ref = xs
            data.push({ name: list[i] ? String(list[i].name || "") : "", color: _colorOf(list[i], i), xs: xs, ys: ys })
        }
        _empty = !(dmax >= dmin)
        if (xy) {
            _x1 = xmax; _x0 = xmin < xmax ? xmin : xmax - 1
        } else {
            _x1 = slots - 1; _x0 = slots > 1 ? 0 : -1
        }
        _refXs = ref
        _firstSlot = xy ? 0 : slots - n
        _data = data

        var r
        if (_empty && !isNaN(yMax)) {
            r = _range(isNaN(yMin) ? 0 : yMin, yMax, 10)
        } else if (_empty) {
            r = { lo: isNaN(yMin) ? 0 : yMin, hi: 1, ticks: [] }
        } else {
            r = _range(dmin, dmax, 10)
            if ((isNaN(yMin) || isNaN(yMax) ? dmax - Math.min(0, dmin) : yMax - yMin) / (Math.max(1, gridLines) + 1) >= 1024) {
                var b = _range(dmin, dmax, 1024)
                if (b.score < r.score)
                    r = b
            }
        }
        _lo = r.lo
        _hi = r.hi
        if (JSON.stringify(r.ticks) !== JSON.stringify(_ticks))
            _ticks = r.ticks
        var keys = data.map(d => ({ name: d.name, color: String(d.color) }))
        if (JSON.stringify(keys) !== JSON.stringify(_keys))
            _keys = keys
        if (hoverIndex >= _hoverCount())
            hoverIndex = -1
        canvas.requestPaint()
        if (!_empty && !_live)
            Qt.callLater(function () { root._live = true })
    }

    function _hoverCount() { return _refXs ? _refXs.length : (_empty ? 0 : Math.round(_x1) + 1) }
    function _hoverX() { return _refXs ? _refXs[hoverIndex] : hoverIndex }
    // Index of the point in series `d` shown for the hovered x, or -1.
    function _pointAt(d, x) {
        if (!_refXs) {
            var j = d.xs.length ? Math.round(x - d.xs[0]) : -1
            return j >= 0 && j < d.xs.length && isFinite(d.ys[j]) ? j : -1
        }
        var best = -1
        for (var k = 0; k < d.xs.length; k++)
            if (isFinite(d.ys[k]) && (best < 0 || Math.abs(d.xs[k] - x) < Math.abs(d.xs[best] - x)))
                best = k
        return best
    }
    function _hoverFromMouse(mx) {
        var count = _hoverCount()
        if (!count)
            return -1
        var x = _x0 + (mx - _px0) / (_px1 - _px0) * (_x1 - _x0)
        if (!_refXs)
            return Math.max(_firstSlot, Math.min(count - 1, Math.round(x)))
        var best = 0
        for (var k = 1; k < _refXs.length; k++)
            if (Math.abs(_refXs[k] - x) < Math.abs(_refXs[best] - x))
                best = k
        return best
    }

    onSeriesChanged: _update()
    onYMinChanged: _update()
    onYMaxChanged: _update()
    onCapacityChanged: _update()
    onGridLinesChanged: _update()
    onFormatChanged: _update()
    onAreaChanged: canvas.requestPaint()
    onHoverIndexChanged: canvas.requestPaint()
    on_AloChanged: canvas.requestPaint()
    on_AhiChanged: canvas.requestPaint()
    Component.onCompleted: _update()

    FontMetrics {
        id: metrics
        font.family: Theme.fontFamily
        font.pixelSize: Theme.captionSize
        font.features: { "tnum": 1 }
    }

    FontMetrics {
        id: strong
        font.family: Theme.fontFamily
        font.pixelSize: Theme.captionSize
        font.weight: Font.Medium
        font.features: { "tnum": 1 }
    }

    Flow {
        id: legendRow
        visible: root.legend
        width: parent.width
        spacing: 16
        Repeater {
            model: root.legend ? root._keys : []
            delegate: Row {
                required property var modelData
                spacing: 6
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 12; height: 2; radius: 1
                    color: modelData.color
                }
                Text {
                    text: modelData.name
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                }
            }
        }
    }

    Item {
        id: plot
        anchors.fill: parent
        anchors.topMargin: root.legend ? legendRow.height + 8 : 0

        Repeater {
            model: root._ticks
            delegate: Text {
                required property real modelData
                readonly property real cy: root._yPix(modelData)
                x: root._px0 - 8 - width
                y: cy - height / 2
                visible: cy >= root._py0 - 1 && cy <= root._py1 + 1
                text: root._label(modelData)
                color: Theme.muted
                font: metrics.font
            }
        }

        Text {
            visible: root._xLabels
            x: root._px0
            y: root._py1 + 6
            text: visible ? root.xFormat(root._refXs ? root._x0 : Math.max(0, root._x0)) : ""
            color: Theme.muted
            font: metrics.font
        }
        Text {
            visible: root._xLabels
            x: root._px1 - width
            y: root._py1 + 6
            text: visible ? root.xFormat(root._x1) : ""
            color: Theme.muted
            font: metrics.font
        }

        Canvas {
            id: canvas
            anchors.fill: parent
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()

            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.clearRect(0, 0, width, height)
                var x0 = root._px0, x1 = root._px1, y0 = root._py0, y1 = root._py1
                if (x1 <= x0 || y1 <= y0)
                    return
                ctx.lineWidth = 1

                // Gridlines at the ticks (or evenly while empty), the baseline at 0.
                var base = Math.round(root._yPix(Math.max(root._alo, Math.min(0, root._ahi)))) + 0.5
                var ticks = root._ticks
                if (!ticks.length) {
                    ticks = []
                    for (var g = 0; g <= root.gridLines; g++)
                        ticks.push(root._alo + (root._ahi - root._alo) * g / Math.max(1, root.gridLines))
                }
                ctx.strokeStyle = Theme.grid
                ctx.beginPath()
                for (var t = 0; t < ticks.length; t++) {
                    var gy = Math.round(root._yPix(ticks[t])) + 0.5
                    if (gy < y0 - 1 || gy > y1 + 1 || Math.abs(gy - base) < 1)
                        continue
                    ctx.moveTo(x0, gy)
                    ctx.lineTo(x1, gy)
                }
                ctx.stroke()
                ctx.strokeStyle = Theme.axis
                ctx.beginPath()
                ctx.moveTo(x0, base)
                ctx.lineTo(x1, base)
                ctx.stroke()

                var hx = -1
                if (root.hoverIndex >= 0 && root.hoverIndex < root._hoverCount()) {
                    hx = Math.round(root._xPix(root._hoverX())) + 0.5
                    ctx.strokeStyle = Theme.borderStrong
                    ctx.beginPath()
                    ctx.moveTo(hx, y0 - 4)
                    ctx.lineTo(hx, y1)
                    ctx.stroke()
                }

                ctx.save()
                ctx.beginPath()
                ctx.rect(x0 - 2, y0 - 1, x1 - x0 + 4, y1 - y0 + 2)
                ctx.clip()
                var data = root._data, i, j, d
                if (root.area) {
                    var by = root._yPix(Math.max(root._alo, Math.min(0, root._ahi)))
                    for (i = 0; i < data.length; i++) {
                        d = data[i]
                        ctx.fillStyle = d.color
                        ctx.globalAlpha = 0.1
                        ctx.beginPath()
                        var open = false, last = 0
                        for (j = 0; j <= d.xs.length; j++) {
                            var ok = j < d.xs.length && isFinite(d.ys[j])
                            if (ok) {
                                var ax = root._xPix(d.xs[j]), ay = root._yPix(d.ys[j])
                                if (!open)
                                    ctx.moveTo(ax, by)
                                ctx.lineTo(ax, ay)
                                last = ax
                                open = true
                            } else if (open) {
                                ctx.lineTo(last, by)
                                ctx.closePath()
                                open = false
                            }
                        }
                        ctx.fill()
                    }
                    ctx.globalAlpha = 1
                }
                ctx.lineWidth = 2
                ctx.lineJoin = "round"
                ctx.lineCap = "round"
                for (i = 0; i < data.length; i++) {
                    d = data[i]
                    ctx.strokeStyle = d.color
                    ctx.beginPath()
                    var pen = false
                    for (j = 0; j < d.xs.length; j++) {
                        if (!isFinite(d.ys[j])) { pen = false; continue }
                        var lx = root._xPix(d.xs[j]), ly = root._yPix(d.ys[j])
                        if (pen) ctx.lineTo(lx, ly); else ctx.moveTo(lx, ly)
                        pen = true
                    }
                    ctx.stroke()
                }
                ctx.restore()

                // Dots: an 8 px mark with a 2 px ring cut out of whatever is under it.
                function dot(x, y, color) {
                    ctx.globalCompositeOperation = "destination-out"
                    ctx.fillStyle = "#000"
                    ctx.beginPath()
                    ctx.arc(x, y, 6, 0, Math.PI * 2)
                    ctx.fill()
                    ctx.globalCompositeOperation = "source-over"
                    ctx.fillStyle = color
                    ctx.beginPath()
                    ctx.arc(x, y, 4, 0, Math.PI * 2)
                    ctx.fill()
                }
                for (i = 0; i < data.length; i++) {
                    d = data[i]
                    j = d.xs.length - 1
                    while (j >= 0 && !isFinite(d.ys[j]))
                        j--
                    if (j >= 0 && d.ys[j] >= root._alo && d.ys[j] <= root._ahi)
                        dot(root._xPix(d.xs[j]), root._yPix(d.ys[j]), d.color)
                }
                if (hx >= 0) {
                    for (i = 0; i < data.length; i++) {
                        d = data[i]
                        j = root._pointAt(d, root._hoverX())
                        if (j >= 0 && d.ys[j] >= root._alo && d.ys[j] <= root._ahi)
                            dot(root._xPix(d.xs[j]), root._yPix(d.ys[j]), d.color)
                    }
                }
            }
        }

        MouseArea {
            x: root._px0
            y: 0
            width: Math.max(0, root._px1 - root._px0 + 6)
            height: root._py1 + 4
            hoverEnabled: true
            acceptedButtons: Qt.NoButton
            onPositionChanged: mouse => root.hoverIndex = root._hoverFromMouse(mouse.x + x)
            onExited: root.hoverIndex = -1
        }

        Rectangle {
            id: tip
            readonly property bool shown: root.hoverIndex >= 0 && root.hoverIndex < root._hoverCount()
            readonly property real hx: shown ? root._xPix(root._hoverX()) : 0
            readonly property var rows: {
                if (!shown)
                    return []
                var out = [], hxv = root._hoverX()
                for (var i = 0; i < root._data.length; i++) {
                    var d = root._data[i], j = root._pointAt(d, hxv)
                    if (j >= 0)
                        out.push({ color: d.color, name: d.name, value: root._label(d.ys[j]), y: root._yPix(d.ys[j]) })
                }
                return out
            }
            readonly property real valueWidth: {
                var w = 0
                for (var i = 0; i < rows.length; i++)
                    w = Math.max(w, strong.advanceWidth(rows[i].value))
                return Math.ceil(w)
            }
            visible: shown && rows.length > 0
            z: 10
            width: body.width + 20
            height: body.height + 16
            radius: Theme.radiusSmall
            color: Theme.overlay
            border.color: Theme.borderStrong
            x: hx + 14 + width <= plot.width ? hx + 14 : Math.max(0, hx - 14 - width)
            y: Math.max(0, Math.min(plot.height - height, (rows.length ? rows[0].y : root._py0) - height / 2))

            Column {
                id: body
                x: 10
                y: 8
                spacing: 4
                Text {
                    visible: typeof root.xFormat === "function"
                    text: visible && tip.shown ? root.xFormat(root._hoverX()) : ""
                    color: Theme.muted
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.captionSize
                }
                Repeater {
                    model: tip.rows
                    delegate: Row {
                        required property var modelData
                        spacing: 8
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 10; height: 2; radius: 1
                            color: modelData.color
                        }
                        Text {
                            width: tip.valueWidth
                            horizontalAlignment: Text.AlignRight
                            text: modelData.value
                            color: Theme.fg
                            font: strong.font
                        }
                        Text {
                            visible: text !== ""
                            text: modelData.name
                            color: Theme.muted
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.captionSize
                        }
                    }
                }
            }
        }
    }
}
