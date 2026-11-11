import QtQuick

// A stand-in for Quickshell's NotificationServer, for a test that has no Quickshell: the capability flags are plain
// properties (the test reads what the shell advertises) and `arrive` hands the shell a notification as the server does.
QtObject {
    id: server
    property bool actionsSupported: false
    property bool actionIconsSupported: false
    property bool bodySupported: true
    property bool bodyMarkupSupported: false
    property bool bodyHyperlinksSupported: false
    property bool bodyImagesSupported: false
    property bool imageSupported: false
    property bool inlineReplySupported: false
    property bool persistenceSupported: false
    property bool keepOnReload: true

    signal notification(var notification)

    property Component _note: FakeNotification {}
    property Component _action: FakeAction {}

    // spec: the notification's fields, with `actions` as [{identifier, text}].
    function arrive(spec) {
        const n = _note.createObject(server, Object.assign({}, spec, { actions: [] }))
        n.actions = (spec.actions || []).map(a => _action.createObject(server, { identifier: a.identifier, text: a.text, owner: n }))
        notification(n)
        return n
    }
}
