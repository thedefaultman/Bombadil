import QtQml
import Bombadil

// Persistent state: every property an app declares on a Store is saved to
// data/<name>.json (300 ms after it changes, and before a hot reload or quit) and
// applied again when the Store is created. Values go through JSON.
// The file is never trimmed to what this version declares: a saved value for a property
// that was renamed, or whose new type cannot take it, stays in the file until the app
// assigns that property again, so the next edit of the app can still get it back.
QtObject {
    id: store

    property string name: "state"
    property bool loaded: false

    function save() {
        if (!loaded || App.checking)
            return
        _timer.stop()
        const values = _parse(KitFiles.snapshot(store, _keys))
        const merged = Object.assign({}, _state.file)
        for (const k in values) {
            if (!_state.held[k])
                merged[k] = values[k]
        }
        _state.file = merged
        KitFiles.writeText(name + ".json", JSON.stringify(merged, null, 1))
    }

    function reset() {
        _timer.stop()
        _state.file = {}
        _state.held = {}
        if (!App.checking)
            KitFiles.remove(name + ".json")
        _apply(_defaults)
    }

    property var _keys: []
    property var _defaults: ({})
    // file: what data/<name>.json holds; held: saved keys this version could not take.
    property var _state: ({ started: false, applying: false, file: {}, held: {} })
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
        _defaults = _parse(KitFiles.snapshot(store, keys))
        const text = KitFiles.readText(name + ".json")
        if (text.trim()) {
            let saved = null
            try {
                saved = _parse(text)
            } catch (e) {
            }
            if (saved && typeof saved === "object" && !Array.isArray(saved)) {
                _state.file = saved
                for (const k of _apply(saved)) {
                    _state.held[k] = true
                    if (saved[k] !== null)
                        console.warn("Store: the saved " + k + " does not fit this property; it stays in "
                                     + name + ".json until the app sets " + k)
                }
            } else {
                // Moved aside, so the next save cannot overwrite what is in it.
                KitFiles.quarantine(name + ".json")
                console.warn("Store: " + name + ".json is not valid JSON; it was renamed to " + name
                             + ".json.bad and the app starts from the defaults")
            }
        }
        for (const k of keys)
            store[k + "Changed"].connect(() => _changed(k))
        loaded = true
        return true
    }

    // Assigns every value there is a key for; returns the keys that did not take their value.
    function _apply(values) {
        const tried = [], failed = []
        _state.applying = true
        for (const k of _keys) {
            // null is how an unset date or a NaN is saved: nothing to restore over the same default.
            if (!values.hasOwnProperty(k) || (values[k] === null && _defaults[k] === null))
                continue
            try {
                store[k] = values[k]
                tried.push(k)
            } catch (e) {
                failed.push(k)
            }
        }
        _state.applying = false
        // A typed property can also turn a value into something else (an array into a string).
        const now = _parse(KitFiles.snapshot(store, tried))
        return failed.concat(tried.filter(k => now.hasOwnProperty(k) && !_same(now[k], values[k])))
    }

    function _changed(k) {
        if (!loaded || _state.applying || App.checking)
            return
        delete _state.held[k]
        _timer.restart()
    }

    // JSON.parse, except that NaN and Infinity (written by Python's json) become null.
    function _parse(text) {
        try {
            return JSON.parse(text)
        } catch (e) {
            return JSON.parse(text.replace(/"(?:[^"\\]|\\.)*"|-?\b(?:NaN|Infinity)\b/g,
                                           m => m[0] === '"' ? m : "null"))
        }
    }

    function _same(a, b) {
        if (a === b)
            return true
        if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b))
            return false
        const keys = Object.keys(a)
        return keys.length === Object.keys(b).length && keys.every(k => b.hasOwnProperty(k) && _same(a[k], b[k]))
    }

    Component.onCompleted: _start()
    Component.onDestruction: {
        if (_timer.running)
            save()
    }
}
