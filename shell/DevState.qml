import QtQuick

// The coding sessions, as agentd's "dev" messages describe them (dev.py): a chip per project
// with a dot per session, and the one line the pill shows while a session waits for you.
// Plain QtQuick with no Quickshell types, so tests can drive it offscreen.
QtObject {
    id: dev

    // Messages for agentd; shell.qml writes them to the socket.
    signal outgoing(var msg)

    property var sessions: []        // [{key, project, projectTitle, role, tool, toolTitle, title, state, alive,
                                     //   unseen, yours, copy, since, last, lines}]
    property var attention: []       // keys waiting for you, in Tab's order
    property string line: ""         // "reviewer on Bombadil: run the migration?" ("" = nothing waits)
    property string front: ""        // the session whose window is in front
    property string peeked: ""       // the session whose dot is hovered
    property bool expanded: false    // the folded chip was opened
    property int foldAbove: 3        // more projects than this fold into one chip

    readonly property var projects: _group(sessions)
    readonly property bool folded: projects.length > foldAbove && !expanded
    readonly property int waiting: attention.length
    readonly property string foldText: projects.length + " projects" + (waiting > 0 ? " · " + waiting + " waiting" : "")
    readonly property var peekSession: _find(peeked)

    function handle(ev) {
        if (!ev || ev.type !== "dev") return
        sessions = ev.sessions || []
        attention = ev.attention || []
        line = ev.line || ""
        front = ev.front || ""
        if (peeked !== "" && !_find(peeked)) peeked = ""
        if (projects.length <= foldAbove) expanded = false
    }

    // The bar lost agentd: the sessions live on in their own scopes, so the dots stay until
    // agentd is back and says otherwise; only the line waits for it.
    function lost() { line = "" }

    function _find(key) {
        if (key === "") return null
        for (const s of sessions) if (s.key === key) return s
        return null
    }

    function _group(list) {
        const out = []
        const at = {}
        for (const s of list) {
            const k = String(s.project || "").toLowerCase()
            if (at[k] === undefined) {
                at[k] = out.length
                out.push({ project: s.project, title: s.projectTitle || s.project, sessions: [], waiting: 0 })
            }
            const p = out[at[k]]
            p.sessions.push(s)
            if (attention.indexOf(s.key) >= 0) p.waiting += 1
        }
        return out
    }

    // How a dot looks: "working" moves, "turn" is lit, "failed" is red, "asleep" is dim.
    function look(s) {
        if (!s) return "idle"
        if (s.state === "working") return "working"
        if (s.state === "asked" || (s.state === "done" && s.unseen)) return "turn"
        if (s.state === "failed") return "failed"
        if (s.state === "asleep" || !s.alive) return "asleep"
        return "idle"
    }

    function color(s) {
        switch (look(s)) {
        case "working": return "#d97757"
        case "turn": return "#ffd9b8"
        case "failed": return "#d0504a"
        case "asleep": return "#4a525c"
        }
        return "#8b939c"
    }

    // The words under a hovered dot: what it is, what it is doing, and its last few lines.
    function stateWords(s) {
        if (!s) return ""
        const what = {
            working: "working", idle: "ready", asked: "waiting for you", done: s.unseen ? "finished" : "ready",
            failed: "stopped", asleep: "asleep", ended: "ended"
        }[s.state] || s.state
        const where = s.yours ? " · yours" : (s.copy ? " · own copy" : "")
        return s.toolTitle + " · " + what + where
    }

    function peekLines(s) {
        if (!s) return ""
        const lines = (s.lines || []).slice(-3)
        if (s.last && lines.join("\n").indexOf(s.last) < 0) lines.push(s.last)
        return lines.join("\n")
    }

    // -- what the dots and Tab do --

    function next() { outgoing({ type: "dev", action: "next" }) }

    function open(key) {
        const s = _find(key)
        if (!s) return
        // A red dot that stopped shows its last screen first ("Why?"); typing its name resumes it.
        outgoing({ type: "dev", action: s.state === "failed" && !s.alive ? "why" : "open", key: key })
    }

    function peek(key) { peeked = key }
    function unpeek(key) { if (peeked === key) peeked = "" }
}
