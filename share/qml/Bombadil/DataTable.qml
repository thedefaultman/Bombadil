import QtQuick
import QtQuick.Controls

// A sortable table with a sticky header. Rows are plain objects; when `rows` is replaced
// (a poller refreshing every second) the scroll position stays and the selection follows
// the same row (matched by `id`/`uuid`/`key`/`pid`, else by content).
FocusScope {
    id: root

    property var columns: []
    property var rows: []
    property string sortKey: ""
    property bool sortDescending: false
    property alias currentIndex: view.currentIndex
    readonly property var current: currentIndex >= 0 && currentIndex < _sorted.length ? _sorted[currentIndex] : null
    property string emptyText: "Nothing to show"
    readonly property alias count: view.count

    signal activated(var row)
    signal contextRequested(var row)

    // The rows in display order; `currentIndex` indexes this.
    property var _sorted: []
    // `rows` as a plain array: a JS array, any array-like (a list from Python), or a ListModel.
    property int _revision: 0
    readonly property var _rows: {
        void _revision
        const rs = rows
        if (!rs)
            return []
        if (Array.isArray(rs))
            return rs
        if (typeof rs.get === "function" && typeof rs.count === "number") {
            const out = []
            for (let i = 0; i < rs.count; i++)
                out.push(rs.get(i))
            return out
        }
        if (typeof rs.length === "number")
            return Array.from(rs)
        return []
    }
    readonly property int _pad: 10

    implicitWidth: 360
    implicitHeight: header.height + Math.max(3, Math.min(_sorted.length, 8)) * Theme.rowHeight

    // Columns with their defaults filled in; numbers are detected from the data. What was
    // seen is remembered, so an empty result (a search with no match) keeps the alignment.
    readonly property var _numericSeen: ({})
    readonly property var _cols: {
        const rs = _rows
        return (columns || []).map(c => {
            let numeric = !!_numericSeen[c.key]
            for (let i = 0; i < rs.length && i < 50; i++) {
                const v = rs[i] ? rs[i][c.key] : undefined
                if (v !== null && v !== undefined) {
                    numeric = typeof v === "number"
                    _numericSeen[c.key] = numeric
                    break
                }
            }
            let fixed = 0, flex = 1
            if (typeof c.width === "number")
                flex = c.width
            else if (typeof c.width === "string" && c.width.trim().endsWith("px"))
                fixed = parseFloat(c.width)
            else if (typeof c.width === "string" && !isNaN(parseFloat(c.width)))
                flex = parseFloat(c.width)
            return {
                key: c.key, title: c.title !== undefined ? c.title : c.key, fixed: fixed, flex: flex,
                numeric: numeric, align: c.align || (numeric ? "right" : "left"), format: c.format,
                mono: !!c.mono, sortable: c.sortable !== false
            }
        })
    }

    // x and width of every column: fixed ones first, the rest shared by flex weight, but
    // never narrower than the column's title.
    readonly property var _geo: {
        const avail = Math.max(0, view.width - (view.contentHeight > view.height + 1 ? 10 : 0))
        const widths = _cols.map(c => c.fixed > 0 ? c.fixed : -1)
        // Numbers keep room for their values; text columns may elide down to their title.
        const sample = _sorted.slice(0, 40)
        const mins = _cols.map(c => {
            let w = Math.max(40, headMetrics.advanceWidth(c.title) + 16)
            if (c.align !== "left") {
                const m = c.mono ? monoMetrics : cellMetrics
                for (const r of sample)
                    w = Math.max(w, m.advanceWidth(_text(r, c)))
            }
            return Math.ceil(Math.min(w, 200)) + 2 * _pad
        })
        let rest = avail - widths.reduce((sum, w) => sum + Math.max(0, w), 0)
        for (let settled = false; !settled;) {
            settled = true
            let flex = 0
            _cols.forEach((c, i) => { if (widths[i] < 0) flex += c.flex })
            for (let i = 0; i < _cols.length; i++) {
                if (widths[i] >= 0 || flex <= 0 || rest * _cols[i].flex / flex >= mins[i])
                    continue
                widths[i] = mins[i]
                rest -= mins[i]
                settled = false
                break
            }
            if (settled)
                _cols.forEach((c, i) => { if (widths[i] < 0) widths[i] = flex > 0 ? Math.max(0, rest) * c.flex / flex : 0 })
        }
        const xs = [], ws = []
        let x = 0
        for (const w of widths) {
            xs.push(Math.round(x))
            ws.push(Math.round(x + w) - Math.round(x))
            x += w
        }
        return { xs: xs, ws: ws }
    }

    FontMetrics {
        id: cellMetrics
        font.family: Theme.fontFamily
        font.pixelSize: Theme.textSize
        font.features: { "tnum": 1 }
    }
    FontMetrics {
        id: monoMetrics
        font: Theme.monoFont
    }
    FontMetrics {
        id: headMetrics
        font.family: Theme.fontFamily
        font.pixelSize: Theme.captionSize
        font.weight: Font.DemiBold
    }

    function _text(row, col) {
        if (!row)
            return ""
        const v = row[col.key]
        if (col.format)
            return String(col.format(v, row))
        if (v === null || v === undefined)
            return ""
        if (typeof v === "number" && !Number.isInteger(v))
            return String(Math.round(v * 100) / 100)
        return String(v)
    }

    function _key(row) {
        if (row === null || row === undefined)
            return undefined
        if (typeof row !== "object")
            return String(row)
        for (const k of ["id", "uuid", "key", "pid"])
            if (row[k] !== undefined)
                return k + ":" + row[k]
        return JSON.stringify(row)
    }

    function _hasId(row) {
        return !!row && typeof row === "object"
            && ["id", "uuid", "key", "pid"].some(k => row[k] !== undefined)
    }

    function _compare(a, b) {
        const an = a === null || a === undefined || a === "", bn = b === null || b === undefined || b === ""
        if (an || bn)
            return an === bn ? 0 : an ? 1 : -1
        if (typeof a === "number" && typeof b === "number")
            return a - b
        const sa = String(a).toLowerCase(), sb = String(b).toLowerCase()
        return sa < sb ? -1 : sa > sb ? 1 : 0
    }

    function _resort() {
        const rs = _rows
        let next = rs
        if (sortKey !== "") {
            const key = sortKey, dir = sortDescending ? -1 : 1
            const empty = v => v === null || v === undefined || v === ""
            next = rs.map((r, i) => [r, i]).sort((p, q) => {
                const a = p[0] ? p[0][key] : undefined, b = q[0] ? q[0][key] : undefined
                // Missing values sink to the bottom in both directions.
                const c = empty(a) || empty(b) ? _compare(a, b) : dir * _compare(a, b)
                return c !== 0 ? c : p[1] - q[1]
            }).map(p => p[0])
        }
        const prevIndex = view.currentIndex
        const prevKey = prevIndex >= 0 ? _key(_sorted[prevIndex]) : undefined
        const prevHasId = prevIndex >= 0 && _hasId(_sorted[prevIndex])
        const prevCount = _sorted.length
        const y = view.contentY
        _sorted = next
        let index = prevIndex < next.length ? prevIndex : -1
        if (prevKey !== undefined) {
            const found = next.findIndex(r => _key(r) === prevKey)
            // Only a row known by its content keeps its place when it is gone: its values
            // changed. One with an id that is gone (a process that exited) is deselected.
            if (found >= 0 || prevHasId || next.length !== prevCount)
                index = found
        }
        if (view.currentIndex !== index)
            view.currentIndex = index
        if (next.length !== prevCount)
            view.contentY = Math.max(0, Math.min(y, view.contentHeight - view.height))
    }

    on_RowsChanged: _resort()
    onSortKeyChanged: _resort()
    onSortDescendingChanged: _resort()
    Component.onCompleted: _resort()

    Connections {
        target: root.rows && typeof root.rows.get === "function" ? root.rows : null
        ignoreUnknownSignals: true
        function onCountChanged() { root._revision++ }
        function onDataChanged() { root._revision++ }
        function onModelReset() { root._revision++ }
    }

    function _sortBy(col) {
        if (!col.sortable)
            return
        if (sortKey === col.key) {
            sortDescending = !sortDescending
        } else {
            sortDescending = col.numeric
            sortKey = col.key
        }
    }

    function _move(delta) {
        if (view.count === 0)
            return
        view.currentIndex = Math.max(0, Math.min(view.count - 1, view.currentIndex + delta))
    }

    Item {
        id: header
        width: parent.width
        height: Theme.controlHeight
        clip: true

        Repeater {
            model: root._cols.length
            delegate: Item {
                id: head
                required property int index
                readonly property var col: root._cols[index] || {}
                readonly property bool active: root.sortKey !== "" && root.sortKey === col.key
                x: root._geo.xs[index] || 0
                width: root._geo.ws[index] || 0
                height: header.height
                clip: true

                HoverHandler {
                    id: headHover
                    enabled: head.col.sortable === true
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler { onTapped: root._sortBy(head.col) }

                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    x: head.col.align === "right" ? parent.width - width - root._pad
                     : head.col.align === "center" ? (parent.width - width) / 2 : root._pad
                    width: Math.min(implicitWidth, parent.width - 2 * root._pad)
                    spacing: 4
                    layoutDirection: head.col.align === "right" ? Qt.RightToLeft : Qt.LeftToRight

                    Text {
                        width: Math.min(implicitWidth, head.width - 2 * root._pad - (arrow.visible ? 16 : 0))
                        text: head.col.title || ""
                        color: head.active || headHover.hovered ? Theme.fg : Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.captionSize
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Icon {
                        id: arrow
                        anchors.verticalCenter: parent.verticalCenter
                        visible: head.active
                        name: root.sortDescending ? "arrow-down" : "arrow-up"
                        size: 12
                        color: Theme.fg
                    }
                }
            }
        }

        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: Theme.border
        }
    }

    ListView {
        id: view
        anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: parent.bottom; topMargin: 4 }
        focus: true
        clip: true
        model: root._sorted.length
        currentIndex: -1
        reuseItems: true
        boundsBehavior: Flickable.StopAtBounds
        acceptedButtons: Qt.NoButton
        keyNavigationEnabled: true
        highlightFollowsCurrentItem: false

        ScrollBar.vertical: ScrollBar {}

        onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)

        delegate: Item {
            id: rowItem
            required property int index
            readonly property var row: root._sorted[index]
            readonly property bool selected: ListView.isCurrentItem
            width: ListView.view.width
            height: Theme.rowHeight

            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSmall
                color: rowItem.selected ? Theme.accentSoft : rowHover.hovered ? Theme.raised : Theme.alpha(Theme.raised, 0)
            }
            HoverHandler { id: rowHover }

            Repeater {
                model: root._cols.length
                delegate: Text {
                    required property int index
                    readonly property var col: root._cols[index] || {}
                    x: (root._geo.xs[index] || 0) + root._pad
                    width: Math.max(0, (root._geo.ws[index] || 0) - 2 * root._pad)
                    height: rowItem.height
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: col.align === "right" ? Text.AlignRight
                                       : col.align === "center" ? Text.AlignHCenter : Text.AlignLeft
                    text: root._text(rowItem.row, col)
                    color: Theme.fg
                    font.family: col.mono ? Theme.monoFamily : Theme.fontFamily
                    font.pixelSize: col.mono ? Theme.monoFont.pixelSize : Theme.textSize
                    font.features: { "tnum": 1 }
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                }
            }
        }

        TapHandler {
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onTapped: (point, button) => {
                const i = view.indexAt(point.position.x, point.position.y)
                view.forceActiveFocus()
                if (i < 0)
                    return
                view.currentIndex = i
                if (button === Qt.RightButton)
                    root.contextRequested(root._sorted[i])
                else if (tapCount === 2)
                    root.activated(root._sorted[i])
            }
        }

        Keys.onReturnPressed: if (root.current) root.activated(root.current)
        Keys.onEnterPressed: if (root.current) root.activated(root.current)
        Keys.onPressed: event => {
            const page = Math.max(1, Math.floor(view.height / Theme.rowHeight) - 1)
            if (event.key === Qt.Key_Home)
                root._move(-view.count)
            else if (event.key === Qt.Key_End)
                root._move(view.count)
            else if (event.key === Qt.Key_PageUp)
                root._move(-page)
            else if (event.key === Qt.Key_PageDown)
                root._move(page)
            else
                return
            event.accepted = true
        }
    }

    EmptyState {
        anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: parent.bottom }
        visible: root._sorted.length === 0 && root.emptyText !== ""
        text: root.emptyText
    }
}
