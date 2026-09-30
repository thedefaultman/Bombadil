import QtQuick

// What the pill and the line above it show, worked out from agentd's events.
// Plain QtQuick with no Quickshell types, so tests can drive it offscreen.
QtObject {
    id: pill

    // Messages for agentd; shell.qml writes them to the socket.
    signal outgoing(var msg)
    // agentd asked the bar to take the keyboard (Super was tapped), or an app asked for the
    // pill with words already in it ("About ~/lease.pdf: ", from the Brain's Ask about this).
    signal summoned(string text)
    // The drawer is opening: the bar gives the keyboard back so the drawer can take it.
    signal handOff()

    property bool connected: false
    property bool busy: false
    property string provider: ""
    property var entries: []         // launcher words: [{name, title, kind, words}]
    property var queue: []           // prompts waiting their turn: [{turn, prompt}]

    // Whether the machine can talk to its AI yet (agentd's "setup"): which AI (first boot),
    // signed in, the sign-in under way in the browser. Its line shows when no turn runs, with
    // chips under it; prompts typed meanwhile wait in the queue.
    property string setupState: ""   // choose, checking, signed_out, offline, signing_in, ready
    property string setupLine: ""
    property string setupTone: "step" // step, ask, error, done
    property var setupActions: []    // chips: [{id, label, style: big | primary | quiet}]
    readonly property bool ready: setupState === "" || setupState === "ready" || setupState === "checking"

    // The line: "working" while a turn runs, "closing" for how it ended, "local" for an
    // open/undo/stop answered without the model, "setup" for choosing the AI and signing in,
    // "idle" when there is nothing to say.
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
    property int hovers: 0           // lines being hovered, on any screen: none fades meanwhile
    // Esc and the Stop dot act while a turn runs, from the moment Enter showed "On it", and
    // while a sign-in is under way (they call it off).
    readonly property bool stoppable: busy || optimistic || setupState === "signing_in"

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
        if (ev.type === "setup") { _setup(ev); return }
        if (ev.type === "summon") { summoned(typeof ev.text === "string" ? ev.text : ""); return }
        if (ev.type === "local") {
            // agentd answered our prompt without the model: no turn is coming.
            if (optimistic) { optimistic = false; mode = "local"; line = "…"; source = "step"; sticky = false; lineAt = _now() }
            return
        }
        if (ev.type === "queued") {
            // agentd accepted our prompt as this turn: follow it, even if it ends before it starts.
            if (optimistic && turn === null) turn = ev.turn
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
                else { optimistic = false; mode = "local"; line = _firstLines(ev.text, 2); source = "error"; sticky = false; lineAt = _now() }
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
            optimistic = false
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

    function _showSetup() {
        mode = "setup"; line = setupLine; source = setupTone === "error" ? "error" : "step"
        risk = ""; command = ""; sticky = false; flash = ""
    }

    function _setup(ev) {
        const was = setupState, wasLine = setupLine
        setupState = ev.state || ""
        setupLine = ev.line || ""
        setupTone = ev.tone || "step"
        setupActions = ev.actions || []
        if (mode === "working" && !optimistic) return   // a turn has the line; the setup waits for it
        if (!ready && setupLine) {
            optimistic = false
            _showSetup()
        } else if (setupState === "ready" && setupLine && was !== "" && (was !== "ready" || setupLine !== wasLine)) {
            // "Signed in to Claude. Ask me for anything.": said once, then it fades. (Not said
            // again to a bar that has just started: it was not there for it.)
            optimistic = false
            mode = "local"; line = setupLine; source = "step"; risk = ""; command = ""
            sticky = false; lineAt = _now(); fadeAfter = 8000
        } else if (mode === "setup") {
            mode = "idle"; line = ""
        }
    }

    function setupAction(id) {
        if (_offline()) return
        // The sign-in page and the Wi-Fi list open a window that must take the keyboard (a summoned
        // pill holds it, and the password would go into the pill); Cancel opens nothing.
        if (id !== "cancel") handOff()
        outgoing({ type: "setup_action", id: id })
    }

    function submit(text) {
        const t = String(text || "").trim()
        if (!t) return false
        if (!connected) {
            mode = "local"; line = "Not connected to the agent yet."; source = "error"; sticky = false
            lineAt = _now(); fadeAfter = 4000
            return false
        }
        outgoing({ type: "prompt", text: t })
        if (!ready && !t.startsWith("!")) {
            // No AI to answer yet: the prompt waits in the queue, the setup line stays (or
            // comes back over a finished line that would hide it).
            if (mode === "closing" || mode === "local") {
                sticky = false
                if (setupLine) _showSetup()
            }
            return true
        }
        if (!busy && mode !== "working") {
            // Something true on screen at once; agentd confirms with turn_start (or a local answer).
            optimistic = true
            mode = "working"; line = "On it"; source = "step"; risk = ""; command = ""
            startedAt = _now(); turn = null; sticky = false; stopped = false
            flash = ""   // a "Stopped while…" still showing would hide "On it"
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

    // Nothing reaches agentd while the socket is down: say so, and keep what is on screen.
    function _offline() {
        if (connected) return false
        flash = "Not connected to the agent yet."; flashAt = _now()
        return true
    }

    function stop() {
        if (_offline()) return
        outgoing({ type: "stop" })
        if (mode === "working") { line = "Stopping"; source = "step"; risk = ""; command = "" }
    }

    function unqueue(t) {
        if (_offline()) return
        outgoing({ type: "unqueue", turn: t })
        _setQueue(queue.filter(q => q.turn !== t))
    }

    function undo() {
        if (_offline()) return
        outgoing({ type: "local", action: "undo" }); sticky = false
    }

    // Details opens the drawer, and closes it when it already shows this turn.
    function details() {
        if (_offline()) return
        handOff()
        outgoing({ type: "details", turn: turn })
    }

    // Esc in the pill with nothing to stop or clear: put the drawer away too.
    function closeDetails() { if (connected) outgoing({ type: "close_details" }) }

    function dismiss() {
        if (mode === "closing" || mode === "local") {
            sticky = false
            // Not signed in yet: the setup line comes back rather than an empty pill.
            if (!ready && setupLine) _showSetup()
            else { mode = "idle"; line = "" }
        }
        flash = ""
    }

    // -- the launcher: completing and recognising names --

    readonly property var _verbs: ["open ", "show ", "launch ", "start ", "close ", "quit ", "hide "]
    // A widget's name means the desk only after one of these ("start now" is for the agent).
    readonly property var _widgetVerbs: ["open ", "show ", "close ", "hide "]

    function _split(text) {
        const t = String(text || "").toLowerCase().replace(/^\s+/, "")
        for (const v of _verbs)
            if (t.startsWith(v)) return { verb: v, rest: t.slice(v.length) }
        return { verb: "", rest: t }
    }

    // As launcher.py's _key: only spaces, hyphens and underscores fold away; any other sign
    // or letter outside a-z makes it no launcher word ("" never matches).
    function _key(s) {
        const k = String(s || "").toLowerCase().replace(/[\s_-]+/g, "")
        return /^[a-z0-9]+$/.test(k) ? k : ""
    }

    function _title(s) { return String(s || "").trim().split(/\s+/).join(" ").toLowerCase() }

    // The rest of the word Tab would fill in, or "".
    function completion(text) {
        const s = _split(text)
        if (s.rest.length < 2) return ""
        for (const e of entries) {
            if (s.verb && e.kind === "command") continue
            if (e.kind === "widget" && _widgetVerbs.indexOf(s.verb) < 0) continue
            for (const w of (e.words || [])) {
                if (w.length > s.rest.length && w.startsWith(s.rest)) return w.slice(s.rest.length)
            }
        }
        return ""
    }

    // What an exact launcher word would open ("Passwords"), or "" when it goes to the agent.
    function exact(text) {
        if (String(text || "").trim().startsWith("!")) return ""
        const s = _split(text)
        const rest = s.rest.trim().replace(/[.!?,;:]+$/, "")
        const k = _key(rest)
        const title = _title(rest)
        if (!k && !title) return ""
        // A question about the desk ("desk?", "show machine?") goes to the agent, not the launcher.
        const asks = /\?\s*$/.test(String(text || ""))
        for (const e of entries) {
            if (e.kind === "app" && title === _title(e.title)) return e.title || e.name
            if (!k || (s.verb && e.kind === "command")) continue
            if (e.kind === "widget" && (asks || _widgetVerbs.indexOf(s.verb) < 0)) continue
            if (asks && e.name === "desk") continue
            for (const w of (e.words || []).concat([e.name, String(e.title || "").toLowerCase()])) {
                if (_key(w) === k) return e.title || e.name
            }
        }
        return ""
    }
}
