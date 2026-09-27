import QtQml
import Bombadil

// Persistent state: every property an app declares on a Store is saved to
// data/<name>.json (300 ms after it changes, and before a hot reload or quit) and
// applied again when the Store is created. Values go through JSON.
QtObject {
    id: store

    property string name: "state"
    property bool loaded: false

    function save() {
        if (!loaded || App.checking)
            return
        _timer.stop()
        KitFiles.writeText(name + ".json", KitFiles.snapshot(store, _keys))
    }

    function reset() {
        _timer.stop()
        if (!App.checking)
            KitFiles.remove(name + ".json")
        _apply(_defaults)
    }

    property var _keys: []
    property var _defaults: ({})
    property var _state: ({ started: false, applying: false })
    // Restoring runs in a binding: those are evaluated after the app's own property values are
    // set but before any Component.onCompleted, so the app never sees the defaults first.
    // KitFiles reads the values, which keeps this binding from depending on them.
    property bool _started: _start()
    property Timer _timer: Timer {
        interval: 300
        onTriggered: store.save()
    }
    property Connections _reload: Connections {
        target: App
        function onAboutToReload() { store.save() }
    }

    function _start() {
        if (_state.started)
            return true
        _state.started = true
        const keys = KitFiles.storeKeys(store)
        _keys = keys
        _defaults = JSON.parse(KitFiles.snapshot(store, keys))
        const text = KitFiles.readText(name + ".json")
        if (text) {
            try {
                _apply(JSON.parse(text))
            } catch (e) {
                console.warn("Store: " + name + ".json is not valid JSON, starting from the defaults")
            }
        }
        for (const k of keys)
            store[k + "Changed"].connect(_changed)
        loaded = true
        return true
    }

    function _apply(values) {
        if (!values || typeof values !== "object")
            return
        _state.applying = true
        for (const k of _keys) {
            if (!values.hasOwnProperty(k))
                continue
            try {
                store[k] = values[k]
            } catch (e) {
                // null is how an unset date is saved; a typed property just keeps its value
                if (values[k] !== null)
                    console.warn("Store: cannot restore " + k + ": " + e)
            }
        }
        _state.applying = false
    }

    function _changed() {
        if (loaded && !_state.applying && !App.checking)
            _timer.restart()
    }

    Component.onCompleted: _start()
    Component.onDestruction: {
        if (_timer.running)
            save()
    }
}
