//@ pragma UseQApplication
// The Bombadil bar: a Quickshell shell that is the whole visible UI at login.
// A pill at the bottom takes what you type; the line above it says what the agent is doing
// while it works and how the turn ended, and, when nothing else has it, a notice another service
// asked agentd to say (new mail). The pill is also the launcher: an app or panel name
// ("passwords", "browser") opens at once, and undo and stop never wait for the model.
// Above the pill's left end, a chip per project holds a dot per coding session; when one waits
// for you the pill's empty line says so, and Tab goes there.
// Everything else on screen is a panel or an app the agent opened. The bar also owns the desktop's notification
// name (Notifications.qml), and sends what arrives to agentd, which decides what the person sees.
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
    property bool draftTip: false      // they are a tip's: they wait in an empty pill and never replace typed words
    readonly property bool hyprland: !!Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE")
    // The screens that show the card and the welcome: the ones that had the focus when they
    // arrived ("" = every screen).
    property string cardOn: ""
    // The keyboard goes to the card (not the pill) when a screen turns summoned: the card asked for it
    // or was clicked. Super and a click on the pill clear it, so they always mean the pill.
    property bool cardKeys: false
    property string welcomeOn: ""
    // Reduced motion (BOMBADIL_REDUCE_MOTION=1): the stone pulses instead of rolling and knocking, and
    // everything else that moves goes instant. The kit's Theme holds the switch for every surface.
    readonly property bool reducedMotion: Quickshell.env("BOMBADIL_REDUCE_MOTION") === "1"
    Binding { target: Kit.Theme; property: "reducedMotion"; value: root.reducedMotion }

    // The Widgets card is open only while the pill holds the keyboard: whatever lets go of it (Esc, a click
    // elsewhere, Super, Enter, the drawer taking it) closes the card with it.
    onSummonedOnChanged: if (summonedOn === "") deskState.closeWidgets()

    // Not "pill": inside StatusLine { pill: ... } that name is the line's own property.
    PillState {
        id: pillState
        onOutgoing: msg => root.write(msg)
        onSummoned: (text, tip) => root.summon(text, tip)
        onHandOff: root.release()
        onAiOpenChanged: if (aiOpen) deskState.closeWidgets()   // one card over the pill at a time
        onAsked: root.summonKeep()
        onWelcomeShown: root.welcomeOn = root.focusedScreen()
        // A full-screen window in front: a greeting is dropped rather than drawn over it.
        fullscreen: !!(Hyprland.focusedMonitor && Hyprland.focusedMonitor.activeWorkspace && Hyprland.focusedMonitor.activeWorkspace.hasFullscreen)
    }

    // Away and back: agentd times a welcome-back from this. respectInhibitors stays on, so a
    // video being watched counts as present.
    IdleMonitor {
        timeout: Number(Quickshell.env("BOMBADIL_IDLE_SECONDS")) || 300
        onIsIdleChanged: pillState.presence(isIdle)
    }

    // The first key or pointer movement after a welcome starts its fade. Keys typed into other
    // windows are invisible to the bar, so this watches for a pause of a second and the activity after it.
    IdleMonitor {
        timeout: 1
        respectInhibitors: false
        enabled: pillState.mode === "welcome" && pillState.touchedAt === 0
        onIsIdleChanged: if (!isIdle) pillState.touched()
    }

    // The notification server, in place of mako. It draws nothing: each notification goes to agentd, which decides
    // what the person sees. Loaded on its own, so a Quickshell without the service costs the notifications and
    // nothing else.
    Loader {
        id: notifications
        source: "Notifications.qml"
        Binding { target: notifications.item; property: "link"; value: root.connected; when: notifications.item !== null }
        Connections {
            target: notifications.item
            function onOutgoing(msg) { root.write(msg) }
        }
    }

    // The screen that has the card with a box in it, where the person is when it arrives and for as long as it is
    // there ("" = a picture, which every screen draws). Two boxes for one draft would be two people typing in one.
    property string cardScreen: ""
    signal cardKeysWanted(string name)
    function focusedScreenName() {
        if (root.summonedOn !== "" && root.hasScreen(root.summonedOn)) return root.summonedOn
        const m = Hyprland.focusedMonitor
        if (m && root.hasScreen(m.name)) return m.name
        return Quickshell.screens.length > 0 ? Quickshell.screens[0].name : ""
    }
    Connections {
        target: pillState
        function onCardIdChanged() { root.cardScreen = pillState.cardId === "" ? "" : root.focusedScreenName() }
        // The person pressed Reply, or clicked in the card's box: the bar on the card's screen takes the keyboard and
        // the box has it. (A card an agent opened asks for nothing, so it never takes the keys from what is typed.)
        function onCardKeys() {
            root.cardKeysWanted(root.hasScreen(root.cardScreen) ? root.cardScreen : root.focusedScreenName())
        }
    }

    // "Starting" shows for the first seconds, until agentd answers; after that, no answer is "offline".
    Timer { interval: 15000; running: true; onTriggered: pillState.booting = false }

    // The ground under everything: the wallpaper, on every screen.
    Wallpaper { reducedMotion: Kit.Theme.reducedMotion }

    // The desk: cards on two rails under every window, strips beside the pill when they fold.
    DeskState {
        id: deskState
        pill: pillState
        onOutgoing: msg => root.write(msg)
        onWindowOpened: pillState.windowOpened()
        // The words "widgets" put the card above the pill, and the pill takes the keyboard for it (every time:
        // the card is open only while the pill holds it).
        onWidgetsRequested: {
            pillState.closeAi()
            root.takeKeyboard()
            if (root.summonedOn === "") deskState.closeWidgets()   // no screen to hold it: it has no card
        }
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
    // The rails stop where the bar's zone starts, and that zone grows with the rows of chips above the pill.
    readonly property int deskZone: {
        for (const w of bars.instances) if (w.onDesk) return w.exclusiveZone
        return 64
    }
    Binding { target: deskState; property: "rowZone"; value: root.deskZone }
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
        // A message as agentd would send it, for demos and the desktop test. A notice, or its end, only on a
        // bar started for a test (BOMBADIL_BAR_INJECT=1, which no session of the image sets): the line above the
        // pill is where agentd's warnings about mail show, and any process of the person's, an agent's shell
        // included, that could say what it likes there could take one down. The other kinds are as open as
        // they were (docs/MAIL.md says so).
        function inject(message: string): void {
            let ev
            try { ev = JSON.parse(message) } catch (e) { return }
            if (ev && String(ev.type).startsWith("notice") && Quickshell.env("BOMBADIL_BAR_INJECT") !== "1") return
            root.handle(message)
        }
    }
    IpcHandler {
        target: "line"
        // What the line above the pill is saying, notices included, as JSON: for the VM smoke test and the
        // desktop test.
        function state(): string { return JSON.stringify(pillState.snapshot()) }
    }

    // The coding sessions (dev.py): their dots, and the line the pill shows while one waits.
    DevState {
        id: devState
        onOutgoing: msg => root.write(msg)
    }
    IpcHandler {
        target: "dev"
        // Where the first screen's chips sit, as JSON.
        function chips(): string {
            const w = bars.instances.length > 0 ? bars.instances[0] : null
            return JSON.stringify(w ? w.chipsGeometry() : {})
        }
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
                else { pillState.lost(); deskState.lost(); devState.lost() }
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
        if (notifications.item) notifications.item.handle(ev)
        devState.handle(ev)   // only its own "dev" messages
    }

    function write(msg) {
        if (!root.connected || !root.agentd) return
        root.agentd.write(JSON.stringify(msg) + "\n")
        root.agentd.flush()
    }

    // Super tapped (Hyprland runs `bombadil pill`, agentd relays it here): the pill on the
    // focused screen takes the keyboard. A second tap gives it back.
    function summon(text, tip) {
        const m = Hyprland.focusedMonitor
        const name = m ? m.name : (Quickshell.screens.length > 0 ? Quickshell.screens[0].name : "")
        root.cardKeys = false
        if (text) {
            // Words to finish: always take the keyboard, never toggle it away. A card holding the keys
            // here gives them to the pill.
            root.draftTip = tip === true
            root.draft = text
            root.summonedOn = name
            if (pillState.personaAsk !== null) pillState.focusPill()
            return
        }
        // Super while the card holds the keyboard here means the pill: the card stays, the keys move.
        if (pillState.personaAsk !== null && root.summonedOn === name) { pillState.focusPill(); return }
        root.summonedOn = root.summonedOn === name ? "" : name
    }

    function release() { root.cardKeys = false; root.summonedOn = "" }

    function focusedScreen() {
        const m = Hyprland.focusedMonitor
        return m ? m.name : (Quickshell.screens.length > 0 ? Quickshell.screens[0].name : "")
    }

    // A screen unplugged while the card or a welcome was on it: show them everywhere, not nowhere.
    function hasScreen(name) {
        for (let i = 0; i < Quickshell.screens.length; i++)
            if (Quickshell.screens[i].name === name) return true
        return false
    }

    // The card asks what to call you: it comes up on the focused screen and takes the keyboard
    // there. Unlike summon() this never gives the keyboard back, and it works on every compositor.
    function summonKeep() {
        root.cardKeys = true
        root.cardOn = root.focusedScreen()
        root.summonedOn = root.cardOn
    }

    // The pill that was typed into (else the one on the focused screen) takes the keyboard and keeps it: no toggle,
    // for what asks for it (the Widgets card), not for a tap on Super. The keys are the pill's, as after a tap on Super.
    function takeKeyboard() { root.cardKeys = false; root.summonedOn = root.focusedScreenName() }

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
        id: bars
        model: Quickshell.screens
        PanelWindow {
            id: win
            required property var modelData
            screen: modelData
            readonly property bool summoned: root.summonedOn !== "" && root.summonedOn === modelData.name
            readonly property bool cardHere: root.cardOn === "" || root.cardOn === modelData.name || !root.hasScreen(root.cardOn)
            readonly property bool welcomeHere: root.welcomeOn === "" || root.welcomeOn === modelData.name || !root.hasScreen(root.welcomeOn)
            // The desk narrows the pill and turns it into a capsule under a full-screen window on the
            // screen it lives on; the pill on another screen is as it always was.
            readonly property bool onDesk: modelData.name === root.deskScreen
            readonly property bool capsule: onDesk && deskState.capsule
            readonly property real pillMax: onDesk ? deskState.pillWidth : Kit.Theme.pillMaxWidth
            // A picture beside a window is wider than the pill (see DeskState.pictureTarget).
            readonly property real pictureMax: onDesk ? deskState.pictureWidth : Kit.Theme.pillMaxWidth
            anchors { left: true; right: true; bottom: true }
            implicitHeight: column.implicitHeight + 24
            color: "transparent"
            // Where the session chips sit against the pill, for the desktop test (`quickshell ipc call dev chips`).
            function chipsGeometry() {
                const c = sessionChips.mapToItem(null, 0, 0)
                const p = pillBox.mapToItem(null, 0, 0)
                return { visible: sessionChips.visible, x: c.x, y: c.y, width: sessionChips.width,
                         height: sessionChips.height, pillX: p.x, pillY: p.y, pillWidth: pillBox.width }
            }
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
                           + (sessionRow.visible ? sessionRow.implicitHeight + column.spacing : 0)
            // Clicks go through the transparent parts of the bar to the windows behind it.
            mask: Region {
                Region { item: cardHost }
                Region { item: statusLine }
                // (a hidden item keeps its last place)
                Region { item: personaCard.visible ? personaCard : null }
                Region { item: foundChips.visible ? foundChips : null }
                Region { item: setupChips.visible ? setupChips : null }   // (a hidden item keeps its last place)
                Region { item: chips }
                Region { item: sessionChips.visible ? sessionChips : null }   // (a hidden item keeps its last place)
                Region { item: appChips }
                Region { item: aiCard.visible ? aiCard : null }
                Region { item: widgetsCard.visible ? widgetsCard : null }
                Region { item: pillBox }
                Region { item: stripsLeft }
                Region { item: stripsRight }
            }

            // The card's box is to have the keyboard when this bar gets it, not the pill's field.
            property bool boxNext: false
            onSummonedChanged: {
                if (summoned) {
                    if (boxNext) focusBox()
                    // The persona card, when it is up here, is where typing goes; the pill stays one click away.
                    else if (root.cardKeys && personaCard.shown) personaCard.takeKeys()
                    else input.forceActiveFocus()
                } else boxNext = false
                grab.active = summoned
                takeDraft()
            }
            function focusBox() {
                boxNext = false
                // (a frame later: the card may have been drawn in this same turn of the loop)
                Qt.callLater(() => { if (!(cardHost.item && cardHost.item.focusBox())) input.forceActiveFocus() })
            }
            Connections {
                target: root
                function onCardKeysWanted(name) {
                    if (name !== win.modelData.name) return
                    const had = win.summoned
                    win.boxNext = true
                    root.summonedOn = name
                    if (had) win.focusBox()      // (already summoned: nothing changes below to do it)
                }
            }
            Connections {
                target: pillState
                function onTyping() { if (win.summoned) idle.restart() }
            }
            function takeDraft() {
                if (!summoned || root.draft === "") return
                // A tip's words wait in an empty pill and leave typed words alone (PillState.summonText).
                const kept = root.draftTip && input.text.trim() !== ""
                input.text = pillState.summonText(input.text, root.draft, root.draftTip)
                if (!kept) input.cursorPosition = input.text.length
                root.draft = ""
            }
            Connections {
                target: root
                function onDraftChanged() { win.takeDraft() }
            }

            function takeCard() { if (personaCard.shown) personaCard.takeKeys() }

            Connections {
                target: pillState
                // The card asked while this screen already had the keyboard: summoned did not
                // change, so hand it over here. Later, once the card is shown.
                function onAsked() { if (win.summoned) Qt.callLater(win.takeCard) }
                function onFocusPill() { if (win.summoned) input.forceActiveFocus() }
                // Folded by anything (an answer from another bar, a dropped socket): the pill's field is the
                // focus again, and a keyboard the card was holding goes back to the windows at once.
                function onPersonaAskChanged() {
                    if (pillState.personaAsk !== null) return
                    input.forceActiveFocus()
                    if (win.summoned && root.cardKeys) root.release()
                }
            }

            // Clicking the pill is the same as tapping Super: it is where you type. (Elsewhere the
            // layer takes clicks on demand by itself.)
            function summonHere() { if (root.hyprland) root.summonedOn = modelData.name }
            // A click on the pill: its field has the keys, card or no card.
            function focusPill() {
                root.cardKeys = false
                summonHere()
                if (summoned) input.forceActiveFocus()
            }

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
                // (a person writing a reply in a card is not idle, and a longer pause is a person thinking)
                interval: pillState.composing ? 120000 : (input.text === "" ? 20000 : 60000)
                // Not while a card is up: the Widgets card is read at the person's pace, and the persona card is
                // waiting for an answer that is slow to type; the keyboard stays.
                running: win.summoned && !widgetsCard.shown && !personaCard.shown
                onTriggered: root.release()
            }

            // The cards that folded, beside the pill. A drag from them reports the screen's coordinates,
            // so they need to know where this window sits (only where the desk lives).
            DeskStrips {
                id: stripsLeft
                desk: deskState; side: "left"; active: win.onDesk
                origin: Qt.point(0, deskState.screenHeight - win.height)
                pillEdge: column.x + pillBox.x
                pillCentreY: column.y + pillBox.y + pillBox.height / 2
            }
            DeskStrips {
                id: stripsRight
                desk: deskState; side: "right"; active: win.onDesk
                origin: Qt.point(0, deskState.screenHeight - win.height)
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
                    // As wide as the pill, so it stays between the desk's rails; beside a window, up to
                    // about 560 where the rails leave the room (the pill is 360 then). A message or a task needs less.
                    Layout.maximumWidth: {
                        const wide = Math.max(360, win.pictureMax)
                        const want = cardHost.item ? cardHost.item.preferredWidth : 0
                        return want > 0 ? Math.min(want, wide) : wide
                    }
                    Layout.alignment: Qt.AlignHCenter
                    Component.onCompleted: setSource("CardHost.qml", {
                        pill: pillState, maxHeight: Math.round(modelData.height * 0.6) })
                    // A full-screen window on this screen, or the Widgets card, puts the picture away (it is still there
                    // after): the picture may take 60% of the screen, and the two together would push its top off a
                    // small one.
                    Binding { target: cardHost.item; property: "suppressed"; value: win.capsule || widgetsCard.shown; when: cardHost.item !== null }
                    // A card with a box is drawn on one screen (a picture, on every one: the host knows which is which).
                    Binding {
                        target: cardHost.item; property: "onThisScreen"; when: cardHost.item !== null
                        value: root.cardScreen === "" || root.cardScreen === win.modelData.name || !root.hasScreen(root.cardScreen)
                    }
                }

                StatusLine {
                    id: statusLine
                    pill: pillState
                    here: win.welcomeHere
                    Layout.fillWidth: true
                    Layout.maximumWidth: Math.max(360, win.pillMax)
                    Layout.alignment: Qt.AlignHCenter
                }

                PersonaCard {
                    id: personaCard
                    pill: pillState
                    here: win.cardHere
                    Layout.fillWidth: true
                    Layout.maximumWidth: 620
                    Layout.alignment: Qt.AlignHCenter
                    // A click on the card is a click on the pill: take the keyboard (Hyprland gives none unasked).
                    onWantKeys: {
                        root.cardKeys = true
                        win.summonHere()
                        win.takeCard()
                    }
                    onClosed: {
                        root.release()
                        input.forceActiveFocus()
                    }
                }

                // While the AI rests: what the sentence you typed nearly names on this computer (an app, a
                // word, a past ask), under the line that says the ask is kept. A press opens it.
                FoundChips {
                    id: foundChips
                    pill: pillState
                    // As wide as its chips (the input mask lets clicks beside them through), and no wider
                    // than the bar: three long labels shrink and cut themselves short.
                    Layout.fillWidth: false
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: Math.min(Math.max(360, win.pillMax), column.width)
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

                // A hovered dot's last few lines, over its chip. It takes no clicks.
                SessionPeek {
                    id: sessionPeek
                    dev: devState
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: implicitWidth
                    // Over its chip, at the pill's left end.
                    Layout.leftMargin: Math.max(0, Math.min(sessionChips.x, column.width - implicitWidth))
                    Layout.rightMargin: 0
                }

                // The sessions' chips, above the pill's left end (the desk's strips keep the row beside it).
                Item {
                    id: sessionRow
                    // Not sessionChips.visible: a child is only visible while its parent is, so the row
                    // would never come back once hidden.
                    visible: devState.sessions.length > 0
                    Layout.fillWidth: true
                    Layout.maximumWidth: win.pillMax
                    Layout.alignment: Qt.AlignHCenter
                    implicitHeight: sessionChips.implicitHeight
                    SessionChips {
                        id: sessionChips
                        dev: devState
                        x: 14
                        width: implicitWidth
                        height: implicitHeight
                    }
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
                            Behavior on border.color { ColorAnimation { duration: Kit.Theme.duration(150) } }
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

                // The AI card: one row per AI with a switch, opened by a click on the stone while no turn runs.
                AiCard {
                    id: aiCard
                    pill: pillState
                    Layout.fillWidth: false
                    Layout.alignment: Qt.AlignHCenter
                }

                // The Widgets card: what each widget is set to (Keep, When needed, Off), opened by the word
                // "widgets". Open only while this pill holds the keyboard.
                WidgetsCard {
                    id: widgetsCard
                    desk: deskState
                    active: win.summoned
                    Layout.fillWidth: false
                    Layout.alignment: Qt.AlignHCenter
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
                    TapHandler { onTapped: win.focusPill() }

                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                        spacing: 8

                        // The stone: Bombadil's mark, in the dot's place. Its face says what the machine is
                        // doing (Kit.Stone); while a turn runs, hover turns it into Stop and a click stops
                        // it; otherwise a click opens the AI card (and another closes it).
                        Rectangle {
                            id: dotBox
                            readonly property bool stoppable: pillState.stoppable && dotHover.hovered && !win.capsule
                            implicitWidth: stoppable ? stopRow.implicitWidth + 16 : 24
                            implicitHeight: 24
                            radius: 12
                            color: stoppable ? Kit.Theme.accentSoft : "transparent"
                            Behavior on implicitWidth { NumberAnimation { duration: Kit.Theme.fast } }
                            Kit.Stone {
                                id: stone
                                objectName: "stone"
                                visible: !dotBox.stoppable
                                anchors.centerIn: parent
                                // The screen that holds the keyboard leans toward what you type.
                                face: win.summoned && pillState.face === "rest" ? "listening" : pillState.face
                                reducedMotion: Kit.Theme.reducedMotion
                            }
                            Row {
                                id: stopRow
                                visible: dotBox.stoppable
                                anchors.centerIn: parent
                                spacing: 6
                                Rectangle { width: 9; height: 9; radius: 2; color: Kit.Theme.accent; anchors.verticalCenter: parent.verticalCenter }
                                Text { font.family: Kit.Theme.fontFamily; text: "Stop"; color: Kit.Theme.accentInk; font.pixelSize: Kit.Theme.smallSize }
                            }
                            HoverHandler { id: dotHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: pillState.stoppable ? pillState.stop() : pillState.toggleAi() }
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
                                // While a coding session waits for you, the empty pill says so ("reviewer on
                                // Bombadil: run the migration?") and Tab goes there; else, while the AI rests,
                                // it says what still works and when asks run.
                                placeholderText: pillState.face === "starting" ? "Starting"
                                                 : (!root.connected ? "Waiting for agentd…"
                                                    : (devState.line !== "" ? devState.line + "   ⇥"
                                                       : (pillState.restHint || "Ask anything")))
                                color: Kit.Theme.fg
                                placeholderTextColor: devState.line !== "" && root.connected && pillState.face !== "starting"
                                                      ? Kit.Theme.accentInk : Kit.Theme.muted
                                font.pixelSize: Kit.Theme.promptSize
                                background: null
                                focus: true
                                // The field takes the press itself, so the pill's own handler never sees it.
                                TapHandler { onTapped: win.focusPill() }
                                onAccepted: {
                                    if (pillState.submit(text)) {
                                        text = ""
                                        root.release()
                                    } else {
                                        pillState.dismissWelcome()   // Enter on nothing ends a greeting too
                                    }
                                }
                                onTextChanged: {
                                    if (win.summoned) idle.restart()
                                    if (text !== "") pillState.dismissWelcome()
                                    if (text !== "") pillState.closeAi()
                                    if (text !== "") deskState.closeWidgets()
                                }
                                // Tab takes the suggested name: "pass" + Tab = "passwords". In an empty
                                // pill it walks to the next session waiting for you.
                                Keys.onTabPressed: {
                                    if (text === "" && devState.sessions.length > 0) {
                                        devState.next()
                                        root.release()
                                        return
                                    }
                                    const rest = pillState.completion(text)
                                    if (rest) text = text + rest
                                }
                                // Esc puts the Widgets card away first, whatever is running: it is the card on top, and
                                // it can be open while a turn works (a launcher word does not wait for a turn), so the
                                // first Esc closes it and a second one stops the turn. Then it stops a running turn or a
                                // sign-in; otherwise it puts the AI card away, clears, then puts the line, the picture
                                // and the drawer away. Closing a card gives the keyboard back.
                                Keys.onEscapePressed: {
                                    if (widgetsCard.shown) { deskState.closeWidgets(); root.release() }
                                    else if (pillState.stoppable) pillState.stop()
                                    else if (pillState.aiOpen) { pillState.closeAi(); root.release() }
                                    else if (text !== "") text = ""
                                    else { pillState.dismiss(); pillState.dismissWelcome(); pillState.closeDetails(); root.release() }
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
