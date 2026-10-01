//@ pragma UseQApplication
// The Bombadil bar: a Quickshell shell that is the whole visible UI at login.
// A pill at the bottom takes what you type; the line above it says what the agent is doing
// while it works and how the turn ended. The pill is also the launcher: an app or panel name
// ("passwords", "browser") opens at once, and undo and stop never wait for the model.
// Everything else on screen is a panel or an app the agent opened.
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import Quickshell.Wayland
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil as Kit

ShellRoot {
    id: root
    property bool connected: false
    // The screen whose pill has the keyboard after a tap on Super ("" = none).
    property string summonedOn: ""
    property string draft: ""          // words an app put in the pill, taken by the summoned pill
    readonly property bool hyprland: !!Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE")
    // The stone pulses instead of rolling and knocking (BOMBADIL_REDUCE_MOTION=1).
    readonly property bool reducedMotion: Quickshell.env("BOMBADIL_REDUCE_MOTION") === "1"

    // Not "pill": inside StatusLine { pill: ... } that name is the line's own property.
    PillState {
        id: pillState
        onOutgoing: msg => root.write(msg)
        onSummoned: text => root.summon(text)
        onHandOff: root.release()
    }

    // "Starting" shows for the first seconds, until agentd answers; after that, no answer is "offline".
    Timer { interval: 15000; running: true; onTriggered: pillState.booting = false }

    // The ground under everything: the wallpaper, on every screen.
    Wallpaper { reducedMotion: root.reducedMotion }

    // The desk: cards on two rails under every window, strips beside the pill when they fold.
    DeskState {
        id: deskState
        pill: pillState
        onOutgoing: msg => root.write(msg)
    }
    // The screen the desk lives on: the one desk.toml names, or the first when it names none or one
    // that is not plugged in (a desk on no screen would hide Needs you too).
    readonly property string deskScreen: {
        if (deskState.screen !== "")
            for (const s of Quickshell.screens) if (s.name === deskState.screen) return s.name
        return Quickshell.screens.length > 0 ? Quickshell.screens[0].name : ""
    }
    // The desk screen's size, in the pixels windows and cards are laid out in.
    readonly property var deskScreenObject: {
        for (const s of Quickshell.screens) if (s.name === root.deskScreen) return s
        return Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
    }
    Binding { target: deskState; property: "screenWidth"; value: root.deskScreenObject ? root.deskScreenObject.width : 1920 }
    Binding { target: deskState; property: "screenHeight"; value: root.deskScreenObject ? root.deskScreenObject.height : 1080 }
    HyprCover { desk: deskState; screenName: root.deskScreen }
    Variants {
        model: Quickshell.screens
        DeskRails {
            required property var modelData
            desk: deskState
            screen: modelData
            active: modelData.name === root.deskScreen
        }
    }
    IpcHandler {
        target: "desk"
        // What the desk is showing, as JSON.
        function state(): string { return JSON.stringify(deskState.snapshot()) }
        // Stand-in windows for a session without Hyprland: {"windows": [{x, y, w, h, fullscreen}]}.
        // (The command line takes the brackets off a bare list, so the list goes in an object.)
        function cover(windows: string): void {
            const v = JSON.parse(windows)
            deskState.setWindows(Array.isArray(v) ? v : (Array.isArray(v.windows) ? v.windows : [v]))
        }
        // A message as agentd would send it, for demos and the VM smoke check.
        function inject(message: string): void { root.handle(message) }
    }

    // agentd may start after the shell or restart under it. A Quickshell Socket that failed
    // to connect does not retry, so each attempt is a fresh Socket.
    property var agentd: null
    Component {
        id: link
        Socket {
            path: (Quickshell.env("BOMBADIL_SOCKET") || (Quickshell.env("XDG_RUNTIME_DIR") + "/bombadil/agentd.sock"))
            connected: true
            parser: SplitParser {
                onRead: message => root.handle(message)
            }
            onConnectionStateChanged: {
                root.connected = connected
                deskState.connected = connected
                if (connected) pillState.connected = true
                else { pillState.lost(); deskState.lost() }
            }
        }
    }
    Timer {
        interval: 1500; repeat: true; running: !root.connected; triggeredOnStart: true
        onTriggered: {
            if (root.agentd) root.agentd.destroy()
            root.agentd = link.createObject(root)
        }
    }

    function handle(message) {
        let ev
        try { ev = JSON.parse(message) } catch (e) { return }
        pillState.handle(ev)
        deskState.handle(ev)
    }

    function write(msg) {
        if (!root.connected || !root.agentd) return
        root.agentd.write(JSON.stringify(msg) + "\n")
        root.agentd.flush()
    }

    // Super tapped (Hyprland runs `bombadil pill`, agentd relays it here): the pill on the
    // focused screen takes the keyboard. A second tap gives it back.
    function summon(text) {
        const m = Hyprland.focusedMonitor
        const name = m ? m.name : (Quickshell.screens.length > 0 ? Quickshell.screens[0].name : "")
        if (text) {
            // Words to finish: always take the keyboard, never toggle it away.
            root.draft = text
            root.summonedOn = name
            return
        }
        root.summonedOn = root.summonedOn === name ? "" : name
    }

    function release() { root.summonedOn = "" }

    // Running apps, for the chips above the prompt. Each app window lives in its own special
    // workspace "special:app-<name>" (bombadil-app's placement.py puts it there).
    property var specials: ({})   // monitor name -> special workspace shown on it ("" = none)
    readonly property string activeSpecial: {
        const m = Hyprland.focusedMonitor
        if (!m) return ""
        if (m.name in root.specials) return root.specials[m.name]
        const ipc = m.lastIpcObject
        return ipc && ipc.specialWorkspace ? ipc.specialWorkspace.name : ""
    }
    readonly property var apps: {
        const seen = {}
        const out = []
        for (const t of Hyprland.toplevels.values) {
            const ws = t.workspace ? t.workspace.name : ""
            if (!ws.startsWith("special:app-") || seen[ws]) continue
            seen[ws] = true
            out.push({ name: ws.slice(12), title: t.title || ws.slice(12) })
        }
        return out.sort((a, b) => a.name.localeCompare(b.name))
    }
    Connections {
        target: Hyprland
        function onRawEvent(event) {
            if (event.name === "activespecial") {
                const args = event.parse(2)   // "special:app-x,DP-1", or ",DP-1" when hidden
                const s = Object.assign({}, root.specials)
                s[args[1]] = args[0]
                root.specials = s
            } else if (event.name === "openwindow" || event.name === "closewindow" || event.name === "movewindowv2") {
                Hyprland.refreshToplevels()
            }
        }
    }

    Variants {
        model: Quickshell.screens
        PanelWindow {
            id: win
            required property var modelData
            screen: modelData
            readonly property bool summoned: root.summonedOn !== "" && root.summonedOn === modelData.name
            // The desk narrows the pill and turns it into a capsule under a full-screen window on the
            // screen it lives on; the pill on another screen is as it always was.
            readonly property bool onDesk: modelData.name === root.deskScreen
            readonly property bool capsule: onDesk && deskState.capsule
            readonly property real pillMax: onDesk ? deskState.pillWidth : Kit.Theme.pillMaxWidth
            anchors { left: true; right: true; bottom: true }
            implicitHeight: column.implicitHeight + 24
            color: "transparent"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.namespace: "bombadil-bar"
            // On Hyprland the pill takes keys only while summoned (a tap on Super, or a click on the
            // pill). The focus grab below gives it the keyboard at once, and a click anywhere else
            // takes it away. Going from OnDemand back to None makes Hyprland hand the keyboard to the
            // window you were in when we let go (Enter, Esc, idle). Never Exclusive there: that
            // commit reaches Hyprland after the grab (Quickshell applies it at the next polish) and
            // Hyprland ends any grab when a layer turns exclusive, so the keys went to the window
            // under the pointer. Other compositors have no focus grab, so Exclusive it is (this is
            // also what the headless sway test runs).
            WlrLayershell.keyboardFocus: root.hyprland
                ? (summoned ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None)
                : (summoned ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand)
            // The prompt bar, plus the app chips while apps run, so windows never cover them.
            exclusiveZone: 64 + (appChips.visible ? appChips.implicitHeight + column.spacing : 0)
            // Clicks go through the transparent parts of the bar to the windows behind it.
            mask: Region {
                Region { item: cardHost }
                Region { item: statusLine }
                Region { item: setupChips.visible ? setupChips : null }   // (a hidden item keeps its last place)
                Region { item: chips }
                Region { item: appChips }
                Region { item: pillBox }
                Region { item: stripsLeft }
                Region { item: stripsRight }
            }

            onSummonedChanged: {
                if (summoned) input.forceActiveFocus()
                grab.active = summoned
                takeDraft()
            }
            function takeDraft() {
                if (!summoned || root.draft === "") return
                input.text = root.draft
                input.cursorPosition = input.text.length
                root.draft = ""
            }
            Connections {
                target: root
                function onDraftChanged() { win.takeDraft() }
            }

            // Clicking the pill is the same as tapping Super: it is where you type. (Elsewhere the
            // layer takes clicks on demand by itself.)
            function summonHere() { if (root.hyprland) root.summonedOn = modelData.name }

            // While summoned the pill holds the keyboard; a click anywhere else hands it back,
            // so typing meant for another window (a password prompt) never lands in the pill.
            HyprlandFocusGrab {
                id: grab
                windows: [win]
                onCleared: root.release()
            }

            Timer {
                // Summoned and left alone: give the keyboard back to the windows. Typing starts
                // the wait again; text already typed stays in the pill.
                id: idle
                interval: input.text === "" ? 20000 : 60000
                running: win.summoned
                onTriggered: root.release()
            }

            // The cards that folded, beside the pill.
            DeskStrips {
                id: stripsLeft
                desk: deskState; side: "left"; active: win.onDesk
                pillEdge: column.x + pillBox.x
                pillCentreY: column.y + pillBox.y + pillBox.height / 2
            }
            DeskStrips {
                id: stripsRight
                desk: deskState; side: "right"; active: win.onDesk
                pillEdge: column.x + pillBox.x + pillBox.width
                pillCentreY: column.y + pillBox.y + pillBox.height / 2
            }

            ColumnLayout {
                id: column
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
                spacing: 8

                // A picture the machine drew from itself, or the agent drew: above the line, over the
                // windows. It draws with the kit's Diagram, so it loads on its own: a picture
                // that will not draw costs the pictures, never the bar.
                Loader {
                    id: cardHost
                    visible: status === Loader.Ready && item !== null && item.opacity > 0
                    Layout.fillWidth: true
                    // As wide as the pill, so it stays between the desk's rails.
                    Layout.maximumWidth: Math.max(360, win.pillMax)
                    Layout.alignment: Qt.AlignHCenter
                    Component.onCompleted: setSource("CardHost.qml", {
                        pill: pillState, maxHeight: Math.round(modelData.height * 0.6) })
                    // A full-screen window on this screen puts the picture away (it is still there after).
                    Binding { target: cardHost.item; property: "suppressed"; value: win.capsule; when: cardHost.item !== null }
                }

                StatusLine {
                    id: statusLine
                    pill: pillState
                    Layout.fillWidth: true
                    Layout.maximumWidth: Math.max(360, win.pillMax)
                    Layout.alignment: Qt.AlignHCenter
                }

                // Which AI, Sign in, Show sign-in: the choices under the setup line.
                SetupChips {
                    id: setupChips
                    pill: pillState
                    Layout.fillWidth: false
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: Kit.Theme.pillMaxWidth
                }

                QueueChips {
                    id: chips
                    pill: pillState
                    // A layout fills the width by default; this one stays as wide as its chips,
                    // so the input mask lets clicks beside them through.
                    Layout.fillWidth: false
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: Math.max(360, win.pillMax)
                }

                // Running apps: a chip per app. Click slides it in or out, × quits it.
                RowLayout {
                    id: appChips
                    Layout.alignment: Qt.AlignHCenter
                    Layout.fillWidth: false
                    Layout.maximumWidth: Kit.Theme.pillMaxWidth
                    visible: root.apps.length > 0
                    spacing: 6
                    Repeater {
                        model: root.apps
                        Rectangle {
                            id: chip
                            required property var modelData
                            readonly property bool shown: root.activeSpecial === "special:app-" + modelData.name
                            implicitWidth: chipRow.implicitWidth + 28
                            implicitHeight: 28
                            radius: 14
                            color: shown ? Kit.Theme.glassRaised : Kit.Theme.glassChip
                            border.width: 1
                            border.color: shown ? Kit.Theme.accent : Kit.Theme.border
                            Behavior on border.color { ColorAnimation { duration: 150 } }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: Hyprland.dispatch('hl.dsp.workspace.toggle_special("app-' + chip.modelData.name + '")')
                            }
                            RowLayout {
                                id: chipRow
                                anchors.centerIn: parent
                                spacing: 8
                                Text {
                                    font.family: Kit.Theme.fontFamily
                                    Layout.maximumWidth: 180
                                    text: chip.modelData.title
                                    color: chip.shown ? Kit.Theme.fg : Kit.Theme.muted
                                    font.pixelSize: Kit.Theme.smallSize
                                    textFormat: Text.PlainText
                                    elide: Text.ElideRight
                                }
                                Text {
                                    font.family: Kit.Theme.fontFamily
                                    text: "×"
                                    color: closeArea.containsMouse ? Kit.Theme.fg : Kit.Theme.muted
                                    font.pixelSize: 15
                                    MouseArea {
                                        id: closeArea
                                        anchors.fill: parent
                                        anchors.margins: -6
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: Quickshell.execDetached(["bombadil-app", "close", chip.modelData.name])
                                    }
                                }
                            }
                        }
                    }
                }

                // Prompt bar
                Rectangle {
                    id: pillBox
                    Layout.fillWidth: true
                    Layout.maximumWidth: win.pillMax
                    Layout.alignment: Qt.AlignHCenter
                    implicitHeight: Kit.Theme.pillHeight
                    radius: Kit.Theme.radiusPill
                    color: Kit.Theme.glassPill
                    border.color: pillState.busy ? Kit.Theme.accent : (win.summoned ? Kit.Theme.borderActive : (pillState.face === "offline" ? Kit.Theme.badLine : Kit.Theme.border))
                    border.width: Kit.Theme.pillBorder
                    Behavior on border.color { ColorAnimation { duration: Kit.Theme.slow } }
                    TapHandler { onTapped: win.summonHere() }

                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                        spacing: 8

                        // The stone: Bombadil's mark, in the dot's place. Its face says what the machine is
                        // doing (Stone.qml); while a turn runs, hover turns it into Stop.
                        Rectangle {
                            id: dotBox
                            readonly property bool stoppable: pillState.stoppable && dotHover.hovered && !win.capsule
                            implicitWidth: stoppable ? stopRow.implicitWidth + 16 : 24
                            implicitHeight: 24
                            radius: 12
                            color: stoppable ? Kit.Theme.accentSoft : "transparent"
                            Behavior on implicitWidth { NumberAnimation { duration: Kit.Theme.fast } }
                            Stone {
                                id: stone
                                objectName: "stone"
                                visible: !dotBox.stoppable
                                anchors.centerIn: parent
                                // The screen that holds the keyboard leans toward what you type.
                                face: win.summoned && pillState.face === "rest" ? "listening" : pillState.face
                                reducedMotion: root.reducedMotion
                            }
                            Row {
                                id: stopRow
                                visible: dotBox.stoppable
                                anchors.centerIn: parent
                                spacing: 6
                                Rectangle { width: 9; height: 9; radius: 2; color: Kit.Theme.accent; anchors.verticalCenter: parent.verticalCenter }
                                Text { font.family: Kit.Theme.fontFamily; text: "Stop"; color: Kit.Theme.accentInk; font.pixelSize: Kit.Theme.smallSize }
                            }
                            HoverHandler { id: dotHover; cursorShape: pillState.busy ? Qt.PointingHandCursor : Qt.ArrowCursor }
                            TapHandler { enabled: pillState.stoppable; onTapped: pillState.stop() }
                        }

                        Item {
                            visible: !win.capsule     // a full-screen window: the pill is the dot and the clock
                            Layout.fillWidth: true
                            implicitHeight: input.implicitHeight

                            TextField {
                                id: input
                                font.family: Kit.Theme.fontFamily
                                anchors.fill: parent
                                enabled: !win.capsule   // hidden in the capsule: nothing can be typed blind
                                placeholderText: pillState.face === "starting" ? "Starting" : (root.connected ? "Ask anything" : "Waiting for agentd…")
                                color: Kit.Theme.fg
                                placeholderTextColor: Kit.Theme.muted
                                font.pixelSize: Kit.Theme.promptSize
                                background: null
                                focus: true
                                // The field takes the press itself, so the pill's own handler never sees it.
                                TapHandler { onTapped: win.summonHere() }
                                onAccepted: {
                                    if (pillState.submit(text)) {
                                        text = ""
                                        root.release()
                                    }
                                }
                                onTextChanged: if (win.summoned) idle.restart()
                                // Tab takes the suggested name: "pass" + Tab = "passwords".
                                Keys.onTabPressed: {
                                    const rest = pillState.completion(text)
                                    if (rest) text = text + rest
                                }
                                // Esc stops a running turn; otherwise it clears, then puts the line, the
                                // picture and the drawer away and gives the keyboard back.
                                Keys.onEscapePressed: {
                                    if (pillState.stoppable) pillState.stop()
                                    else if (text !== "") text = ""
                                    else { pillState.dismiss(); pillState.closeDetails(); root.release() }
                                }
                            }

                            // The rest of a name Tab would complete, drawn after what was typed.
                            Row {
                                x: input.leftPadding
                                anchors.verticalCenter: parent.verticalCenter
                                visible: input.text !== "" && ghost.text !== ""
                                Text { text: input.text; font: input.font; color: "transparent"; textFormat: Text.PlainText }
                                Text { id: ghost; text: pillState.completion(input.text); font: input.font; color: Kit.Theme.faint; textFormat: Text.PlainText }
                            }
                        }

                        // An exact launcher word: say it opens here, without the model.
                        Text {
                            font.family: Kit.Theme.fontFamily
                            readonly property string target: pillState.exact(input.text)
                            visible: target !== "" && !win.capsule
                            text: "↵ " + target
                            color: Kit.Theme.muted
                            font.pixelSize: Kit.Theme.captionSize
                        }

                        Text {
                            id: clock
                            font.family: Kit.Theme.fontFamily
                            text: Qt.formatTime(new Date(), "HH:mm"); color: Kit.Theme.muted; font.pixelSize: Kit.Theme.smallSize
                            Timer { interval: 30000; running: true; repeat: true; onTriggered: clock.text = Qt.formatTime(new Date(), "HH:mm") }
                        }
                    }
                }
            }
        }
    }
}
