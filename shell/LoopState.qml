import QtQuick

// What the bar knows about the self-improvement loop: what agentd says has been noticed, whether
// the chip and its card are up, and what the bar reports about itself (hello, alive, where its
// parts are, whether a summon got the keyboard, Esc that did not help). Plain QtQuick with no
// Quickshell types, like PillState, so tests can drive it offscreen.
// None of it is on a turn's path: a message that cannot be sent is dropped, never waited for, and
// nothing here starts, stops or delays a turn.
QtObject {
    id: loop

    // Messages for agentd; shell.qml writes them to the socket.
    signal outgoing(var msg)
    // He said "noticed": the card is kept up, and shell.qml picks the screen and takes the keyboard.
    signal opened()
    // Just connected: every bar window reports where its parts are.
    signal announce()

    // -- what agentd says (the "noticed" message) --
    property int count: 0            // what waits: the chip's number
    property bool hidden: false      // "hide noticed"
    property string resting: ""      // offers are resting until this day
    property string lately: ""       // "2 changes this week"
    property var rows: []            // at most three: {id, kind, title, meta, what, primary, others, forms}
    property var result: null        // the last answer to a tap: {op, id, ok, text, preview}
    property string pending: ""      // id of the row a tap is waiting on, so a second tap does nothing

    // -- what the bar knows --
    property bool connected: false
    property bool busy: false        // a turn runs, or Enter just started one
    property bool typing: false      // the pill has the keyboard
    property bool drawer: false      // a drawer is up, as far as the bar knows
    property int pid: 0
    property string build: ""

    // The chip: there while something waits and agentd is there to answer, never during a turn, and
    // it never turns up by itself while the pill has the keyboard (it comes when he is done
    // typing). One that is already up stays up.
    readonly property bool chipVisible: _shown
    property bool _shown: false

    // The card: peeked while the pointer is on the chip or the card, kept after a click or "noticed".
    property bool peeked: false
    property bool kept: false
    readonly property bool cardOpen: peeked || kept
    property string cardScreen: ""   // the screen whose chip opened it: the card shows there only
    property string focusedRow: ""   // id of the row whose "Other ways" are open
    readonly property int maxRows: 3
    readonly property var shownRows: rows.slice(0, maxRows)
    property int peekDelay: 250      // the pointer must rest this long, so crossing the chip does not flash it
    property int leaveDelay: 300     // and may take this long to get from the chip to the card

    property int aliveMs: 5000
    // The longest a summon may take to reach the keyboard and still be reported.
    property int focusWaitMs: 3000
    // Tests freeze the clock here (epoch ms); -1 is the real one.
    property double fixedNow: -1

    property bool _chipHover: false
    property bool _cardHover: false
    property bool _blockPeek: false  // the card was just closed by a click: no peek until the pointer leaves
    property string _hoverScreen: ""
    property var _summonId: null
    property double _summonAt: 0
    property var _esc: []            // when Esc was pressed in this burst (epoch ms)
    property bool _escSent: false
    property bool _escDrawer: false
    property bool _escCard: false
    property var _lastRects: ({})    // per screen: what agentd was last told

    function _now() { return fixedNow >= 0 ? fixedNow : Date.now() }

    // A list from agentd, however it came: JSON gives arrays, a test's variant lists only look like them.
    function _list(v) { return v && typeof v !== "string" && typeof v.length === "number" ? Array.from(v) : [] }

    // -- agentd's messages --

    function handle(ev) {
        if (!ev || typeof ev !== "object") return
        switch (ev.type) {
        case "noticed":
            _noticed(ev)
            break
        case "noticed_open":
            // His own word: show the card even when the chip is hidden or nothing waits.
            peeked = false
            kept = true
            opened()
            break
        case "noticed_result": {
            const p = ev.preview
            const op = String(ev.op || "")
            result = {
                op: op, id: String(ev.id === undefined || ev.id === null ? "" : ev.id),
                ok: ev.ok !== false, text: String(ev.text || ""),
                // The report and the send hand over to the Noticed window, which shows the whole
                // text: the card never draws it (a full report is taller than the screen).
                preview: op === "report" || op === "send" ? "" : (typeof p === "string" ? p : (p && p.text ? String(p.text) : ""))
            }
            pending = ""
            break
        }
        case "summon":
            // Remember when it reached the bar; focus_ack says how long the keyboard took.
            _summonId = ev.id === undefined ? null : ev.id
            _summonAt = _now()
            break
        }
    }

    function _noticed(ev) {
        const had = rows.length
        rows = _list(ev.rows).filter(r => r && typeof r === "object")
        // "hide noticed" puts the card away too.
        if (ev.hidden && !hidden) { kept = false; peeked = false }
        hidden = !!ev.hidden
        count = Math.max(0, parseInt(ev.count) || 0)
        resting = String(ev.resting || "")
        lately = String(ev.lately || "")
        pending = ""
        if (focusedRow !== "" && !rows.some(r => String(r.id) === focusedRow)) focusedRow = ""
        if (result && !rows.some(r => String(r.id) === result.id)) result = null
        // The last thing that waited is done: the card has nothing more to say.
        if (had > 0 && rows.length === 0) { kept = false; peeked = false }
    }

    // -- the chip --

    function _refresh() {
        const want = count > 0 && !hidden && !busy && connected
        if (!want) _shown = false
        else if (!_shown && !typing) _shown = true
        // A turn started: the card goes with the chip. A peek ends with its chip.
        if (busy) { kept = false; peeked = false }
        else if (!_shown && !kept) peeked = false
    }
    onCountChanged: _refresh()
    onHiddenChanged: _refresh()
    onBusyChanged: _refresh()
    onTypingChanged: _refresh()

    // -- the card --

    function hoverChip(screen, on) {
        _chipHover = on
        if (on) {
            _hoverScreen = screen
            _leave.stop()
            if (!peeked && !_blockPeek) _peek.restart()
        } else {
            _blockPeek = false
            _peek.stop()
            _leaveCheck()
        }
    }

    function hoverCard(screen, on) {
        _cardHover = on
        if (on) _leave.stop()
        else _leaveCheck()
    }

    function _leaveCheck() { if (!_chipHover && !_cardHover && peeked) _leave.restart() }

    // (A QtObject has no default property: the timers are properties.)
    property Timer _peek: Timer {
        interval: loop.peekDelay
        onTriggered: {
            if (!loop._chipHover || !loop.chipVisible || loop._blockPeek || loop.kept) return
            loop.cardScreen = loop._hoverScreen
            loop.peeked = true
        }
    }
    property Timer _leave: Timer {
        interval: loop.leaveDelay
        onTriggered: if (!loop._chipHover && !loop._cardHover) loop.peeked = false
    }

    // A click on the chip keeps the card up and asks agentd for the Noticed window; another click
    // puts the card away.
    function chipClicked(screen) {
        if (kept && cardScreen === screen) { closeCard(); return }
        peeked = false
        kept = true
        cardScreen = screen
        _send({ type: "noticed_do", op: "open" })
    }

    function closeCard() {
        kept = false
        peeked = false
        focusedRow = ""
        pending = ""
        // The pointer is still on the chip: it must leave before the card peeks again.
        _blockPeek = _chipHover
        _peek.stop()
        _leave.stop()
    }

    // The pill gave the keyboard back (a click elsewhere, idle): a card kept by a click goes too.
    function keyboardLost(screen) { if (kept && cardScreen === screen) closeCard() }

    function toggleWays(row) { focusedRow = row && focusedRow !== String(row.id) ? String(row.id) : "" }

    function openWindow() { _send({ type: "noticed_do", op: "open" }) }

    // -- what a row can do --

    function _found(row) { return !!row && (row.kind === "found" || row.kind === "report") }

    function _has(v) { return v !== undefined && v !== null && v !== "" }

    // The one button: the row's own, else what its kind would say.
    function primary(row) {
        const p = row && row.primary && typeof row.primary === "object" ? row.primary : {}
        const found = _found(row)
        const fallback = found ? "Send to the project" : (row && row.kind === "change" ? "Use it" : "Make it")
        const out = { label: String(p.label || fallback), op: String(p.op || (found ? "report" : "accept")) }
        if (_has(p.form)) out.form = p.form
        return out
    }

    // The other ways to do it, from the row's own list, else from its forms without the one the
    // primary button already does. At most two: the card is a peek, not a menu.
    function others(row) {
        if (!row) return []
        const p = primary(row)
        let list = _list(row.others).filter(o => o && o.label && o.op !== "not_now" && o.op !== "never" && o.op !== "other_ways")
        if (list.length === 0) {
            list = _list(row.forms).filter(f => f && f.form && !f.recommended && f.form !== p.form)
                .map(f => ({ label: f.label || f.form, op: "accept", form: f.form }))
        }
        return list.slice(0, 2).map(o => {
            const out = { label: String(o.label), op: String(o.op || "accept") }
            if (_has(o.form)) out.form = o.form
            return out
        })
    }

    // The quiet buttons: a change has only Not now; "Got it" has none; the rest can be said no to.
    function quiet(row) {
        if (!row || primary(row).op === "got_it") return []
        const notNow = { label: "Not now", op: "not_now" }
        return row.kind === "change" ? [notNow] : [notNow, { label: "Never", op: "never" }]
    }

    function act(row, spec) {
        // Pressing the button is the ask: no second question, and no second press while it is in.
        if (!row || !spec || !connected || pending === String(row.id)) return
        const msg = { type: "noticed_do", op: spec.op, id: row.id }
        if (_has(spec.form)) msg.form = spec.form
        result = null
        pending = String(row.id)
        _send(msg)
        // These hand over to the Noticed window: the card has done its part.
        if (spec.op === "report" || spec.op === "send") closeCard()
    }

    function press(row) { act(row, primary(row)) }
    function choose(row, way) { act(row, { op: way.op || "accept", form: way.form }) }
    function notNow(row) { act(row, { op: "not_now" }) }
    function never(row) { act(row, { op: "never" }) }

    // What the bar shows of this, as JSON for the headless desktop test (`quickshell ipc call loop state`).
    function snapshot() {
        return {
            connected: connected, count: count, hidden: hidden, chip: chipVisible, card: cardOpen,
            kept: kept, pending: pending, resting: restingLine(), lately: latelyLine(), why: why(),
            rows: shownRows.map(r => ({ id: String(r.id), kind: r.kind, title: String(r.title || ""),
                                        meta: String(r.meta || ""), what: String(r.what || ""),
                                        primary: primary(r).label })),
            result: result
        }
    }
    function rowById(id) { return rows.find(r => String(r.id) === String(id)) || null }

    // -- the words on the card --

    // "1 idea · 1 change to look at", from the kinds of the rows it shows.
    function why() {
        const n = { offer: 0, change: 0, found: 0, report: 0 }
        for (const r of shownRows) n[n[r.kind] === undefined ? "offer" : r.kind]++
        const parts = []
        if (n.offer) parts.push(n.offer + (n.offer === 1 ? " idea" : " ideas"))
        if (n.change) parts.push(n.change + (n.change === 1 ? " change to look at" : " changes to look at"))
        if (n.found) parts.push(n.found + (n.found === 1 ? " thing it found" : " things it found"))
        if (n.report) parts.push(n.report + (n.report === 1 ? " report to send" : " reports to send"))
        return parts.length > 0 ? parts.join(" · ") : "Nothing waiting"
    }

    function latelyLine() {
        const s = String(lately || "").replace(/^\s*lately:?\s*/i, "").trim()
        return s !== "" ? "Lately: " + s : ""
    }

    // agentd says "3 Nov" or a whole line; an ISO date or epoch seconds read the same way.
    function restingLine() {
        const s = String(resting || "").trim()
        if (s === "") return ""
        if (/^resting/i.test(s)) return s
        const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        let day = s
        const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(s)
        if (iso) day = parseInt(iso[3]) + " " + months[parseInt(iso[2]) - 1]
        else if (/^\d{9,11}(\.\d+)?$/.test(s)) {
            const d = new Date(parseFloat(s) * 1000)
            day = d.getDate() + " " + months[d.getMonth()]
        }
        return "Resting offers until " + day
    }

    // -- what the bar says about itself --

    function _send(msg) { outgoing(msg) }

    function hello() { _send({ type: "hello", client: "bar", pid: pid, build: build }) }

    onConnectedChanged: {
        if (connected) { _lastRects = ({}); _hello.restart() }
        else {
            // Nobody to answer a tap: the card goes with the chip, so it is never up with buttons that do nothing.
            _hello.stop()
            pending = ""
            kept = false
            peeked = false
            focusedRow = ""
            result = null
        }
        _refresh()
    }

    // A zero timer, not a call: the Socket that just connected may not be stored yet.
    property Timer _hello: Timer {
        interval: 0
        onTriggered: {
            loop.hello()
            loop._send({ type: "noticed_state" })
            loop.announce()
        }
    }

    property Timer _alive: Timer {
        interval: loop.aliveMs
        repeat: true
        running: loop.connected
        onTriggered: loop._send({ type: "alive", t: loop._now() / 1000 })
    }

    // Where this bar's parts are, in the screen's own pixels, for the prober to set against the
    // windows. `items` is [[name, item], …]; `dy` is how far the window's top is below the
    // screen's top (the layer sits on the bottom edge). Only what is showing is reported, and
    // only when it differs from what agentd last heard for that screen.
    function reportRects(screen, w, h, dy, items) {
        const rects = []
        for (const pair of items) {
            const it = pair[1]
            if (!it || !it.visible || it.width < 1 || it.height < 1) continue
            const p = it.mapToItem(null, 0, 0)
            rects.push({ name: pair[0], x: Math.round(p.x), y: Math.round(p.y + dy),
                         w: Math.round(it.width), h: Math.round(it.height) })
        }
        const key = JSON.stringify([w, h, rects])
        if (_lastRects[screen] === key) return
        _lastRects[screen] = key
        _send({ type: "rects", screen: screen, w: w, h: h, rects: rects })
    }

    // The input took the keyboard. After a summon that is what agentd wants to hear, with how long
    // it took; a keyboard that comes much later was not the summon's, so nothing is sent.
    function inputFocused() {
        if (_summonId === null) return
        const id = _summonId
        const ms = Math.round(_now() - _summonAt)
        _summonId = null
        if (ms >= 0 && ms <= focusWaitMs) _send({ type: "focus_ack", id: id, ms: ms })
    }

    // The summon was answered by giving the keyboard back (a second tap on Super): no focus_ack is
    // coming and none was meant to, and agentd is told so, or it would write the summon up as one
    // that never got the keyboard.
    function summonCancelled() {
        if (_summonId === null) return
        const id = _summonId
        _summonId = null
        _send({ type: "focus_cancel", id: id })
    }

    // Esc was pressed in the pill. Three within 10 s while a drawer or the card is up says the
    // thing will not go away: agentd hears it once per burst. Returns true when it put a kept card
    // away, so the pill does nothing else with that press.
    function esc() {
        const t = _now()
        const last = _esc.length > 0 ? _esc[_esc.length - 1] : -1
        if (last >= 0 && t - last > 10000) { _esc = []; _escSent = false }
        // A burst starts with something up; after that, every press within 10 s of the last belongs to it.
        if (_esc.length > 0 || drawer || cardOpen) {
            // What was up when it began: a kept card is put away by the first press, so asking later says "nothing".
            if (_esc.length === 0) { _escDrawer = drawer; _escCard = cardOpen }
            _esc = _esc.concat([t])
            const recent = _esc.filter(x => t - x <= 10000)
            if (recent.length >= 3 && !_escSent) {
                _escSent = true
                _send({ type: "friction", what: "esc", count: recent.length, seconds: 10, drawer: _escDrawer, card: _escCard })
            }
        }
        if (kept) { closeCard(); return true }
        return false
    }
}
