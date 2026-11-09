import QtQuick

// What the box of a card and its button do together: a reply on a message card, a new task or a comment on a task
// card. It has no look of its own; the faces draw, and this keeps the rules that make the button the person's.
//
//   typing       tells agentd the draft changed (`edit`, 150 ms after the last key) and then that the card has drawn
//                exactly that (`shown`, 200 ms after the last key and on the frame that holds it), so agentd lights
//                its `ready` only for what is on the screen. The first key in a box that has no draft yet asks agentd
//                for one (`open`); what was typed meanwhile is sent when the draft has its id.
//   updates      agentd's word on the draft (its state, `ready`, fingerprint, receipt, warnings, note) is shown as it
//                comes. Its content goes into the boxes only for a draft the person has not typed in and whose boxes
//                are empty or just opened: a person who is typing is never written over, and one who has emptied the
//                box does not get the old words back from a late answer.
//   the button   is live only while agentd says `ready` and the boxes hold exactly the draft's content, and has been
//                for a moment (a click that was on its way to something else when the card changed does not press).
//                One press sends one `press` with the fingerprint agentd last gave for this content, and the button
//                stays dim until agentd answers. After a press whose outcome is unknown it is dim until the person
//                says they looked where it would have gone, and then presses with `again`.
//
// A card whose reply is a `copy` (a notification) has no draft: nothing here goes to agentd for it, and its button
// is not a send.
Item {
    id: draft

    required property var pill
    property var card: null
    property bool fields: false          // the content is an object of field values (a task); else text (a reply)
    property var boxKeys: []             // fields: the keys that have a box (any other key of the draft is carried as it is)
    property var current: fields ? ({}) : ""   // what the boxes hold now: text, or {key: text} for each box
    property bool boxFocus: false        // the person is in a box
    property int armDelay: 300
    property bool looked: false          // "I looked in Slack", after a press that may have gone
    readonly property bool drawn: visible

    // Puts a content into the boxes. Not an edit.
    signal write(var content)

    readonly property var reply: !card ? null
        : card.type === "task" ? (card.proposal && typeof card.proposal === "object" ? card.proposal : null)
        : (card.reply && typeof card.reply === "object" ? card.reply : null)
    readonly property string kind: reply && reply.kind ? String(reply.kind) : ""
    readonly property bool copy: kind === "copy"
    readonly property string proposalId: reply && reply.proposal ? String(reply.proposal) : ""
    readonly property string phase: reply && reply.state ? String(reply.state) : "open"
    // Once a draft is being sent, was sent, or may have gone, its boxes show what is there and take no edit.
    readonly property bool editable: phase === "open"
    readonly property bool hasText: _filled(current)
    readonly property bool matches: !!reply && _key(_whole(current)) === _key(_fromReply())
    readonly property bool lit: !copy && proposalId !== "" && !!reply && reply.ready === true && phase === "open"
                                && matches && pill.pressOut !== proposalId
    readonly property bool again: !copy && proposalId !== "" && phase === "unknown" && looked && matches
                                  && pill.pressOut !== proposalId
    readonly property bool wanted: lit || again
    property bool armed: false
    readonly property bool live: armed && wanted

    property string _card: ""            // the card the boxes were loaded for
    property string _proposal: ""        // and the draft of it
    property bool _typed: false          // the person has typed in this draft
    property string _sent: ""            // what agentd was last told the boxes hold (or had, when they were loaded)
    property bool _opening: false
    property bool _owed: false           // agentd is to be told what is drawn on the next frame
    property string _owedKey: ""         // the content that frame is to hold
    readonly property var _window: Window.window

    // -- the content, as one string to compare --

    function _filled(c) {
        if (typeof c === "string") return c !== ""
        if (c && typeof c === "object") return Object.keys(c).some(k => String(c[k] === undefined || c[k] === null ? "" : c[k]) !== "")
        return false
    }
    function _key(c) {
        if (typeof c === "string") return c
        const o = c && typeof c === "object" ? c : ({})
        return JSON.stringify(Object.keys(o).sort().map(k => [k, String(o[k] === undefined || o[k] === null ? "" : o[k])]))
    }
    // The boxes' content with what the draft carries for fields that have no box (it would go, so an edit keeps it).
    function _whole(c) {
        if (!fields) return typeof c === "string" ? c : ""
        const held = reply && reply.content && typeof reply.content === "object" ? reply.content : ({})
        const out = {}
        for (const k of Object.keys(held)) if (boxKeys.indexOf(k) < 0) out[k] = held[k]
        for (const k of boxKeys) out[k] = c && c[k] !== undefined && c[k] !== null ? String(c[k]) : ""
        return out
    }
    // What the draft says it holds, in the form of the boxes (a box the draft has no word for is empty).
    function _fromReply() {
        const held = reply ? reply.content : undefined
        if (!fields) return typeof held === "string" ? held : ""
        const o = held && typeof held === "object" ? held : ({})
        const out = {}
        for (const k of Object.keys(o)) out[k] = o[k]
        for (const k of boxKeys) out[k] = o[k] !== undefined && o[k] !== null ? String(o[k]) : ""
        return out
    }
    function _boxes(content) {
        if (!fields) return content
        const out = {}
        for (const k of boxKeys) out[k] = content[k] !== undefined && content[k] !== null ? String(content[k]) : ""
        return out
    }

    // -- the card or its draft changed --

    function sync() {
        if (!card || !reply) {
            _card = ""; _proposal = ""
            _compose()
            return
        }
        if (card.id !== _card) {
            _card = card.id; _proposal = proposalId; _typed = false; _opening = false; looked = false
            write(_boxes(_fromReply()))
            _sent = _key(_fromReply())
            _compose()
            _owe()
            return
        }
        if (proposalId !== _proposal) {
            // The same card, with a draft of its own now (or another): what was typed stays, and is sent to it.
            _proposal = proposalId; _opening = false; looked = false
            if (!_typed) write(_boxes(_fromReply()))
            _sent = _key(_fromReply())
            if (_typed) sendEdit()
            _owe()
        } else if (!_typed && !hasText && _filled(_fromReply())) {
            write(_boxes(_fromReply()))      // an answer that has words, for a box nobody has touched
            _sent = _key(_fromReply())
            _owe()
        }
        _compose()
    }

    // `reply`, `proposalId`, `kind` and the rest are bindings on `card`: when the card changes, a handler that runs
    // before they have caught up would load the box with the words of the card that went (and take that card's draft
    // for this one's), so the card's change is looked at once they have all settled.
    onCardChanged: Qt.callLater(sync)
    onReplyChanged: Qt.callLater(sync)
    onBoxKeysChanged: if (card) _compose()

    Connections {
        target: draft.pill
        // agentd handed the bar a whole card again (it may have lost what it was told was drawn).
        function onCardEpochChanged() { if (draft.card && draft.pill.cardId === draft.card.id) draft._owe() }
        // The card is going (put away, replaced, a turn): what was typed and not yet said is said now.
        function onCardIdChanged() { if (!draft.card || draft.pill.cardId !== draft.card.id) draft.flush() }
    }

    // -- the person typing --

    // A box of the face was edited by the person.
    function changed() {
        if (!editable) return
        _typed = true
        pill.noteTyped()
        _compose()
        if (copy) return
        if (proposalId === "") {
            if (!_opening) { _opening = true; pill.openReply(kind, reply.target || "") }
            return
        }
        editTimer.restart()
        shownTimer.restart()
    }

    function sendEdit() {
        editTimer.stop()
        if (copy || proposalId === "" || !editable) return
        const content = _whole(current)
        const key = _key(content)
        if (key === _sent) return
        _sent = key
        pill.proposalEdit(proposalId, content)
    }

    // Said to agentd when the card has drawn what it holds, on the frame that follows (the window is asked for one).
    function _owe() {
        if (copy || proposalId === "") return
        _owed = true
        _owedKey = _key(_whole(current))
        frameTimer.restart()
        if (_window) _window.update()
        else report()
    }
    function report() {
        _owed = false
        frameTimer.stop()
        shownTimer.stop()
        if (!drawn || copy || proposalId === "" || !card) return
        if (_key(_whole(current)) !== _owedKey) { _owe(); return }   // changed since: that frame did not hold this
        sendEdit()      // never a `shown` for words agentd has not been given
        pill.proposalShown(proposalId, _whole(current))
    }
    // Typing stopped, or the card was put away: what is pending goes now.
    function flush() {
        if (editTimer.running) sendEdit()
    }
    onDrawnChanged: if (drawn) _owe()

    Timer { id: editTimer; interval: 150; onTriggered: draft.sendEdit() }
    Timer { id: shownTimer; interval: 200; onTriggered: draft._owe() }
    // A window that draws no frames (not exposed yet) must not leave a draft unreported for good.
    Timer { id: frameTimer; interval: 500; onTriggered: if (draft._owed) draft.report() }
    Connections {
        target: draft._window
        function onFrameSwapped() { if (draft._owed) draft.report() }
    }

    // -- the keyboard, for the bar --

    function _compose() {
        if (card && typeof card.id === "string") pill.compose(card.id, boxFocus, hasText)
    }
    onBoxFocusChanged: _compose()
    onHasTextChanged: _compose()
    Component.onDestruction: if (_card !== "") pill.compose(_card, false, false)

    // -- the button --

    onWantedChanged: {
        if (wanted) armTimer.restart()
        else { armTimer.stop(); armed = false }
    }
    Timer { id: armTimer; interval: draft.armDelay; onTriggered: draft.armed = draft.wanted }

    // The press: once, with the fingerprint agentd gave for the content on the screen.
    function press() {
        if (!live) return false
        return pill.press(kind, proposalId, String(reply.fingerprint || ""), phase === "unknown")
    }

    Component.onCompleted: sync()
}
