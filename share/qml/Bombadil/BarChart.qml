import QtQuick

// Bars for comparing a few named values (top processes, disk use per folder). One
// color for every bar unless an item sets its own; the value sits at each bar's tip.
// Hovering a row (or setting `hoverIndex`) lifts that bar and shows a tooltip.
Item {
    id: root

    property var bars: []
    property bool horizontal: true
    property var format: function (v) { return Math.abs(v) >= 1000 ? Fmt.compact(v) : String(Number(Number(v).toPrecision(3))) }
    property real max: NaN
    property int hoverIndex: -1

    implicitWidth: 320
    implicitHeight: horizontal ? Math.max(1, _bars.length) * 32 : 180

    // [{ label, value, color, text }] with value clamped to >= 0.
    readonly property var _bars: {
        var out = [], list = bars || []
        for (var i = 0; i < list.length; i++) {
            var b = list[i] || {}, v = Math.max(0, Number(b.value) || 0)
            out.push({
                label: String(b.label === undefined ? "" : b.label),
                value: v,
                text: String(format(v)),
                color: b.color ? b.color : Theme.series[0]
            })
        }
        return out
    }
    // Bar lengths as fractions of the scale, animated from the previous ones (matched by label).
    property var _labels: []
    property var _from: []
    property var _to: []
    property real _t: 1
    property bool _ready: false
    NumberAnimation { id: grow; target: root; property: "_t"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }
    function _frac(i) {
        if (i >= _to.length)
            return 0
        var a = i < _from.length ? _from[i] : 0
        return a + (_to[i] - a) * _t
    }
    function _retarget() {
        var old = {}, scale = 0, i
        for (i = 0; i < _to.length && i < _labels.length; i++)
            old[_labels[i]] = _frac(i)
        for (i = 0; i < _bars.length; i++)
            scale = Math.max(scale, _bars[i].value)
        if (max > 0)
            scale = max
        var to = [], from = [], labels = []
        for (i = 0; i < _bars.length; i++) {
            labels.push(_bars[i].label)
            to.push(scale > 0 ? Math.min(1, _bars[i].value / scale) : 0)
            from.push(old[_bars[i].label] !== undefined ? old[_bars[i].label] : 0)
        }
        _labels = labels
        _from = from
        _to = to
        if (_ready) {
            grow.restart()
        } else {
            _t = 1
            canvas.requestPaint()
        }
    }
    on_BarsChanged: _retarget()
    onMaxChanged: _retarget()
    Component.onCompleted: _ready = true
    on_TChanged: canvas.requestPaint()
    onHoverIndexChanged: canvas.requestPaint()

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

    readonly property real _valueWidth: {
        var w = 0
        for (var i = 0; i < _bars.length; i++)
            w = Math.max(w, metrics.advanceWidth(_bars[i].text))
        return Math.ceil(w)
    }
    readonly property real _labelWidth: {
        var w = 0
        for (var i = 0; i < _bars.length; i++)
            w = Math.max(w, metrics.advanceWidth(_bars[i].label))
        return Math.min(Math.ceil(w), Math.round(width * 0.4))
    }

    // Horizontal geometry: rows of `_pitch`, bars from `_bx0` to at most `_bx1`.
    readonly property real _pitch: _bars.length ? Math.min(32, height / _bars.length) : 32
    readonly property real _bx0: _labelWidth + (_labelWidth > 0 ? 12 : 0)
    readonly property real _bx1: width - _valueWidth - 8
    // Vertical geometry: columns of `_slot` between `_cy0` (top) and `_cy1` (baseline).
    readonly property real _slot: _bars.length ? width / _bars.length : width
    readonly property real _cy0: metrics.height + 6
    readonly property real _cy1: height - metrics.height - 8
    readonly property real _thick: horizontal ? Math.max(4, Math.min(20, Math.round(_pitch * 0.56)))
                                              : Math.max(4, Math.min(24, Math.round(_slot * 0.5)))

    // Rectangle of bar i in item coordinates: { x, y, w, h, tipX, tipY }.
    function _rect(i) {
        var f = _frac(i)
        if (horizontal) {
            var len = Math.max(0, _bx1 - _bx0) * f
            var y = i * _pitch + (_pitch - _thick) / 2
            return { x: _bx0, y: y, w: len, h: _thick, tipX: _bx0 + len, tipY: y + _thick / 2 }
        }
        var hgt = Math.max(0, _cy1 - _cy0) * f
        var x = i * _slot + (_slot - _thick) / 2
        return { x: x, y: _cy1 - hgt, w: _thick, h: hgt, tipX: x + _thick / 2, tipY: _cy1 - hgt }
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
            var n = root._bars.length
            if (!n)
                return
            // A 4 px rounded data end, square at the baseline.
            for (var i = 0; i < n; i++) {
                var r = root._rect(i), b = root._bars[i]
                var len = root.horizontal ? r.w : r.h
                if (len < 0.5)
                    continue
                var rad = Math.min(4, len, (root.horizontal ? r.h : r.w) / 2)
                ctx.fillStyle = i === root.hoverIndex ? Qt.lighter(b.color, 1.25) : b.color
                ctx.beginPath()
                if (root.horizontal) {
                    ctx.moveTo(r.x, r.y)
                    ctx.lineTo(r.x + r.w - rad, r.y)
                    ctx.arcTo(r.x + r.w, r.y, r.x + r.w, r.y + rad, rad)
                    ctx.lineTo(r.x + r.w, r.y + r.h - rad)
                    ctx.arcTo(r.x + r.w, r.y + r.h, r.x + r.w - rad, r.y + r.h, rad)
                    ctx.lineTo(r.x, r.y + r.h)
                } else {
                    ctx.moveTo(r.x, r.y + r.h)
                    ctx.lineTo(r.x, r.y + rad)
                    ctx.arcTo(r.x, r.y, r.x + rad, r.y, rad)
                    ctx.lineTo(r.x + r.w - rad, r.y)
                    ctx.arcTo(r.x + r.w, r.y, r.x + r.w, r.y + rad, rad)
                    ctx.lineTo(r.x + r.w, r.y + r.h)
                }
                ctx.closePath()
                ctx.fill()
            }
            ctx.strokeStyle = Theme.axis
            ctx.lineWidth = 1
            ctx.beginPath()
            if (root.horizontal) {
                var ax = Math.round(root._bx0) - 0.5
                ctx.moveTo(ax, root._rect(0).y - 4)
                ctx.lineTo(ax, root._rect(n - 1).y + root._thick + 4)
            } else {
                var ay = Math.round(root._cy1) + 0.5
                ctx.moveTo(0, ay)
                ctx.lineTo(width, ay)
            }
            ctx.stroke()
        }
    }

    Repeater {
        model: root._bars.length
        delegate: Item {
            id: row
            required property int index
            readonly property var bar: root._bars[index]
            readonly property var geo: { root._t; return root._rect(index) }

            Text {
                // Category label: left of the row, or centered under the column.
                x: root.horizontal ? 0 : row.index * root._slot
                y: root.horizontal ? row.geo.tipY - height / 2 : root._cy1 + 6
                width: root.horizontal ? root._labelWidth : root._slot
                horizontalAlignment: root.horizontal ? Text.AlignLeft : Text.AlignHCenter
                elide: Text.ElideRight
                text: row.bar.label
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
            }
            Text {
                // Value at the bar's tip.
                x: root.horizontal ? row.geo.tipX + 8 : Math.max(0, Math.min(root.width - width, row.geo.tipX - width / 2))
                y: root.horizontal ? row.geo.tipY - height / 2 : row.geo.tipY - height - 4
                text: row.bar.text
                color: Theme.muted
                font: metrics.font
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: mouse => {
            var i = Math.floor(root.horizontal ? mouse.y / root._pitch : mouse.x / root._slot)
            root.hoverIndex = i >= 0 && i < root._bars.length ? i : -1
        }
        onExited: root.hoverIndex = -1
    }

    Rectangle {
        id: tip
        readonly property bool shown: root.hoverIndex >= 0 && root.hoverIndex < root._bars.length
        readonly property var bar: shown ? root._bars[root.hoverIndex] : null
        readonly property var geo: { root._t; return shown ? root._rect(root.hoverIndex) : null }
        visible: shown
        z: 10
        width: tipRow.width + 20
        height: tipRow.height + 14
        radius: Theme.radiusSmall
        color: Theme.overlay
        border.color: Theme.borderStrong
        // Horizontal: above the hovered bar, centered on its tip (below when there is no
        // room). Vertical: beside the column, level with its cap.
        x: {
            if (!geo)
                return 0
            if (root.horizontal)
                return Math.max(0, Math.min(root.width - width, geo.tipX - width / 2))
            var right = geo.x + geo.w + 8
            return right + width <= root.width ? right : Math.max(0, geo.x - 8 - width)
        }
        y: {
            if (!geo)
                return 0
            if (!root.horizontal)
                return Math.max(0, Math.min(root._cy1 - height, geo.tipY - height / 2))
            return geo.y - height - 6 >= 0 ? geo.y - height - 6 : geo.y + geo.h + 6
        }

        Row {
            id: tipRow
            x: 10
            y: 7
            spacing: 8
            Text {
                text: tip.bar ? tip.bar.text : ""
                color: Theme.fg
                font: strong.font
            }
            Text {
                text: tip.bar ? tip.bar.label : ""
                color: Theme.muted
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
            }
        }
    }
}
