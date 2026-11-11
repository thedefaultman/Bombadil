import QtQuick
import Quickshell.Services.Notifications

// The desktop's notification name (org.freedesktop.Notifications), in place of mako. It draws nothing: every
// notification goes to agentd as it arrived, and agentd decides what the person sees (a line with Reply and Open,
// docs/CONNECT.md, "Notifications"). A notification is another program's words, often another person's; they are
// passed on untouched except for a cut to a sane length, and agentd makes them plain.
//
// The bar keeps each one tracked, so that it can still be acted on, until agentd says what became of it:
//   notification_invoke  the named action, else the notification's default one (for a browser tab: bring the tab
//                        forward), and then the notification is let go
//   notification_close   the person put it away or nobody will ask about it: dismissed, as if they had
// One an app closes itself (or that timed out in the app) is simply let go; a later word from agentd about it finds
// nothing and does nothing. At most 64 are held; the oldest is dismissed to make room (agentd holds as many).
// A notification that arrives while agentd is away waits and goes when the link is back; one agentd lost with a
// restart is not sent again (it would be a notice for something the person may have seen).
//
// What the server says it can do is what is true: it can carry actions and a body, and it cannot show markup,
// links, images, an inline reply or keep a notification across a restart of the bar. Chromium sends clicks and
// actions only to a server that advertises "actions", so that one matters.
Item {
    id: server
    visible: false
    width: 0
    height: 0

    // Whether agentd is there to be told. Set by the shell.
    property bool link: false
    // What goes to agentd (shell.qml writes it to the socket).
    signal outgoing(var msg)

    readonly property int maxHeld: 64
    readonly property int textMax: 2000
    readonly property int actionsMax: 8

    // id -> { n: the notification, sent: told to agentd }, and the ids oldest first.
    property var _held: ({})
    property var _order: []

    NotificationServer {
        id: backend
        objectName: "notificationServer"
        actionsSupported: true
        bodySupported: true
        bodyMarkupSupported: false
        bodyHyperlinksSupported: false
        bodyImagesSupported: false
        imageSupported: false
        actionIconsSupported: false
        inlineReplySupported: false
        persistenceSupported: false
        keepOnReload: false

        onNotification: n => server.take(n)
    }

    onLinkChanged: if (link) flush()

    function _text(value) {
        return typeof value === "string" ? value.slice(0, textMax) : ""
    }

    function _urgency(n) {
        if (n.urgency === NotificationUrgency.Critical) return "critical"
        if (n.urgency === NotificationUrgency.Low) return "low"
        return "normal"
    }

    function _actions(n) {
        const out = []
        const list = n.actions
        for (let i = 0; list && i < list.length && out.length < actionsMax; i++) {
            const id = _text(list[i].identifier)
            if (id !== "") out.push({ id: id, label: _text(list[i].text) })
        }
        return out
    }

    function _message(n) {
        return { type: "notification", id: n.id, app: _text(n.appName), summary: _text(n.summary), body: _text(n.body),
                 urgency: _urgency(n), icon: _text(n.appIcon), actions: _actions(n) }
    }

    // A notification arrived: kept (Quickshell destroys one that is not tracked as the signal returns), and told.
    function take(n) {
        const id = n.id
        _release(id, false)         // (an app that sends it again with new words is updating it)
        n.tracked = true
        _held[id] = { n: n, sent: false }
        _order.push(id)
        n.closed.connect(() => _release(id, false, n))
        while (_order.length > maxHeld) _release(_order[0], true)
        if (link) _send(id)
    }

    function _send(id) {
        const entry = _held[id]
        if (!entry || entry.sent) return
        entry.sent = true
        outgoing(_message(entry.n))
    }

    function flush() {
        for (const id of _order.slice()) _send(id)
    }

    // Lets one go. `dismiss`: tell the app it was put away. `n`: only if this is still the notification held under
    // the id (one that closes after it was replaced is not the one now held).
    function _release(id, dismiss, n) {
        const entry = _held[id]
        if (!entry || (n !== undefined && entry.n !== n)) return
        delete _held[id]
        const at = _order.indexOf(id)
        if (at >= 0) _order.splice(at, 1)
        try {
            if (dismiss) entry.n.dismiss()
            else if (entry.n.tracked) entry.n.tracked = false
        } catch (e) {
            // already gone: the app closed it as this ran
        }
    }

    function _action(entry, wanted) {
        const list = entry.n.actions
        let fallback = null
        for (let i = 0; list && i < list.length; i++) {
            if (wanted !== "" && list[i].identifier === wanted) return list[i]
            if (list[i].identifier === "default") fallback = list[i]
        }
        return fallback
    }

    // agentd's word about one (the shell passes on every message that comes from it).
    function handle(ev) {
        if (!ev || (ev.type !== "notification_invoke" && ev.type !== "notification_close")) return
        if (typeof ev.id !== "number") return           // (an object's keys are text: "4" would find 4)
        const entry = _held[ev.id]
        if (!entry) return
        if (ev.type === "notification_close") {
            _release(ev.id, true)
            return
        }
        const action = _action(entry, typeof ev.action === "string" ? ev.action : "")
        // The action goes first and the notification is let go after: the app may close it in answer, which is no
        // news. With no action to take there is nothing to bring forward, and it is put away.
        if (action !== null) {
            try { action.invoke() } catch (e) { /* the app closed it first */ }
        }
        _release(ev.id, action === null)
    }
}
