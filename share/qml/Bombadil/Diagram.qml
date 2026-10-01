import QtQuick
import QtQuick.Shapes

// A picture from data: boxes and the lines between them, in one of four fixed shapes.
//
//   Diagram { width: 640; spec: card }          // card: the data show_card and system_map make
//
// `spec` is a diagram card (src/bombadil/cards.py): shape, title, nodes, links, highlight, say.
//   chain     boxes left to right, wrapping to rows when they do not fit
//   layers    a box below everything that points at it (nodes carry rank and col)
//   compare   before on the left, after on the right, one row per `key`
//   timeline  one row per step: its time, its name and a bar as long as it took (`weight`)
// The layout is fixed and never a force layout: the same data always draws the same picture.
// Nothing here asks the model or the machine; it draws what it is given. The shell's card host,
// agent-written QML cards and apps all use this one component, so every picture in the OS looks
// the same.
//
// A node's state is shown by colour and by a mark (amber and a triangle for warn, red and an x for
// bad, orange and a plus for new, dim and struck through for gone, blue for active), so nothing
// depends on colour alone. `highlight` lights boxes and dims the rest. A box with `opens` is a
// button: clicking it emits opened({kind, value}); picked(node) is emitted for every click.
Item {
    id: root

    property var spec: ({})
    property bool showTitle: true        // the title above the picture and the `say` line under it
    property int minBoxWidth: 132
    property int maxBoxWidth: 208
    // A before-and-after: the width of each column and the room between them for the arrow.
    property int maxCompareWidth: 230
    property int compareGap: 76

    signal opened(var target)
    signal picked(var node)

    readonly property string shape: (spec && spec.shape) || "chain"
    readonly property var nodes: _list(spec ? spec.nodes : null)
    readonly property var links: _list(spec ? spec.links : null)
    readonly property var litIds: _list(spec ? spec.highlight : null)
    readonly property bool partial: !!(spec && spec.partial)
    readonly property bool anyLit: litIds.length > 0
    readonly property bool hasNotes: nodes.some(n => !!n.note)

    readonly property int boxH: 52
    readonly property int noteH: hasNotes ? 30 : 0
    readonly property var geo: _layout(width, nodes, shape)

    implicitWidth: 560
    implicitHeight: column.implicitHeight

    // A list from JSON.parse, or one a Python host handed over: always a real array.
    function _list(v) {
        return v && typeof v !== "string" && v.length !== undefined ? Array.prototype.slice.call(v) : []
    }

    function isLit(id) { return litIds.indexOf(id) >= 0 }

    function _tone(state) {
        switch (state) {
        case "warn": return Theme.warn
        case "bad": return Theme.bad
        case "new": return Theme.accent
        case "active": return Theme.info
        default: return Theme.faint
        }
    }

    function _mark(state) {
        switch (state) {
        case "warn": return "alert-triangle"
        case "bad": return "x"
        case "new": return "plus"
        case "gone": return "minus"
        default: return ""
        }
    }

    // -- layout --

    function _layout(W, nodes, shape) {
        const out = { boxes: ({}), height: 0, rows: [] }
        const n = nodes.length
        if (n === 0 || W <= 0) return out
        const H = boxH + noteH
        if (shape === "timeline") {
            out.height = n * 30
            return out
        }
        if (shape === "compare") {
            // The two columns sit together in the middle with the arrow between them, not out at the
            // edges of a wide card with a gap nothing fills.
            const bw = Math.max(80, Math.min(maxCompareWidth, Math.floor((W - 56) / 2)))
            const mid = Math.max(40, Math.min(compareGap, W - 2 * bw))
            const x0 = Math.max(0, Math.floor((W - (2 * bw + mid)) / 2))
            const rows = {}
            let count = 0
            for (let i = 0; i < n; i++) {
                const r = nodes[i].row !== undefined ? nodes[i].row : i
                const side = nodes[i].side === "after" ? "after" : "before"
                ;(rows[r] = rows[r] || {})[side] = nodes[i]
                count = Math.max(count, r + 1)
            }
            const top = 24
            for (let r = 0; r < count; r++) {
                const row = rows[r] || {}
                const y = top + r * (H + 12)
                if (row.before) out.boxes[row.before.id] = { x: x0, y: y, w: bw }
                if (row.after) out.boxes[row.after.id] = { x: x0 + bw + mid, y: y, w: bw }
                out.rows.push({ y: y, both: !!(row.before && row.after) })
            }
            out.height = top + count * (H + 12) - 12
            out.bw = bw
            out.beforeX = x0
            out.afterRight = x0 + 2 * bw + mid
            return out
        }
        if (shape === "layers") {
            const ranks = {}
            let maxRank = 0, maxCols = 1
            for (let i = 0; i < n; i++) {
                const r = nodes[i].rank !== undefined ? nodes[i].rank : 0
                ;(ranks[r] = ranks[r] || []).push(nodes[i])
                maxRank = Math.max(maxRank, r)
            }
            for (const r in ranks) {
                ranks[r].sort((a, b) => (a.col !== undefined ? a.col : 0) - (b.col !== undefined ? b.col : 0))
                maxCols = Math.max(maxCols, ranks[r].length)
            }
            const gap = 20
            const bw = Math.max(72, Math.min(maxBoxWidth, Math.floor((W - (maxCols - 1) * gap) / maxCols)))
            const rowGap = 38
            for (const r in ranks) {
                const list = ranks[r]
                const total = list.length * bw + (list.length - 1) * gap
                const x0 = Math.max(0, Math.floor((W - total) / 2))
                list.forEach((nd, k) => { out.boxes[nd.id] = { x: x0 + k * (bw + gap), y: r * (H + rowGap), w: bw } })
            }
            out.height = (maxRank + 1) * H + maxRank * rowGap
            return out
        }
        // chain: as many to a row as fit, rows of nearly equal length. The gap holds the longest link label.
        let longest = 0
        for (const ln of links) longest = Math.max(longest, String(ln.label || "").length)
        // A picture still being drawn has its boxes before its links. Leave the room a label will
        // want, so the finished picture does not lay itself out again in two rows.
        if (partial && links.length === 0) longest = 14      // "normal traffic"
        const gap = longest > 0 ? Math.min(132, 34 + Math.round(longest * 6.2)) : 26
        let cols = Math.max(1, Math.min(n, Math.floor((W + gap) / (minBoxWidth + gap))))
        const rows = Math.ceil(n / cols)
        cols = Math.ceil(n / rows)
        const bw = Math.max(72, Math.min(maxBoxWidth, Math.floor((W - (cols - 1) * gap) / cols)))
        const total = cols * bw + (cols - 1) * gap
        const x0 = Math.max(0, Math.floor((W - total) / 2))
        const rowGap = 36
        for (let i = 0; i < n; i++) {
            out.boxes[nodes[i].id] = { x: x0 + (i % cols) * (bw + gap), y: Math.floor(i / cols) * (H + rowGap), w: bw }
        }
        out.height = rows * H + (rows - 1) * rowGap
        return out
    }

    // -- the lines: one path per state, so the links are a handful of shape paths, not one per link --

    function _link(a, b) {
        // a and b: {x, y, w}. Side to side on one row, else bottom to top. The space under a box
        // for its note is part of the box as far as lines go.
        const ah = boxH + noteH, bh = boxH + noteH
        let d, ex, ey, dx, dy
        if (Math.abs(a.y - b.y) < 2) {
            const right = b.x > a.x
            const x1 = right ? a.x + a.w : a.x, x2 = right ? b.x : b.x + b.w
            const y = a.y + boxH / 2
            d = "M " + x1 + " " + y + " L " + x2 + " " + y
            ex = x2; ey = y; dx = right ? 1 : -1; dy = 0
        } else {
            const down = b.y > a.y
            const x1 = a.x + a.w / 2, y1 = down ? a.y + ah : a.y
            const x2 = b.x + b.w / 2, y2 = down ? b.y : b.y + bh
            const mid = (y1 + y2) / 2
            const gapX = x2 - x1, r = 8, s = down ? 1 : -1
            if (shape === "chain" && Math.abs(gapX) > 2 * r + 2) {
                // A chain that wrapped: down, across and down, with rounded corners.
                const t = gapX > 0 ? 1 : -1
                d = "M " + x1 + " " + y1 + " L " + x1 + " " + (mid - s * r)
                  + " Q " + x1 + " " + mid + " " + (x1 + t * r) + " " + mid
                  + " L " + (x2 - t * r) + " " + mid
                  + " Q " + x2 + " " + mid + " " + x2 + " " + (mid + s * r)
                  + " L " + x2 + " " + y2
            } else {
                d = "M " + x1 + " " + y1 + " C " + x1 + " " + mid + " " + x2 + " " + mid + " " + x2 + " " + y2
            }
            ex = x2; ey = y2; dx = 0; dy = s
        }
        // The arrowhead: two short strokes back from the tip.
        const px = -dy, py = dx
        d += " M " + (ex - dx * 7 + px * 4.5) + " " + (ey - dy * 7 + py * 4.5) + " L " + ex + " " + ey
           + " L " + (ex - dx * 7 - px * 4.5) + " " + (ey - dy * 7 - py * 4.5)
        return d
    }

    function _svg(state) {
        let d = ""
        if (shape !== "chain" && shape !== "layers") return d
        for (const ln of links) {
            if ((ln.state || "") !== state) continue
            const a = geo.boxes[ln.from], b = geo.boxes[ln.to]
            if (a && b && ln.from !== ln.to) d += " " + _link(a, b)
        }
        return d
    }

    function _mid(ln) {
        const a = geo.boxes[ln.from], b = geo.boxes[ln.to]
        if (!a || !b) return null
        if (Math.abs(a.y - b.y) < 2) {
            const right = b.x > a.x
            return { x: ((right ? a.x + a.w : a.x) + (right ? b.x : b.x + b.w)) / 2, y: a.y + boxH / 2 }
        }
        const down = b.y > a.y
        const y1 = down ? a.y + boxH + noteH : a.y, y2 = down ? b.y : b.y + boxH + noteH
        return { x: (a.x + a.w / 2 + b.x + b.w / 2) / 2, y: (y1 + y2) / 2 }
    }

    function _longest() {
        let m = 0
        for (const n of nodes) m = Math.max(m, Number(n.weight) || 0)
        return m
    }

    Accessible.role: Accessible.StaticText
    Accessible.name: spec && spec.text ? spec.text : (spec && spec.title) || ""

    Column {
        id: column
        width: root.width
        spacing: 10

        Item {
            visible: root.showTitle && !!(root.spec && root.spec.title)
            width: parent.width
            height: visible ? titleText.implicitHeight : 0

            Text {
                id: titleText
                objectName: "diagramTitle"
                anchors { left: parent.left; right: drawing.left; rightMargin: 8 }
                text: root.spec && root.spec.title ? root.spec.title : ""
                color: Theme.fg
                font.family: Theme.fontFamily
                font.pixelSize: 15
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }
            Text {
                id: drawing
                anchors.right: parent.right
                anchors.verticalCenter: titleText.verticalCenter
                visible: root.partial
                text: "drawing…"
                color: Theme.faint
                font.family: Theme.fontFamily
                font.pixelSize: Theme.captionSize
            }
        }

        // The picture.
        Item {
            id: canvas
            objectName: "diagramCanvas"
            width: parent.width
            height: root.geo.height

            Shape {
                anchors.fill: parent
                visible: root.shape === "chain" || root.shape === "layers"
                preferredRendererType: Shape.CurveRenderer

                ShapePath {
                    strokeColor: Theme.faint; strokeWidth: 1.5; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    joinStyle: ShapePath.RoundJoin
                    PathSvg { path: root._svg("") + root._svg("ok") }
                }
                ShapePath {
                    strokeColor: Theme.warn; strokeWidth: 1.5; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    joinStyle: ShapePath.RoundJoin
                    PathSvg { path: root._svg("warn") }
                }
                ShapePath {
                    strokeColor: Theme.bad; strokeWidth: 2; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    joinStyle: ShapePath.RoundJoin
                    PathSvg { path: root._svg("bad") }
                }
                ShapePath {
                    strokeColor: Theme.accent; strokeWidth: 1.5; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    joinStyle: ShapePath.RoundJoin
                    PathSvg { path: root._svg("new") + root._svg("active") }
                }
                ShapePath {
                    strokeColor: Theme.alpha(Theme.faint, 0.5); strokeWidth: 1.5; fillColor: "transparent"
                    capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                    PathSvg { path: root._svg("gone") }
                }
            }

            // Words on the lines.
            Repeater {
                model: root.shape === "chain" || root.shape === "layers" ? root.links : []
                Rectangle {
                    id: tag
                    required property var modelData
                    readonly property var at: modelData.label ? root._mid(modelData) : null
                    visible: at !== null
                    x: at ? at.x - width / 2 : 0
                    y: at ? at.y - height / 2 : 0
                    width: tagText.implicitWidth + 12
                    height: 18
                    radius: 9
                    color: Theme.panel
                    border.width: 1
                    border.color: Theme.border
                    Text {
                        id: tagText
                        anchors.centerIn: parent
                        text: tag.modelData.label || ""
                        color: Theme.muted
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        textFormat: Text.PlainText
                    }
                }
            }

            // compare: the side names and an arrow from each before to its after.
            Item {
                visible: root.shape === "compare" && root.nodes.length > 0
                anchors.fill: parent
                Text {
                    x: root.geo.beforeX || 0; y: 0
                    text: "Before"
                    color: Theme.muted
                    font.family: Theme.fontFamily; font.pixelSize: Theme.captionSize; font.weight: Font.Medium
                }
                Text {
                    x: (root.geo.afterRight || canvas.width) - width; y: 0
                    text: "After"
                    color: Theme.muted
                    font.family: Theme.fontFamily; font.pixelSize: Theme.captionSize; font.weight: Font.Medium
                }
                Repeater {
                    model: root.geo.rows || []
                    Item {
                        id: pair
                        required property var modelData
                        x: 0; y: modelData.y; width: canvas.width; height: root.boxH
                        Icon {
                            visible: pair.modelData.both
                            anchors.centerIn: parent
                            name: "arrow-right"
                            size: 18
                            color: Theme.accent
                        }
                        Text {
                            visible: !pair.modelData.both
                            anchors.centerIn: parent
                            text: "·"
                            color: Theme.faint
                            font.pixelSize: 18
                        }
                    }
                }
            }

            // The boxes (chain, layers, compare).
            Repeater {
                model: root.shape === "timeline" ? [] : root.nodes
                Rectangle {
                    id: box
                    objectName: "box-" + modelData.id
                    required property var modelData
                    readonly property var g: root.geo.boxes[modelData.id]
                    readonly property string state: modelData.state || "ok"
                    readonly property bool lit: root.isLit(modelData.id)
                    readonly property bool openable: !!modelData.opens
                    readonly property color tone: root._tone(state)
                    readonly property string mark: root._mark(state)
                    visible: !!g
                    x: g ? g.x : 0
                    y: g ? g.y : 0
                    width: g ? g.w : 0
                    height: root.boxH
                    radius: Theme.radiusSmall
                    color: hover.hovered && openable ? Theme.overlay
                         : state === "ok" ? Theme.raised : Theme.alpha(tone, state === "gone" ? 0.06 : 0.14)
                    readonly property int edgeWidth: lit ? 2 : 1
                    readonly property color edge: state === "ok" ? (lit ? Theme.accent : Theme.border) : tone
                    border.width: edgeWidth
                    border.color: edge
                    opacity: state === "gone" ? 0.6 : (root.anyLit && !lit ? 0.7 : 1)
                    Behavior on x { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
                    Behavior on y { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
                    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                    Component.onCompleted: { if (root.partial) { opacity = 0; opacity = Qt.binding(() => state === "gone" ? 0.6 : 1) } }

                    Accessible.role: openable ? Accessible.Button : Accessible.StaticText
                    Accessible.name: modelData.label + (modelData.sub ? ", " + modelData.sub : "")

                    Row {
                        anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter
                                  leftMargin: 10; rightMargin: box.mark ? 24 : 10 }
                        spacing: 8
                        Icon {
                            visible: !!box.modelData.icon
                            anchors.verticalCenter: parent.verticalCenter
                            name: box.modelData.icon || ""
                            size: 16
                            color: box.state === "ok" ? Theme.muted : box.tone
                        }
                        Column {
                            width: parent.width - (box.modelData.icon ? 24 : 0)
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                objectName: "boxLabel"
                                width: parent.width
                                text: box.modelData.label
                                color: box.state === "gone" ? Theme.muted : Theme.fg
                                font.family: Theme.fontFamily
                                font.pixelSize: 13
                                font.weight: Font.Medium
                                font.strikeout: box.state === "gone"
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                            }
                            Text {
                                visible: !!box.modelData.sub
                                width: parent.width
                                text: box.modelData.sub || ""
                                color: Theme.muted
                                font.family: Theme.fontFamily
                                font.pixelSize: 11
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                            }
                        }
                    }

                    Icon {
                        visible: box.mark !== ""
                        anchors { right: parent.right; top: parent.top; rightMargin: 7; topMargin: 7 }
                        name: box.mark
                        size: 13
                        color: box.tone
                    }

                    // What the machine says about this box, under it.
                    Text {
                        visible: !!box.modelData.note
                        y: root.boxH + 4
                        x: -(width - parent.width) / 2
                        width: Math.max(parent.width, 170)
                        horizontalAlignment: Text.AlignHCenter
                        text: box.modelData.note || ""
                        color: Theme.faint
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }

                    HoverHandler { id: hover; cursorShape: box.openable ? Qt.PointingHandCursor : Qt.ArrowCursor }
                    TapHandler {
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: {
                            root.picked(box.modelData)
                            if (box.openable) root.opened(box.modelData.opens)
                        }
                    }
                }
            }

            // timeline: a row per step, with a bar as long as it took.
            Repeater {
                model: root.shape === "timeline" ? root.nodes : []
                Item {
                    id: step
                    objectName: "step-" + modelData.id
                    required property var modelData
                    required property int index
                    readonly property string state: modelData.state || "ok"
                    readonly property bool lit: root.isLit(modelData.id)
                    readonly property real longest: root._longest()
                    readonly property real took: Number(modelData.weight) || 0
                    readonly property int timeW: 64
                    readonly property int nameW: Math.min(260, Math.max(120, Math.floor(canvas.width * 0.42)))
                    x: 0; y: index * 30; width: canvas.width; height: 30
                    opacity: state === "gone" ? 0.6 : (root.anyLit && !lit ? 0.7 : 1)

                    Rectangle {
                        anchors.fill: parent
                        anchors.topMargin: 1; anchors.bottomMargin: 1
                        radius: 6
                        color: stepHover.hovered && !!step.modelData.opens ? Theme.raised : (step.lit ? Theme.accentSoft : "transparent")
                    }
                    Text {
                        x: 0; width: step.timeW - 10; anchors.verticalCenter: parent.verticalCenter
                        horizontalAlignment: Text.AlignRight
                        text: step.modelData.time || ""
                        color: Theme.muted
                        font.family: Theme.monoFamily; font.pixelSize: 11
                        elide: Text.ElideLeft
                    }
                    Rectangle {
                        x: step.timeW; anchors.verticalCenter: parent.verticalCenter
                        width: 8; height: 8; radius: 4
                        color: step.state === "ok" ? Theme.faint : root._tone(step.state)
                    }
                    Text {
                        x: step.timeW + 18; width: step.nameW - 18; anchors.verticalCenter: parent.verticalCenter
                        text: step.modelData.label
                        color: step.state === "gone" ? Theme.muted : Theme.fg
                        font.family: Theme.fontFamily; font.pixelSize: 13
                        font.strikeout: step.state === "gone"
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                    }
                    // The bar: how long it took, against the longest step.
                    Rectangle {
                        id: barTrack
                        x: step.timeW + step.nameW + 8
                        width: Math.max(0, parent.width - x - 84)
                        anchors.verticalCenter: parent.verticalCenter
                        height: 8; radius: 4
                        visible: step.longest > 0 && width > 20
                        color: Theme.alpha(Theme.faint, 0.18)
                        Rectangle {
                            width: Math.max(step.took > 0 ? 3 : 0, parent.width * (step.longest > 0 ? step.took / step.longest : 0))
                            height: parent.height; radius: 4
                            color: step.state === "ok" ? Theme.accent : root._tone(step.state)
                            Behavior on width { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
                        }
                    }
                    Text {
                        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                        width: 76
                        horizontalAlignment: Text.AlignRight
                        text: step.modelData.sub || ""
                        color: step.state === "warn" ? Theme.warn : step.state === "bad" ? Theme.bad : Theme.muted
                        font.family: Theme.fontFamily; font.pixelSize: 11
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                    }
                    HoverHandler { id: stepHover; cursorShape: step.modelData.opens ? Qt.PointingHandCursor : Qt.ArrowCursor }
                    TapHandler {
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: {
                            root.picked(step.modelData)
                            if (step.modelData.opens) root.opened(step.modelData.opens)
                        }
                    }
                }
            }
        }

        // One sentence: what to look at.
        Text {
            objectName: "diagramSay"
            visible: root.showTitle && !!(root.spec && root.spec.say)
            width: parent.width
            text: root.spec && root.spec.say ? root.spec.say : ""
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
        }
    }
}
