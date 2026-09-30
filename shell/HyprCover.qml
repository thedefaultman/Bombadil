import Quickshell
import Quickshell.Hyprland
import QtQuick

// Where the windows are, for the desk. Hyprland says when one opens, closes or changes workspace;
// nothing says when one is dragged, so while any window is on the stage the list is asked for again
// a few times a second. The desk's cards fold and unfold from it (DeskState.setWindows) and never
// reserve room. Does nothing without Hyprland (the headless sway test): the desk stays open there,
// and `qs ipc call desk cover` stands in for the windows.
Scope {
    id: cover
    required property var desk       // a DeskState
    property string screenName: ""   // the screen the desk lives on
    property int pollMs: 300
    readonly property bool live: !!Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE")

    // Ask Hyprland where everything is. The answers arrive a moment later and settle() reads them:
    // soon, then again once a slow answer has surely come. A burst of events settles every 40 ms
    // rather than waiting for the burst to end.
    function refresh() {
        _waits = 0
        _ask()
    }

    function _ask() {
        if (!live) return
        Hyprland.refreshMonitors()
        Hyprland.refreshToplevels()
        if (!settleTimer.running) settleTimer.start()
        lateTimer.restart()
    }

    function _monitor() {
        const all = Hyprland.monitors.values
        for (const m of all) if (m.name === screenName) return m
        return all.length > 0 ? all[0] : null
    }

    property int _waits: 0   // how many times in a row the monitor's answer had not come yet

    // The windows a person can see on the desk's screen: on its workspace, or on the special one
    // (browser, terminal, details) that is shown over it.
    function settle() {
        const m = _monitor()
        const mi = m ? (m.lastIpcObject || {}) : {}
        // The monitor's own answer (its workspace) is what places every window. When the shell starts
        // with windows open it is not there yet: look again shortly instead of calling the stage empty.
        if (!mi.activeWorkspace) {
            if (_waits++ < 20) retryTimer.restart()
            return
        }
        _waits = 0
        const ws = mi.activeWorkspace ? mi.activeWorkspace.id : null
        const special = mi.specialWorkspace && mi.specialWorkspace.name ? mi.specialWorkspace.name : ""
        const out = []
        for (const t of Hyprland.toplevels.values) {
            const o = t.lastIpcObject || {}
            if (!o.at || !o.size || o.mapped === false || o.hidden === true) continue
            if (o.monitor !== undefined && o.monitor !== m.id) continue
            const name = o.workspace ? o.workspace.name : ""
            const isSpecial = name.indexOf("special:") === 0
            if (isSpecial ? name !== special : (!o.workspace || o.workspace.id !== ws)) continue
            out.push({ x: o.at[0] - (mi.x || 0), y: o.at[1] - (mi.y || 0), w: o.size[0], h: o.size[1],
                       kind: isSpecial ? "panel" : "window",
                       // 2 is fullscreen, 3 fullscreen and maximized
                       fullscreen: o.fullscreen === 2 || o.fullscreen === 3 })
        }
        desk.setWindows(out)
    }

    Timer {
        id: settleTimer
        interval: 40
        onTriggered: cover.settle()
    }
    Timer {
        id: lateTimer
        interval: 250
        onTriggered: cover.settle()
    }
    Timer {
        id: retryTimer
        interval: 100
        onTriggered: cover._ask()
    }

    // While anything is on the stage, a drag is only seen by asking.
    Timer {
        interval: cover.pollMs
        repeat: true
        running: cover.live && cover.desk.windows.length > 0
        onTriggered: cover.refresh()
    }

    Connections {
        target: Hyprland
        enabled: cover.live
        function onRawEvent(event) {
            switch (event.name) {
            case "openwindow": case "closewindow": case "movewindow": case "movewindowv2":
            case "workspace": case "workspacev2": case "activespecial": case "activespecialv2":
            case "fullscreen": case "monitoradded": case "monitorremoved": case "focusedmon":
            case "changefloatingmode":
                cover.refresh()
                break
            }
        }
    }

    Component.onCompleted: refresh()
    onScreenNameChanged: refresh()
}
