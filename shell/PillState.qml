import QtQuick

// What the pill and the line above it show, worked out from agentd's events.
// Plain QtQuick with no Quickshell types, so tests can drive it offscreen.
QtObject {
    id: pill

    // Messages for agentd; shell.qml writes them to the socket.
    signal outgoing(var msg)
    // agentd asked the bar to take the keyboard (Super was tapped).
    signal summoned()

    property bool connected: false
    property bool busy: false
    property string provider: ""
    property var entries: []         // launcher words: [{name, title, kind, words}]
    property var queue: []           // prompts waiting their turn: [{turn, prompt}]

    // The line: "working" while a turn runs, "closing" for how it ended, "local" for an
    // open/undo/stop answered without the model, "idle" when there is nothing to say.
    property string mode: "idle"
    property string line: ""
    property string source: "step"   // step (plain words), agent (its own words), error
    property string risk: ""         // "", "system" (amber) or "irreversible" (red)
    property string command: ""      // the exact command under a marked step
    property double startedAt: 0     // for the seconds counter
    property var turn: null
    property bool optimistic: false  // "On it" shown before agentd confirmed a turn
    property bool changed: false
    property bool irreversible: false
    property bool stopped: false
    property bool sticky: false      // a closing line that stays until the next prompt
    property double lineAt: 0        // when the closing or local line appeared
    property int fadeAfter: 12000    // how long a closing or local line stays (ms)
    property string flash: ""        // a local answer shown over a running turn for a moment
    property double flashAt: 0

    property string _result: ""
    property bool _resultOk: true
    property string _error: ""

    function _now() { return Date.now() }

    function _firstLines(text, n) {
        const lines = String(text || "").trim().split("\n").map(s => s.trim()).filter(s => s.length > 0)
        return lines.slice(0, n).join("\n")
    }

    function _setQueue(q) { queue = q }

    function handle(ev) {
        if (!ev || typeof ev !== "object") return
        if (ev.type === "status") {
            connected = true
            busy = !!ev.busy
            provider = ev.provider || ""
            _setQueue(ev.queue || [])
            if (ev.busy && mode !== "working" && ev.turn !== undefined && ev.turn !== null) {
                // The bar (re)connected in the middle of a turn.
                mode = "working"; turn = ev.turn; line = "Working"; source = "step"
                risk = ""; command = ""; startedAt = _now()
            }
            return
        }
        if (ev.type === "entries") { entries = ev.entries || []; return }
        if (ev.type === "summon") { summoned(); return }
        if (ev.type === "local") {
            // agentd answered our prompt without the model: no turn is coming.
            if (optimistic) { optimistic = false; mode = "local"; line = "…"; source = "step"; lineAt = _now() }
            return
        }
        if (ev.type !== "event") return
        switch (ev.kind) {
        case "queued":
            if (!queue.some(q => q.turn === ev.turn))
                _setQueue(queue.concat([{ turn: ev.turn, prompt: ev.prompt || "" }]))
            break
        case "unqueued":
            _setQueue(queue.filter(q => q.turn !== ev.turn))
            break
        case "turn_start":
            _setQueue(queue.filter(q => q.turn !== ev.turn))
            if (!optimistic || mode !== "working") startedAt = _now()
            optimistic = false
            mode = "working"; turn = ev.turn; busy = true
            line = "On it"; source = "step"; risk = ""; command = ""
            changed = false; irreversible = false; stopped = false; sticky = false
            _result = ""; _resultOk = true; _error = ""
            break
        case "status":
            if (mode !== "working" || (ev.turn !== undefined && ev.turn !== turn)) break
            line = ev.text || line
            source = ev.source || "step"
            risk = ev.risk || ""
            command = ev.command || ""
            break
        case "error":
            if (ev.turn === null || ev.turn === undefined) {
                if (mode === "working" && !optimistic) { flash = _firstLines(ev.text, 1); flashAt = _now() }
                else { optimistic = false; mode = "local"; line = _firstLines(ev.text, 2); source = "error"; lineAt = _now() }
            } else if (ev.turn === turn) {
                _error = ev.text || ""
            }
            break
        case "result":
            if (ev.turn !== turn) break
            _result = ev.text || ""
            _resultOk = ev.ok !== false
            break
        case "turn_end":
            if (ev.turn !== turn) break
            busy = false
            stopped = !!ev.stopped
            changed = !!ev.changed
            irreversible = !!ev.irreversible
            risk = ""; command = ""
            if (stopped) {
                line = ev.line || "Stopped."; source = "step"
                // A queued prompt starts at once; still say what was stopped for a moment.
                flash = line; flashAt = _now()
            }
            else if (_error && !_resultOk || (_error && !_result)) { line = _firstLines(_error, 2); source = "error" }
            else if (_result) { line = _result.trim(); source = "agent" }
            else if (ev.summary) { line = ev.summary; source = "step" }
            else { line = "Done."; source = "step" }
            // A turn that changed something keeps its line, with Undo, until the next prompt.
            sticky = changed && !stopped
            mode = "closing"; lineAt = _now(); fadeAfter = 12000
            break
        case "local":
            if (mode === "working" && !optimistic) {
                flash = ev.text || ""; flashAt = _now()
            } else {
                optimistic = false
                mode = "local"; line = ev.text || ""; source = ev.ok === false ? "error" : "step"
                risk = ""; command = ""; sticky = false; lineAt = _now()
                // Undo says what it covered; give people time to read it.
                fadeAfter = ev.action === "undo" && ev.phase === "done" ? 15000 : 5000
            }
            break
        }
    }

    function submit(text) {
        const t = String(text || "").trim()
        if (!t) return false
        if (!connected) {
            mode = "local"; line = "Not connected to the agent yet."; source = "error"; lineAt = _now(); fadeAfter = 4000
            return false
        }
        outgoing({ type: "prompt", text: t })
        if (!busy && mode !== "working") {
            // Something true on screen at once; agentd confirms with turn_start (or a local answer).
            optimistic = true
            mode = "working"; line = "On it"; source = "step"; risk = ""; command = ""
            startedAt = _now(); turn = null; sticky = false; stopped = false
        } else if (mode === "closing" || mode === "local") {
            mode = "idle"
        }
        return true
    }

    // The socket dropped: agentd restarted or died. Its status says what runs when it is back.
    function lost() {
        connected = false
        busy = false
        optimistic = false
        if (mode === "working") {
            mode = "local"; line = "Lost touch with the agent. Reconnecting."; source = "error"
            risk = ""; command = ""; lineAt = _now(); fadeAfter = 8000
        }
    }

    function stop() {
        outgoing({ type: "stop" })
        if (mode === "working") { line = "Stopping"; source = "step"; risk = ""; command = "" }
    }

    function unqueue(t) {
        outgoing({ type: "unqueue", turn: t })
        _setQueue(queue.filter(q => q.turn !== t))
    }

    function undo() { outgoing({ type: "local", action: "undo" }); sticky = false }

    function details() { outgoing({ type: "details", turn: turn }) }

    function dismiss() {
        if (mode === "closing" || mode === "local") { mode = "idle"; line = ""; sticky = false }
        flash = ""
    }

    // -- the launcher: completing and recognising names --

    readonly property var _verbs: ["open ", "show ", "launch ", "start ", "close ", "quit ", "hide "]

    function _split(text) {
        const t = String(text || "").toLowerCase().replace(/^\s+/, "")
        for (const v of _verbs)
            if (t.startsWith(v)) return { verb: v, rest: t.slice(v.length) }
        return { verb: "", rest: t }
    }

    // The rest of the word Tab would fill in, or "".
    function completion(text) {
        const s = _split(text)
        if (s.rest.length < 2) return ""
        for (const e of entries) {
            if (s.verb && e.kind === "command") continue
            for (const w of (e.words || [])) {
                if (w.length > s.rest.length && w.startsWith(s.rest)) return w.slice(s.rest.length)
            }
        }
        return ""
    }

    // What an exact launcher word would open ("Passwords"), or "" when it goes to the agent.
    function exact(text) {
        const s = _split(text)
        const k = s.rest.trim().replace(/[.!?]+$/, "").replace(/[^a-z0-9]+/g, "")
        if (!k) return ""
        for (const e of entries) {
            if (s.verb && e.kind === "command") continue
            for (const w of (e.words || []).concat([e.name, String(e.title || "").toLowerCase()])) {
                if (String(w).replace(/[^a-z0-9]+/g, "") === k) return e.title || e.name
            }
        }
        return ""
    }
}
