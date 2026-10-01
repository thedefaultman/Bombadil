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
    // A window came onto the stage (one more than before). The pill puts a picture away for it.
    signal windowOpened()
    // A button or a small x on a rows card was pressed, or a row that opens something was tapped.
    // DeskState answers the ones that are its own (Why? and the x on Watching, Open on Needs you, the
    // disk on Machine); the signals stay open for anyone else.
    signal rowAction(string widget, string key, string action)
    signal rowRemove(string widget, string key)
    signal rowOpen(string widget, string key, string opens)
    onRowAction: (widget, key, action) => {
        if (widget === "watching" && action === "Why?") outgoing({ type: "jobs", op: "why", id: key })
        else if (widget === "needs" && action === "Open") outgoing({ type: "dev", action: "open", key: key })
    }
    onRowRemove: (widget, key) => { if (widget === "watching") _removeJob(key) }
    // Never a prompt: agentd shows what is behind the row and answers nothing else.
    onRowOpen: (widget, key, opens) => {
        if (widget === "machine" && opens !== "") outgoing({ type: "vitals", op: "open", row: opens })
    }

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
    // Rows cards: {title, why, rows: [{key, kind, title, sub, meter, meterText, tone, parts, pulse,
    // button, remove, opens}]} and an optional strip: {text, dot, ring, mark, textColor, outlined}.
    // Watching, Needs you and Machine are filled from agentd's `jobs`, `dev` and `machine` messages
    // (below), the others by later pieces.
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

    // -- what Watching and Needs you read --

    // agentd's jobs table as it last sent it: [{id, title, kind, state, started, deadline, ended, pct,
    // last}], times in epoch seconds, in agentd's order.
    property var jobs: []
    // The clock the rows read, in epoch milliseconds (tests set it). The timer below moves it once a
    // tickMs while agentd lists a job that is counting or finished (a finished row has to leave on
    // time), so a desk with nothing counting wakes nothing.
    property double now: Date.now()
    property int tickMs: 1000
    property int doneStaysMs: 12000     // a finished job's row leaves this long after it ended
    property var _gone: ({})            // rows the person removed that agentd's table has yet to drop
    property int goneMs: 5000           // ...and how long they stay gone if the job is still listed
    readonly property bool ticking: jobs.some(j => !_gone[j.id] && j.state !== "failed")
    readonly property bool clockRunning: clock.running
    // The coding sessions and the keys that want you, in Tab's order, as agentd's `dev` message has them.
    property var sessions: []
    property var attention: []

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
    // Something waits for the person, one session or more. The pill's stone knocks for it, whether the
    // card is up, folded to its strip, or the pill is the capsule under a full-screen window: this is
    // the one mark of the desk that no right of way and no "desk" word puts away.
    readonly property bool needsYou: _rows(needsModel) >= 1
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
    // Two rails and the smallest pill need this much width. A narrower screen (a 1280 panel at 125%,
    // a 1920 one at 200%) keeps its cards as strips, so the line above the pill never covers one.
    readonly property real narrowLimit: 2 * (T.cardWidth + T.railMargin + T.stripGap) + 360
    readonly property bool narrow: screenWidth < narrowLimit
    // The pill is as wide as the stage leaves it: up to 900, less what the strips take on each side,
    // and less a rail's width while a card is showing in full (so the line above the pill never
    // covers a card on a small screen).
    // "Full" here is what the cards would be without a window's right of way: a window going away
    // must not let the pill widen for the 400 ms the cards take to come back.
    readonly property bool anyFull: widgetIds.some(id => present[id] && mode !== "immersive" && !folded
                                                        && !narrow && _stack.fit[id] === true)
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
        if (id === "alive") return 168
        const m = _model(id)
        let h = 50 + 18
        for (const r of (m && m.rows ? m.rows : [])) h += r.kind === "meter" || r.kind === "stack" ? 34 : 44
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
        Timer {
            // A row the person removed comes back if the job is still listed when this runs out: the
            // stop did not work, and the desk must not hide what is still counting.
            id: goneTimer
            interval: desk.goneMs
            onTriggered: { desk._gone = ({}); desk._refreshWatch() }
        },
        Timer {
            // The first tick is at once, for the time the clock stood still.
            id: clock
            interval: desk.tickMs
            repeat: true
            running: desk.ticking
            triggeredOnStart: true
            onTriggered: desk.now = Date.now()
        },
        Binding {
            // The stone knocks while a coding session waits for the person (see needsYou). The desk is
            // the only writer: it clears with the last row, and with the socket (lost()).
            target: desk.pill
            property: "needsYou"
            value: desk.needsYou
            when: desk.pill !== null
            restoreMode: Binding.RestoreNone
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
        const opened = clean.length > windows.length
        windows = clean
        _updateCover()
        if (opened) windowOpened()
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
            else if (folded || narrow || !_stack.fit[id] || coverFolded[id]) f[id] = "strip"
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
        if (ev.type === "jobs") { _applyJobs(ev); return }
        if (ev.type === "dev") { _applyDev(ev); return }
        if (ev.type === "machine") { _applyMachine(ev); return }
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
        // Nothing on Watching or Needs you can be answered now, and agentd sends both tables again.
        if (jobs.length > 0 || Object.keys(_gone).length > 0) { jobs = []; _gone = {} }
        if (sessions.length > 0 || attention.length > 0) { sessions = []; attention = []; _refreshNeeds() }
        // Nor Machine's readings, which are agentd's; it sends the card again with the desk.
        if (machineModel !== null) machineModel = null
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
        // The card says so even with no step marked current: it hosts the command under the last one.
        if (!done && _risk) {
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
            const head = n > 0 ? ["Step " + at + " of " + n] : []
            const counts = _touchedText ? [_touchedText] : []
            why = head.concat(counts, ["Esc stops"]).join(" · ")
            // A line too long for the card loses its counts, not the words that stop the turn.
            if (why.length > whyChars && counts.length > 0) why = head.concat(["Esc stops"], counts).join(" · ")
        }
        // A turn a coding session asked for keeps "asked by" whole; it is the ask that gives way.
        const by = askedBy ? " · asked by " + askedBy : ""
        const ask = _oneLine(prompt, askedBy ? Math.max(12, titleChars - by.length) : 60) || "Working on it"
        nowStripText = done ? "done" : n > 0 ? "step " + at + " of " + n : "working"
        nowModel = { title: ask + by, why: why, steps: _window(route, cur), edge: edge,
                     command: command, caption: caption, done: done, running: !done }
    }

    // A plan too long for the rail shows the steps around the current one; "Step 7 of 20" still counts all.
    readonly property int maxRows: 12
    // About what a card's title and why line hold at their sizes.
    readonly property int titleChars: 36
    readonly property int whyChars: 44

    function _window(route, cur) {
        if (route.length <= maxRows) return route
        const from = Math.max(0, Math.min((cur >= 0 ? cur : route.length - 1) - 4, route.length - maxRows))
        return route.slice(from, from + maxRows)
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

    // -- Watching: agentd's jobs --

    // The table as the desk keeps it: known states only, times as numbers or null, pct in 0..100 or null.
    function _cleanJobs(list) {
        const blank = v => v === null || v === undefined || v === "" || !isFinite(Number(v))
        const number = v => blank(v) ? null : Number(v)
        const seen = {}
        const out = []
        for (const j of list) {
            if (!j || typeof j !== "object" || j.id === undefined || j.id === null) continue
            const id = String(j.id)
            if (seen[id] || ["running", "done", "failed"].indexOf(j.state) < 0) continue
            seen[id] = true
            const pct = number(j.pct)
            out.push({ id: id, title: String(j.title || ""), state: j.state,
                       kind: j.kind === "watch" || j.kind === "timer" ? j.kind : "job",
                       started: number(j.started), deadline: number(j.deadline), ended: number(j.ended),
                       pct: pct === null ? null : Math.max(0, Math.min(100, pct)),
                       last: String(j.last || "") })
        }
        return out
    }

    // agentd's jobs message replaces the table. A row the person removed stays gone until a table
    // without it comes, which is agentd saying it stopped or dropped the job, so a table that was
    // already on its way cannot bring the row back for a moment.
    function _applyJobs(ev) {
        if (!Array.isArray(ev.jobs)) return
        const next = _cleanJobs(ev.jobs)
        const gone = {}
        for (const j of next) if (_gone[j.id]) gone[j.id] = true
        _gone = gone
        jobs = next
    }

    // The x on a row: stop a running job, drop a finished one. The row goes at once; the table that
    // comes back confirms it. A second press, or a row that is not there, sends nothing.
    function _removeJob(id) {
        const j = jobs.find(j => j.id === id)
        if (!j || _gone[id]) return
        outgoing({ type: "jobs", op: j.state === "running" ? "stop" : "dismiss", id: id })
        const gone = Object.assign({}, _gone)
        gone[id] = true
        _gone = gone
        goneTimer.restart()
        _refreshWatch()
    }

    // "40 s", "4 min", "2 h 10 min": how long a job has been going, or took.
    function _span(seconds) {
        const s = Math.max(0, Math.round(seconds))
        if (s < 60) return s + " s"
        const m = Math.floor(s / 60)
        if (m < 60) return m + " min"
        return Math.floor(m / 60) + " h" + (m % 60 > 0 ? " " + (m % 60) + " min" : "")
    }

    // "6:40", or "1:05:00" past the hour: what a timer has left, rounded up so 0:00 is when it is up.
    function _left(seconds) {
        const s = Math.max(0, Math.ceil(seconds - 1e-6))
        const pad = n => (n < 10 ? "0" : "") + n
        const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60)
        return (h > 0 ? h + ":" + pad(m) : m) + ":" + pad(s % 60)
    }

    // One job as the rows card draws it, at time t (epoch seconds).
    function _jobRow(j, t) {
        const name = j.kind === "timer" ? "Timer" : j.kind === "watch" ? "Watching" : "Job"
        const row = { key: j.id, kind: "dot", title: j.title || name, sub: "", meter: null, meterText: "",
                      tone: "machine", pulse: false, button: "", remove: true }
        const elapsed = j.started === null ? 0 : t - j.started
        if (j.state === "done") {
            row.tone = "ok"
            row.sub = j.ended === null || j.started === null ? "done"
                    : "done in " + _span(j.ended - j.started)
        } else if (j.state === "failed") {
            row.tone = "red"
            row.sub = j.last
            row.button = "Why?"
        } else if (j.kind === "timer" && j.deadline !== null) {
            row.tone = "sessions"
            row.sub = _left(j.deadline - t) + " left"
            row.pulse = true
        } else if (j.pct !== null) {
            row.kind = "meter"
            row.meter = j.pct / 100
            row.meterText = _span(elapsed)
        } else {
            row.sub = _span(elapsed) + " so far"
            row.pulse = true
        }
        return row
    }

    // Watching, from the table and the clock. A done row leaves doneStaysMs after it ended; a failed
    // one stays until it is dismissed. The card says how many are counting; its strip is the first
    // meter's first word and percent, or the count.
    function _refreshWatch() {
        const t = now / 1000
        const rows = []
        let counting = 0, failed = 0, meter = null
        for (const j of jobs) {
            if (_gone[j.id]) continue
            if (j.state === "done" && j.ended !== null && (t - j.ended) * 1000 >= doneStaysMs) continue
            const row = _jobRow(j, t)
            rows.push(row)
            if (j.state === "failed") failed++
            else if (j.state === "running") {
                counting++
                if (!meter && row.kind === "meter") meter = row
            }
        }
        let next = { title: "Watching", why: "", rows: [] }
        if (rows.length > 0) {
            const word = meter ? meter.title.trim().split(/\s+/)[0] : ""
            // With nothing counting, what is left is what finished: the strip says so, in its colour.
            const strip = counting === 0
                ? { text: "finished", dot: failed > 0 ? T.red : T.ok, ring: false }
                : { text: meter ? word + " " + Math.round(meter.meter * 100) + "%" : counting + " counting",
                    dot: T.machine, ring: true }
            const why = counting > 0 ? counting + " counting · each ends with one line in the pill"
                                     : "finished"
            next = { title: "Watching", why: why, rows: rows, strip: strip }
        }
        // Left alone when nothing changed, so a model that was set by hand is not written over.
        if (JSON.stringify(next) !== JSON.stringify(watchModel)) watchModel = next
    }

    onJobsChanged: _refreshWatch()
    onNowChanged: if (jobs.length > 0) _refreshWatch()

    // -- Needs you: the coding sessions that want you --

    // agentd's dev message: the sessions, and the keys that want you in Tab's order. What it leaves out
    // stays as it was.
    function _applyDev(ev) {
        if (Array.isArray(ev.sessions)) {
            sessions = ev.sessions.filter(s => s && typeof s === "object" && s.key !== undefined
                                               && s.key !== null)
        }
        if (Array.isArray(ev.attention))
            attention = ev.attention.filter(k => k !== undefined && k !== null).map(String)
        _refreshNeeds()
    }

    // "reviewer on Bombadil"; just the project for a session with no role.
    function _needsTitle(s) {
        const project = String(s.projectTitle || s.project || "")
        const role = String(s.role || "")
        return role && project ? role + " on " + project : project || role || String(s.key)
    }

    // One row for each key in attention, in its order; a key with no session is skipped. The row says
    // what the session asked, or the line it finished with, on one line.
    function _refreshNeeds() {
        const by = {}
        for (const s of sessions) by[String(s.key)] = s
        const rows = []
        const seen = {}
        for (const key of attention) {
            const s = by[key]
            if (!s || seen[key]) continue
            seen[key] = true
            const line = String(s.last || "").split(/\r?\n/).find(l => l.trim() !== "") || ""
            rows.push({ key: key, kind: "dot", title: _needsTitle(s), sub: _oneLine(line, 70), meter: null,
                        meterText: "", tone: "sessions", pulse: s.state === "asked", button: "Open",
                        remove: false })
        }
        const why = rows.length > 0 ? "Tab walks these · one alone is just the line" : ""
        const next = { title: "Needs you", why: why, rows: rows }
        if (JSON.stringify(next) !== JSON.stringify(needsModel)) needsModel = next
    }

    // -- Machine: agentd's vitals --

    // What a row may be and say; the card has no dot rows, no buttons and no x.
    readonly property var machineKinds: ["meter", "stack", "plain"]
    readonly property var machineTones: ["machine", "sessions", "you", "ok", "amber", "red"]
    readonly property int machineMaxRows: 6

    // A number held to 0..1, or null for anything that is not one.
    function _unit(v) {
        const n = typeof v === "number" || (typeof v === "string" && v.trim() !== "") ? Number(v) : NaN
        return isFinite(n) ? Math.max(0, Math.min(1, n)) : null
    }

    function _text(v) { return typeof v === "string" ? v : "" }
    function _machineTone(t) { return machineTones.indexOf(t) >= 0 ? t : "you" }

    // The pieces of a stack, each in a known tone and a fraction of the track. All of them together
    // are held to the whole track, so a message that adds up to more cannot draw past it, and a piece
    // with nothing in it is not one.
    function _cleanParts(list) {
        const out = []
        let left = 1
        for (const p of (Array.isArray(list) ? list : [])) {
            const f = p && typeof p === "object" ? _unit(p.fraction) : null
            const take = Math.min(f === null ? 0 : f, left)
            if (take <= 0) continue
            out.push({ tone: _machineTone(p.tone), fraction: take })
            left -= take
        }
        return out
    }

    // Known kinds only, a row with no key is dropped, and so is every row past the sixth.
    function _cleanMachineRows(list) {
        const out = []
        for (const r of (Array.isArray(list) ? list : [])) {
            if (out.length >= machineMaxRows) break
            if (!r || typeof r !== "object" || _text(r.key) === "" || machineKinds.indexOf(r.kind) < 0) continue
            if (out.some(o => o.key === r.key)) continue
            const row = { key: r.key, kind: r.kind, title: _text(r.title), sub: _text(r.sub),
                          meter: r.kind === "plain" ? null : _unit(r.meter), meterText: _text(r.meterText),
                          tone: _machineTone(r.tone), pulse: false, button: "", remove: false,
                          opens: _text(r.opens) }
            if (r.kind === "stack") row.parts = _cleanParts(r.parts)
            out.push(row)
        }
        return out
    }

    // agentd's machine message is the whole card, or says there is none: a calm machine, or nothing
    // on it the card can draw. Whatever it gets wrong is dropped.
    function _applyMachine(ev) {
        const rows = ev.present === true ? _cleanMachineRows(ev.rows) : []
        let next = null
        if (rows.length > 0) {
            next = { title: "Machine", why: _text(ev.why), rows: rows }
            const s = ev.strip && typeof ev.strip === "object" ? ev.strip : {}
            if (_text(s.text) !== "")
                next.strip = { text: s.text, dot: s.dot === "amber" ? T.amber : s.dot === "red" ? T.red : "",
                               ring: false, outlined: false }
        }
        // Left alone when nothing changed, so the card is not touched by a message that repeats itself.
        if (JSON.stringify(next) !== JSON.stringify(machineModel)) machineModel = next
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
            now: { visible: nowVisible, phase: _phase, model: nowModel },
            watching: watchModel, needs: needsModel, machine: machineModel, needsYou: needsYou,
            face: pill ? pill.face : ""
        }
    }
}
