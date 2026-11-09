.pragma library

// A tip's scene as a function of time: compile(beats) lays the beats end to end, frameAt(compiled, t)
// says what is on the mini screen at t ms. Nothing here draws and nothing here runs: TipScene.qml binds
// what it draws to the frame, so a frame at any t is the same every time, and a test (or a still)
// asks for one without waiting. Every number in the timing section is mirrored in
// src/bombadil/tip_scene.py, which validates scenes; tests/test_tip_scene.py checks they agree.

// -- timing, in milliseconds --

var KEY_MS = 360
var KEY_PRESS_AT = 120          // when the key is down, and its effect lands
var TYPE_MS_PER_CHAR = 40       // 25 characters a second
var TYPE_SETTLE_MS = 240
var STONE_MS = { done: 700, needs: 1600, working: 600 }
var STONE_DEFAULT_MS = 300
var LINE_MS = 900
var LINE_CLEAR_MS = 200
var FADE_MS = 200
var SLIDE_MS = 300
var ROW_MS = 200
var WINDOW_DWELL_MS = 300
var WINDOW_HIDE_MS = 300
var CARD_MS = 800
var CARD_ARRIVE_MS = 150
var BOX_MS = 320
var PICTURE_DWELL_MS = 600
var FOLD_MS = 1000
var FOLD_FADE_MS = 150
var LEAN_MS = 240

var KEY_LABELS = { super: "⊞", enter: "↵", esc: "Esc", tab: "Tab", bang: "!" }
var FACES = ["rest", "listening", "working", "needs", "done", "stopped", "resting"]
var DRAWS = ["key", "type", "stone", "line", "window", "card", "picture", "fold", "hold"]
var MODIFIERS = ["ghost", "tag", "tone", "command", "counter", "buttons", "rows", "columns", "lock", "doc", "reload",
                 "from", "title", "why", "boxes", "bars", "lit", "strips", "phase"]

function list(v) { return v && typeof v !== "string" && v.length !== undefined ? Array.prototype.slice.call(v) : [] }
function plain(v) { return v === undefined || v === null ? undefined : v }
function showsWindow(b) { return typeof b.window === "string" && b.window !== "" }

function beatMs(b) {
    var parts = []
    if (b.key !== undefined) parts.push(KEY_MS)
    if (typeof b.type === "string") parts.push(TYPE_MS_PER_CHAR * b.type.length + TYPE_SETTLE_MS)
    if (b.stone !== undefined) parts.push(STONE_MS[b.stone] !== undefined ? STONE_MS[b.stone] : STONE_DEFAULT_MS)
    if (b.line !== undefined) parts.push(b.line !== "" ? LINE_MS : LINE_CLEAR_MS)
    if (b.window !== undefined) {
        if (showsWindow(b)) {
            parts.push((b.reload ? 0 : SLIDE_MS) + ROW_MS * list(b.rows).length + ROW_MS * list(b.columns).length
                       + WINDOW_DWELL_MS)
        } else parts.push(WINDOW_HIDE_MS)
    }
    if (b.card !== undefined) parts.push(CARD_MS)
    if (b.picture !== undefined)
        parts.push(SLIDE_MS + BOX_MS * Math.max(list(b.boxes).length, list(b.bars).length) + PICTURE_DWELL_MS)
    if (b.fold !== undefined) parts.push(FOLD_MS)
    var base = parts.length ? Math.max.apply(null, parts) : 0
    if (b.hold !== undefined && typeof b.hold === "number" && b.hold > 0) base += b.hold
    return base
}

// The beats laid end to end: {beats: [{b, start, ms}], duration, phase1, phase2}. Phases open at the
// beats that say phase 1 and phase 2, or a third and two thirds of the way in.
function compile(beats) {
    var out = [], at = 0, p1 = -1, p2 = -1
    var src = list(beats)
    for (var i = 0; i < src.length; i++) {
        var b = src[i] || {}
        var ms = beatMs(b)
        if (b.phase === 1 && p1 < 0) p1 = at
        if (b.phase === 2 && p2 < 0) p2 = at
        out.push({ b: b, start: at, ms: ms })
        at += ms
    }
    if (p1 < 0 || p2 < 0) { p1 = Math.floor(at / 3); p2 = Math.floor(at * 2 / 3) }
    return { beats: out, duration: at, phase1: p1, phase2: p2 }
}

function phaseAt(c, t) { return t < c.phase1 ? 0 : t < c.phase2 ? 1 : 2 }
// The frame a reduced-motion still shows for phase k: the moment just before the next phase begins, or,
// when that phase ends on the Enter key, before it is pressed, so the still has the words typed in.
function phaseEnd(c, k) {
    if (k >= 2) return c.duration
    var edge = k === 0 ? c.phase1 : c.phase2
    for (var i = c.beats.length - 1; i >= 0; i--) {
        var e = c.beats[i]
        if (e.start + e.ms > edge || e.start >= edge) continue
        if (e.b.key === "enter" && e.start + e.ms === edge) return e.start
        break
    }
    return Math.max(0, edge - 1)
}

// -- easing --

function clamp01(x) { return x < 0 ? 0 : x > 1 ? 1 : x }

// cubic-bezier(x1, y1, x2, y2) at progress x, the way CSS and Qt's BezierSpline both mean it
function bezier(x1, y1, x2, y2, x) {
    x = clamp01(x)
    if (x === 0 || x === 1) return x
    function at(a, b, s) { var m = 1 - s; return 3 * m * m * s * a + 3 * m * s * s * b + s * s * s }
    var lo = 0, hi = 1, s = x
    for (var i = 0; i < 24; i++) {
        s = (lo + hi) / 2
        if (at(x1, x2, s) < x) lo = s; else hi = s
    }
    return at(y1, y2, s)
}
function system(x) { return bezier(0.16, 1, 0.3, 1, x) }          // slides and the lean
function roll(x) { return bezier(0.55, 0, 0.3, 1, x) }            // the stone's turn
function outQuad(x) { return x * (2 - x) }
function inQuad(x) { return x * x }
function inOutQuad(x) { return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2 }
function inOutSine(x) { return -(Math.cos(Math.PI * x) - 1) / 2 }
function lerp(a, b, k) { return a + (b - a) * k }

// -- the stone's pose, as Kit.Stone's own animations write it (Stone.qml), by time in its face --

var HOP = [   // ms, sx, sy, y, easing
    [70, 1.06, 0.9, 0, outQuad], [196, 0.97, 1.04, -3, outQuad], [168, 1.1, 0.84, 0, inQuad],
    [112, 0.98, 1.03, 0, inOutQuad], [154, 1, 1, 0, inOutQuad]
]
var KNOCK = [[192, 8, outQuad], [160, 0, inQuad], [160, 8, outQuad], [160, 0, inQuad], [928, 0, null]]

function hop(dt) {
    var p = { hopSx: 1, hopSy: 1, hopY: 0 }, from = [1, 1, 0], at = 0
    for (var i = 0; i < HOP.length; i++) {
        var seg = HOP[i]
        if (dt < at + seg[0]) {
            var k = seg[4]((dt - at) / seg[0])
            p.hopSx = lerp(from[0], seg[1], k); p.hopSy = lerp(from[1], seg[2], k); p.hopY = lerp(from[2], seg[3], k)
            return p
        }
        from = [seg[1], seg[2], seg[3]]; at += seg[0]
    }
    return p
}

function knock(dt) {
    dt = dt % 1600
    var from = 0, at = 0
    for (var i = 0; i < KNOCK.length; i++) {
        var seg = KNOCK[i]
        if (dt < at + seg[0]) return seg[2] ? lerp(from, seg[1], seg[2]((dt - at) / seg[0])) : seg[1]
        from = seg[1]; at += seg[0]
    }
    return 0
}

function glow(dt) {      // 0.25 to 0.6 in 560 ms and back in 1040, on the knock's 1.6 s beat
    dt = dt % 1600
    return dt < 560 ? lerp(0.25, 0.6, inOutSine(dt / 560)) : lerp(0.6, 0.25, inOutSine((dt - 560) / 1040))
}

// hold 300 ms, turn a third of a turn in 550, hold 150: a third of a turn looks as it did, so it repeats
function turn(dt) { dt = dt % 1000; return dt < 300 ? 0 : dt < 850 ? 120 * roll((dt - 300) / 550) : 120 }

// ended: the scene's last frame, which holds. The stone stands as it does at rest in whatever face it has.
function pose(face, dt, ended, leanDt) {
    var p = { turn: 0, knock: 0, lean: 0, glow: 0.45, hopY: 0, hopSx: 1, hopSy: 1 }
    if (face === "listening") p.lean = ended ? 6 : 6 * system(leanDt / LEAN_MS)
    if (ended) return p
    if (face === "working") p.turn = turn(dt)
    else if (face === "needs") { p.knock = knock(dt); p.glow = glow(dt) }
    else if (face === "done") { var h = hop(dt); p.hopSx = h.hopSx; p.hopSy = h.hopSy; p.hopY = h.hopY }
    return p
}

// -- the frame --

function rows(v) {
    return list(v).map(function (r) {
        var cells = typeof r === "string" ? [r] : list(r)
        return { label: String(cells[0]), value: cells.length > 1 ? cells[1] : undefined, cells: cells.map(String) }
    })
}

// What is on the mini screen at t. Beats before t have happened in full; the one t is inside is in
// progress, and each thing a beat set remembers when it was set (`since`), so its motion is a function
// of t - since alone.
// `still` poses the stone at rest, as it stands in the last frame, wherever t is.
function frameAt(c, t, still) {
    var ended = !!still || t >= c.duration
    t = Math.max(0, Math.min(t, c.duration))
    var s = {
        text: "", ghost: "", face: "rest", faceSince: 0, listenSince: -1, key: null, line: null, counterAt: null,
        win: null, cards: {}, cardOrder: [], pic: null, folded: false, foldSince: -1, strips: [], stripsTag: ""
    }
    var i, e, local, b
    for (i = 0; i < c.beats.length; i++) {
        e = c.beats[i]
        if (e.start > t) break
        b = e.b
        local = t - e.start
        var pressed = local >= KEY_PRESS_AT

        if (b.key !== undefined) {
            if (local < KEY_MS) s.key = { label: KEY_LABELS[b.key] || String(b.key), p: local / KEY_MS, name: b.key }
            if (pressed && b.key === "super" && s.listenSince < 0) s.listenSince = e.start + KEY_PRESS_AT
            if (pressed && b.key === "enter") { s.text = ""; s.ghost = ""; s.listenSince = -1 }
            if (pressed && b.key === "tab" && s.ghost) { s.text += s.ghost; s.ghost = "" }
        }
        if (typeof b.type === "string") {
            var n = Math.min(b.type.length, Math.floor(local / TYPE_MS_PER_CHAR))
            s.text += b.type.slice(0, n)
            if (n >= b.type.length && b.ghost) s.ghost = String(b.ghost)
            if (n > 0 && s.listenSince < 0) s.listenSince = e.start
        }
        if (b.stone !== undefined) {
            var wasListening = s.face === "listening"
            s.face = b.stone; s.faceSince = e.start
            if (b.stone !== "working") s.counterAt = null
            if (b.stone === "listening" && s.listenSince < 0) s.listenSince = e.start      // it leans as it does after Super
            if (b.stone === "rest" && wasListening) s.listenSince = -1                      // and rest stands it up again
            if (b.stone !== "rest" && b.stone !== "listening") s.listenSince = -1
        }
        if (b.line !== undefined) {
            if (b.line === "") s.line = null
            else s.line = {
                text: String(b.line), tag: b.tag || "", tone: b.tone || "", command: b.command || "",
                buttons: list(b.buttons).map(String), since: e.start
            }
        }
        if (b.counter !== undefined) {
            if (b.counter === false) s.counterAt = null
            else s.counterAt = e.start - (typeof b.counter === "number" ? b.counter * 1000 : 0)
        }
        if (b.window !== undefined) {
            if (showsWindow(b)) {
                var fresh = !(b.reload && s.win && !s.win.hideSince)
                var w = fresh ? { title: b.window, rows: [], cols: [], lock: "", doc: false, from: b.from || "bottom",
                                  since: e.start, rowsSince: e.start + SLIDE_MS, hideSince: 0 }
                              : s.win
                if (!fresh && b.window) w.title = b.window
                var rowStart = e.start + (fresh ? SLIDE_MS : 0)
                if (b.rows !== undefined) { w.rows = rows(b.rows); w.rowsSince = rowStart }
                if (b.lock !== undefined) w.lock = String(b.lock)
                if (b.doc !== undefined) w.doc = !!b.doc
                if (b.columns !== undefined) {
                    var colAt = rowStart + ROW_MS * list(b.rows).length
                    list(b.columns).forEach(function (col, j) {
                        var cells = list(col).map(String)
                        w.cols.push({ head: cells[0], cells: cells.slice(1), since: colAt + j * ROW_MS })
                    })
                }
                if (b.from !== undefined) w.from = b.from
                s.win = w
            } else if (s.win) s.win.hideSince = e.start
        }
        if (b.card !== undefined) {
            var old = s.cards[b.card]
            var card = {
                kind: b.card, rows: rows(b.rows),
                title: b.title !== undefined ? String(b.title) : old ? old.title : "",
                why: b.why !== undefined ? String(b.why) : old ? old.why : "",
                tag: b.tag || (old ? old.tag : ""),
                since: old ? old.since : e.start
            }
            if (b.rows === undefined && old) card.rows = old.rows
            s.cards[b.card] = card
            if (!old) s.cardOrder.push(b.card)
        }
        if (b.picture !== undefined) {
            s.pic = {
                title: String(b.picture), boxes: list(b.boxes).map(String), tag: b.tag || "", since: e.start,
                bars: list(b.bars).map(function (x) { var a = list(x); return { label: String(a[0]), pct: a[1], tone: a[2] || "" } }),
                lit: typeof b.lit === "number" ? b.lit : -1
            }
        }
        if (b.fold !== undefined) {
            s.folded = !!b.fold; s.foldSince = e.start
            if (b.fold) s.strips = list(b.strips).map(String)     // unfolding keeps them, fading out
        }
    }

    var f = { t: t, ended: ended, phase: phaseAt(c, t) }

    if (s.key) {
        var p = s.key.p
        f.key = { label: s.key.label, name: s.key.name, alpha: p < 0.28 ? p / 0.28 : p > 0.72 ? (1 - p) / 0.28 : 1,
                  down: p >= 0.28 && p <= 0.72 }
    } else f.key = null

    var busy = s.face === "working"
    var listening = s.face === "rest" && (s.text !== "" || s.listenSince >= 0)
    var face = listening ? "listening" : s.face
    var leanFrom = s.listenSince >= 0 ? s.listenSince : t
    f.pill = {
        text: s.text, ghost: s.ghost, caret: s.text !== "" || s.listenSince >= 0,
        border: busy ? "working" : face === "listening" ? "listening" : "idle", placeholder: s.text === "" && !busy
    }
    var since = listening ? leanFrom : s.faceSince
    var pz = pose(face, t - since, ended, t - leanFrom)
    f.stone = { face: face, turn: pz.turn, knock: pz.knock, lean: pz.lean, glow: pz.glow, hopY: pz.hopY,
                hopSx: pz.hopSx, hopSy: pz.hopSy }

    if (s.line) {
        var la = clamp01((t - s.line.since) / FADE_MS)
        var secs = s.counterAt !== null && busy ? Math.floor((t - s.counterAt) / 1000) : -1
        f.line = { text: s.line.text, tag: s.line.tag, tone: s.line.tone, command: s.line.command,
                   buttons: s.line.buttons, alpha: la, mono: s.line.tag === "output", secs: secs }
    } else f.line = null

    if (s.win) {
        var w2 = s.win
        var slide = system((t - w2.since) / SLIDE_MS)
        var gone = w2.hideSince > 0 && t - w2.hideSince >= WINDOW_HIDE_MS
        if (w2.hideSince > 0) slide = 1 - system((t - w2.hideSince) / WINDOW_HIDE_MS)
        if (gone) f.win = null
        else f.win = {
            title: w2.title, lock: w2.lock, doc: w2.doc, from: w2.from, slide: slide,
            alpha: clamp01(slide * 5),
            rows: w2.rows.map(function (r, k) {
                return { label: r.label, cells: r.cells, value: r.value,
                         alpha: clamp01((t - w2.rowsSince - k * ROW_MS) / FADE_MS) }
            }),
            cols: w2.cols.map(function (col) { return { head: col.head, cells: col.cells, alpha: clamp01((t - col.since) / FADE_MS) } })
        }
    } else f.win = null

    var fp = s.foldSince >= 0 ? clamp01((t - s.foldSince) / FOLD_FADE_MS) : 1
    var cards = []
    for (i = 0; i < s.cardOrder.length; i++) {
        var cd = s.cards[s.cardOrder[i]]
        var arrive = clamp01((t - cd.since) / CARD_ARRIVE_MS)
        var shown = s.foldSince >= 0 ? (s.folded ? 1 - fp : fp) : 1
        cards.push({ kind: cd.kind, rows: cd.rows, title: cd.title, why: cd.why, arrive: arrive, shown: shown * arrive })
    }
    f.cards = cards
    f.cardsLeft = cards.filter(function (x) { return x.kind !== "machine" }).reverse()
    f.cardsRight = cards.filter(function (x) { return x.kind === "machine" })
    f.fold = { folded: s.folded, p: s.foldSince >= 0 ? (s.folded ? fp : 1 - fp) : 0, strips: s.strips }

    if (s.pic) {
        var pc = s.pic
        var lt = t - pc.since
        f.pic = {
            title: pc.title, boxes: pc.boxes.map(function (x, k) { return { label: x, alpha: clamp01((lt - SLIDE_MS - k * BOX_MS) / FADE_MS) } }),
            bars: pc.bars.map(function (x, k) { return { label: x.label, pct: x.pct, tone: x.tone,
                                                            grow: clamp01((lt - SLIDE_MS - k * BOX_MS) / 500) } }),
            lit: pc.lit, slide: system(lt / SLIDE_MS), alpha: clamp01(lt / FADE_MS)
        }
    } else f.pic = null
    return f
}
