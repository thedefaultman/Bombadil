import QtQuick
import "DeskTheme.js" as T

// What the desk shows, worked out from agentd's messages and from where the windows are.
// Plain QtQuick with no Quickshell types, so tests can drive it offscreen: the windows come in
// through setWindows and the fold timing is a property, the way PillState takes _now and fadeAfter.
//
// Widgets are present only with something to say. Each present widget has a face: "full" (a card
// in its rail), "strip" (a chip beside the pill) or "hidden". The desk always shows the largest
// face that fits: a card that does not fit the rail's height folds by the height rule, one that a
// window covers folds for as long as it is covered, and "desk" folds every card at once.
QtObject {
    id: desk

    // Messages for agentd; shell.qml writes them to the socket.
    signal outgoing(var msg)
    // A button on a card was pressed (Needs you and Watching, later).
    signal rowAction(string widget, string key, string action)
    signal rowRemove(string widget, string key)

    // Left rail first, in the default order, then right.
    readonly property var widgetIds: ["now", "watching", "alive", "needs", "away", "machine"]

    // -- inputs --

    property var pill: null             // the PillState: its line decides when Now leaves
    property bool connected: false
    property int screenWidth: 1920      // the desk screen, in logical pixels
    property int screenHeight: 1080
    property var windows: []            // [{x, y, w, h, kind, fullscreen}] on the stage, screen coordinates
    property real leftStripsWidth: 0    // what the strips on each side measure (DeskStrips reports them)
    property real rightStripsWidth: 0
    property int foldMs: T.foldMs
    property int unfoldDelayMs: T.unfoldDelayMs

    // -- the desk agentd keeps (desk.toml); a `desk` message replaces these --

    property bool folded: false
    property var hidden: []
    property var rails: ({ now: "left", watching: "left", alive: "left", needs: "right", away: "right", machine: "right" })
    property var order: ({ left: ["now", "watching", "alive"], right: ["needs", "away", "machine"] })
    property string screen: ""          // "" = the shell's first screen

    // -- what the faces read --

    // Now: {title, why, steps: [{id, label, status}], edge, command, caption, done, running}
    property var nowModel: ({ title: "", why: "", steps: [], edge: "machine", command: "", caption: "",
                              done: false, running: false })
    property string nowStripText: "working"
    // Rows cards: {title, why, rows: [{key, kind, title, sub, meter, meterText, tone, button, remove}]}
    // and an optional strip: {text, dot, ring, mark, textColor, outlined}. Filled by later pieces.
    property var watchModel: ({ title: "Watching", why: "", rows: [] })
    property var needsModel: ({ title: "Needs you", why: "", rows: [] })
    property var awayModel: null
    property var machineModel: null
    property var aliveModel: null

    // -- the turn Now follows --

    property var turn: null
    property string prompt: ""
    property string askedBy: ""
    property var steps: []              // the plan: [{id, subject, active, status}]
    property string _phase: "idle"      // idle, running, or done (the closing line still shows)
    property string _stepText: ""       // the words of the step agentd last said, for a turn with no plan
    property string _touchedText: ""
    property bool _system: false        // this turn has touched the system
    property string _risk: ""           // the risk and command of the step running right now
    property string _command: ""
    property double _seconds: 0
    property bool _changed: false
    property string _error: ""
    property string _result: ""
    property bool _resultOk: true

    readonly property string pillMode: pill ? pill.mode : ""
    // Two steps are a route; one step is only worth a card when it touches the system.
    readonly property bool _qualifies: steps.length >= 2 || _system
    readonly property bool nowVisible: _phase === "running" ? _qualifies
                                     : _phase === "done" && pillMode === "closing"

    readonly property int nowCaptionHeight: 18

    // -- presence --

    function isHidden(id) { return id !== "needs" && hidden.indexOf(id) >= 0 }
    function _rows(m) { return m && m.rows ? m.rows.length : 0 }

    readonly property bool nowPresent: nowVisible && !isHidden("now")
    readonly property bool watchingPresent: _rows(watchModel) > 0 && !isHidden("watching")
    // A single waiting session is the line in the pill, not a card.
    readonly property bool needsPresent: _rows(needsModel) >= 2
    readonly property bool awayPresent: _rows(awayModel) > 0 && !isHidden("away")
    readonly property bool machinePresent: _rows(machineModel) > 0 && !isHidden("machine")
    readonly property bool alivePresent: _rows(aliveModel) > 0 && !isHidden("alive")
    readonly property var present: ({ now: nowPresent, watching: watchingPresent, needs: needsPresent,
                                      away: awayPresent, machine: machinePresent, alive: alivePresent })

    // -- the modes: what the windows on the stage do to the desk --

    readonly property string mode: {
        if (windows.some(w => w.fullscreen)) return "immersive"
        return windows.length > 0 ? "shared" : "open"
    }
    readonly property bool capsule: mode === "immersive"
    // The pill is as wide as the stage leaves it: up to 900, less what the strips take on each side,
    // and less a rail's width while a card is showing in full (so the line above the pill never
    // covers a card on a small screen).
    readonly property bool anyFull: widgetIds.some(id => faces[id] === "full")
    readonly property real pillTarget: mode === "immersive" ? 100
        : mode === "shared" ? 360
        : Math.max(360, Math.min(900, screenWidth - 2 * (Math.max(leftStripsWidth, rightStripsWidth, anyFull ? T.cardWidth : 0)
                                                        + T.stripGap + T.railMargin)))
    property int pillAnimMs: 150
    property real pillWidth: pillTarget
    Behavior on pillWidth { NumberAnimation { duration: desk.pillAnimMs; easing.type: Easing.OutCubic } }

    // -- the rails --

    readonly property int rowZone: 64   // the bar's exclusive zone: the rails stop above it
    readonly property real railBottomY: screenHeight - rowZone - T.railBottom
    readonly property real railHeight: railBottomY - T.railTop

    function railX(side) { return side === "left" ? T.railMargin : screenWidth - T.railMargin - T.cardWidth }

    function _model(id) {
        return id === "watching" ? watchModel : id === "needs" ? needsModel : id === "away" ? awayModel
             : id === "machine" ? machineModel : id === "alive" ? aliveModel : null
    }

    // How tall a widget's full face is: Now by its steps, a rows card by its rows.
    function heightOf(id) {
        if (id === "now") {
            return 68 + 26 * nowModel.steps.length + (nowModel.command ? 20 : 0)
                 + (nowModel.caption ? nowCaptionHeight : 0)
        }
        if (id === "machine") return 210
        if (id === "alive") return 168
        const m = _model(id)
        let h = 50 + 18
        for (const r of (m && m.rows ? m.rows : [])) h += r.kind === "meter" ? 34 : 44
        return h
    }

    // Each present widget's slot, from the bottom of its rail up, nearest the pill first, and whether
    // it fits: a widget takes its full face if that fits the room left; it and everything above it
    // fold to strips otherwise. A slot is where the full face would be whether it is shown or not, so
    // a card that folds for a window does not let the ones above it slide down.
    readonly property var _stack: _layout()
    readonly property var slots: _stack.slots

    function _layout() {
        const slots = {}, fit = {}
        for (const side of ["left", "right"]) {
            let used = 0, fits = true
            for (const id of order[side]) {
                if (!present[id]) continue
                const h = heightOf(id)
                const gap = used > 0 ? T.cardGap : 0
                if (used + gap + h > railHeight) fits = false
                slots[id] = { side: side, x: railX(side), y: railBottomY - used - gap - h, w: T.cardWidth, h: h }
                fit[id] = fits
                used += gap + h
            }
        }
        return { slots: slots, fit: fit }
    }

    // -- right of way --

    property var covered: ({})          // which slots a window touches right now
    property var coverFolded: ({})      // which cards are folded for a window (with the hysteresis)
    property var _unfoldTimers: ({})

    function _hits(w, s) { return w.x < s.x + s.w && w.x + w.w > s.x && w.y < s.y + s.h && w.y + w.h > s.y }

    // A window over a slot folds its card at once (the fold animation is foldMs); the card comes back
    // unfoldDelayMs after the window is gone, and a window back over it in that time keeps it folded.
    function _updateCover() {
        const raw = {}
        for (const id of Object.keys(slots)) raw[id] = windows.some(w => _hits(w, slots[id]))
        if (JSON.stringify(raw) !== JSON.stringify(covered)) covered = raw
        const next = Object.assign({}, coverFolded)
        let changed = false
        for (const id of widgetIds) {
            const t = _unfoldTimers[id]
            if (raw[id]) {
                if (t && t.running) t.stop()
                if (!next[id]) { next[id] = true; changed = true }
            } else if (next[id] && t && !t.running) {
                t.restart()
            }
        }
        if (changed) coverFolded = next
    }

    function _unfold(id) {
        if (covered[id]) return
        const next = Object.assign({}, coverFolded)
        next[id] = false
        coverFolded = next
    }

    onSlotsChanged: _updateCover()

    // A QtObject has nowhere to put child objects, so the pieces it owns are listed here: the component
    // of the per-widget unfold timers, and the pill's line going.
    property list<QtObject> _parts: [
        Component {
            id: unfoldTimer
            Timer {
                property string wid
                interval: desk.unfoldDelayMs
                onTriggered: desk._unfold(wid)
            }
        },
        Connections {
            target: desk.pill
            ignoreUnknownSignals: true
            // The closing line went (dismissed, faded, or another line took its place): so does the card.
            function onModeChanged() {
                if (desk._phase === "done" && desk.pill.mode !== "closing" && desk.pill.mode !== "working") desk._clear()
            }
        }
    ]

    Component.onCompleted: {
        const timers = {}
        for (const id of widgetIds) timers[id] = unfoldTimer.createObject(desk, { wid: id })
        _unfoldTimers = timers
    }

    // The windows on the desk screen's stage. Called again and again while one is up: the same list
    // changes nothing.
    function setWindows(list) {
        const clean = (Array.isArray(list) ? list : []).map(w => ({
            x: Number(w.x), y: Number(w.y), w: Number(w.w), h: Number(w.h),
            kind: w.kind === "panel" ? "panel" : "window", fullscreen: !!w.fullscreen
        })).filter(w => isFinite(w.x + w.y + w.w + w.h) && w.w > 0 && w.h > 0)
        if (JSON.stringify(clean) === JSON.stringify(windows)) return
        windows = clean
        _updateCover()
    }

    function setMonitor(width, height) {
        screenWidth = Math.round(Number(width) || screenWidth)
        screenHeight = Math.round(Number(height) || screenHeight)
    }

    // -- faces --

    readonly property var faces: _faces()

    function _faces() {
        const f = {}
        for (const id of widgetIds) {
            if (!present[id] || mode === "immersive") f[id] = "hidden"
            else if (folded || !_stack.fit[id] || coverFolded[id]) f[id] = "strip"
            else f[id] = "full"
        }
        return f
    }

    // -- strips: at most three a side, nearest the pill first; the rest merge into "+N" --

    readonly property int stripsPerSide: 3
    readonly property var leftStrips: _strips("left")
    readonly property var rightStrips: _strips("right")

    // The rail lists a side bottom-up; on the left that runs outward from the pill the other way,
    // so the first widget of the left rail is the outermost strip.
    function _strips(side) {
        const ids = (side === "left" ? order.left.slice().reverse() : order.right).filter(id => faces[id] === "strip")
        return mergeStrips(ids.map(_stripOf))
    }

    function mergeStrips(list) {
        if (list.length <= stripsPerSide) return list
        return list.slice(0, stripsPerSide).concat([{ id: "more", text: "+" + (list.length - stripsPerSide),
            dot: "", ring: false, mark: false, textColor: T.muted, outlined: false, plus: true }])
    }

    function _stripOf(id) {
        const m = _model(id)
        const n = _rows(m)
        let s = { id: id, text: id, dot: "", ring: false, mark: false, textColor: T.muted, outlined: false, plus: false }
        if (id === "now") {
            s = Object.assign(s, { text: nowStripText, dot: nowModel.done ? T.ok : T.machine, ring: !nowModel.done })
        } else if (id === "watching") {
            s.text = n + " counting"
        } else if (id === "needs") {
            Object.assign(s, { text: n + " need you", dot: T.you, outlined: true })
        } else if (id === "away") {
            s.text = "back · " + n + (n === 1 ? " thing" : " things")
        } else if (id === "alive") {
            s.text = n + " alive"
        }
        return m && m.strip ? Object.assign(s, m.strip) : s
    }

    // -- agentd's messages --

    function _oneLine(text, max) {
        const t = String(text || "").replace(/\s+/g, " ").trim()
        return t.length > max ? t.slice(0, max - 1).replace(/\s+$/, "") + "…" : t
    }

    function _clean(steps) {
        return (Array.isArray(steps) ? steps : []).map(s => ({
            id: String(s.id), subject: String(s.subject || ""), active: s.active ? String(s.active) : null,
            status: s.status === "completed" || s.status === "in_progress" ? s.status : "pending"
        }))
    }

    function _currentId() {
        const s = steps.find(s => s.status === "in_progress")
        return s ? s.id : null
    }

    function _reset(t) {
        turn = t; steps = []; _phase = "running"; _stepText = ""; _touchedText = ""
        _system = false; _risk = ""; _command = ""; _seconds = 0; _changed = false
        _error = ""; _result = ""; _resultOk = true
    }

    function _clear() {
        turn = null; _phase = "idle"
    }

    function handle(ev) {
        if (!ev || typeof ev !== "object") return
        if (ev.type === "desk") { _applyDesk(ev); return }
        if (ev.type === "status") {
            if (ev.busy && ev.turn !== undefined && ev.turn !== null && _phase === "idle") {
                // The bar (re)connected in the middle of a turn; agentd sends its plan next.
                _reset(ev.turn); prompt = ""; askedBy = ""
                _refreshNow()
            } else if (!ev.busy && _phase === "running") {
                // agentd is idle and we never heard the turn end.
                _clear()
            }
            return
        }
        if (ev.type !== "event") return
        switch (ev.kind) {
        case "turn_start":
            _reset(ev.turn)
            prompt = String(ev.prompt || ""); askedBy = ev.asked_by ? String(ev.asked_by) : ""
            break
        case "plan": {
            // A plan is stamped with its turn; one from another turn is stale.
            if (_phase !== "running" || ev.turn !== turn) return
            const before = _currentId()
            steps = _clean(ev.steps)
            if (_currentId() !== before) { _risk = ""; _command = "" }
            break
        }
        case "status":
            // Only agentd's own step words carry the risk and the command; what the agent says
            // itself always has risk null and must not put the amber out.
            if (_phase !== "running" || ev.source !== "step" || (ev.turn !== undefined && ev.turn !== turn)) return
            _risk = ev.risk || ""; _command = ev.command || ""
            _touchedText = ev.touched_text || ""
            if (ev.text) _stepText = String(ev.text)
            if (_risk) _system = true
            break
        case "tool_result":
            if (_phase !== "running" || ev.turn !== turn) return
            _risk = ""; _command = ""
            break
        case "result":
            if (_phase !== "running" || ev.turn !== turn) return
            _result = ev.text || ""; _resultOk = ev.ok !== false
            break
        case "error":
            if (_phase === "running" && ev.turn === turn) _error = ev.text || ""
            break
        case "turn_end":
            if (_phase !== "running" || ev.turn !== turn) return
            _end(ev)
            break
        default:
            return
        }
        _refreshNow()
    }

    function _end(ev) {
        const failed = _error !== "" && (!_resultOk || _result === "")
        if (ev.stopped || failed || !_qualifies) {
            // A stopped or failed turn has nothing to tick: the card leaves at once.
            _clear()
            return
        }
        _phase = "done"
        _seconds = Number(ev.seconds) || 0
        _changed = !!ev.changed
        _risk = ""; _command = ""
    }

    // The socket dropped: what agentd is doing is unknown until its status says so.
    function lost() {
        connected = false
        if (_phase === "running") _clear()
    }

    onConnectedChanged: if (connected) outgoing({ type: "desk", op: "get" })

    // -- Now's model --

    // The plan's steps as the card draws them. A turn that touched the system without stating a plan
    // has one step: the one agentd last said.
    function _route() {
        const done = _phase === "done"
        const list = steps.length === 0 && _stepText !== ""
            ? [{ id: "step", subject: _stepText, active: null, status: "in_progress" }] : steps
        let current = false
        return list.map(s => {
            let st = done ? "completed" : s.status
            if (st === "in_progress") { if (current) st = "pending"; current = true }
            return { id: s.id, label: st === "in_progress" && s.active ? s.active : s.subject, status: st }
        })
    }

    function _refreshNow() {
        if (_phase === "idle") return   // keep what the card showed while it folds away
        const route = _route()
        const done = _phase === "done"
        const cur = route.findIndex(s => s.status === "in_progress")
        let edge = done ? "ok" : "machine", command = "", caption = ""
        if (!done && cur >= 0 && _risk) {
            edge = _risk === "irreversible" ? "red" : "amber"
            command = _command
            if (_risk === "irreversible") caption = "can't be undone · Esc stops it"
        }
        // "Step 2 of 4" needs a plan; with none the strip says the machine is working.
        const n = steps.length
        const at = cur >= 0 ? cur + 1 : Math.min(route.filter(s => s.status === "completed").length + 1, n)
        let why
        if (done) {
            why = "done in " + Math.round(_seconds) + " s" + (_changed ? " · Undo is above the pill" : "")
        } else {
            const parts = []
            if (n > 0) parts.push("Step " + at + " of " + n)
            if (_touchedText) parts.push(_touchedText)
            parts.push("Esc stops")
            why = parts.join(" · ")
        }
        const ask = _oneLine(prompt, 60) || "Working on it"
        nowStripText = done ? "done" : n > 0 ? "step " + at + " of " + n : "working"
        nowModel = { title: askedBy ? ask + " · asked by " + askedBy : ask, why: why, steps: route, edge: edge,
                     command: command, caption: caption, done: done, running: !done }
    }

    // -- the desk state --

    function _known(id) { return widgetIds.indexOf(id) >= 0 }

    // agentd's desk message: whatever it leaves out stays as it was, whatever it gets wrong is dropped.
    function _applyDesk(ev) {
        if (ev.folded !== undefined) folded = !!ev.folded
        if (Array.isArray(ev.hidden)) hidden = ev.hidden.filter(id => _known(id) && id !== "needs")
        if (typeof ev.screen === "string") screen = ev.screen
        const r = Object.assign({}, rails)
        if (ev.rails && typeof ev.rails === "object") {
            for (const id of widgetIds) if (ev.rails[id] === "left" || ev.rails[id] === "right") r[id] = ev.rails[id]
        }
        // Each side lists its widgets bottom-up; one it does not list goes last.
        const o = ev.order && typeof ev.order === "object" ? ev.order : order
        const next = {}
        for (const side of ["left", "right"]) {
            const listed = (Array.isArray(o[side]) ? o[side] : []).filter((id, i, a) => _known(id) && r[id] === side && a.indexOf(id) === i)
            next[side] = listed.concat(widgetIds.filter(id => r[id] === side && listed.indexOf(id) < 0))
        }
        rails = r
        order = next
    }

    // -- what the desk asks agentd to do (the state comes back as a `desk` message) --

    // "desk": every card folds to its strip, and back.
    function fold() { outgoing({ type: "desk", op: "fold" }) }

    function hide(widget) {
        if (widget === "needs") return false   // the one place two sessions' asks are answered together
        outgoing({ type: "desk", op: "hide", widget: widget })
        return true
    }

    function show(widget) { outgoing({ type: "desk", op: "show", widget: widget }) }

    // rank 0 is nearest the pill; rail is left out to keep the widget where it is.
    function move(widget, rail, rank) {
        const msg = { type: "desk", op: "move", widget: widget, rank: rank }
        if (rail) msg.rail = rail
        outgoing(msg)
    }

    // A click on Now's title: the turn's commands and output, as on the line.
    function openDetails() {
        if (pill && pill.turn !== null && pill.turn !== undefined) pill.details()
    }

    // Everything a test or a person at `quickshell ipc call desk state` wants to see.
    function snapshot() {
        return {
            mode: mode, capsule: capsule, pillWidth: pillWidth, folded: folded, hidden: hidden,
            rails: rails, order: order, screen: screen, present: present, faces: faces, covered: covered,
            slots: slots, windows: windows,
            strips: { left: leftStrips.map(s => s.text), right: rightStrips.map(s => s.text) },
            now: { visible: nowVisible, phase: _phase, model: nowModel }
        }
    }
}
