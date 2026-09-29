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

ShellRoot {
    id: root
    property bool connected: false
    // The screen whose pill has the keyboard after a tap on Super ("" = none).
    property string summonedOn: ""

    // Not "pill": inside StatusLine { pill: ... } that name is the line's own property.
    PillState {
        id: pillState
        onOutgoing: msg => root.write(msg)
        onSummoned: root.summon()
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
                if (connected) pillState.connected = true
                else pillState.lost()
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
    }

    function write(msg) {
        if (!root.connected || !root.agentd) return
        root.agentd.write(JSON.stringify(msg) + "\n")
        root.agentd.flush()
    }

    // Super tapped (Hyprland runs `bombadil pill`, agentd relays it here): the pill on the
    // focused screen takes the keyboard. A second tap gives it back.
    function summon() {
        const m = Hyprland.focusedMonitor
        const name = m ? m.name : (Quickshell.screens.length > 0 ? Quickshell.screens[0].name : "")
        root.summonedOn = root.summonedOn === name ? "" : name
    }

    function release() { root.summonedOn = "" }

    Variants {
        model: Quickshell.screens
        PanelWindow {
            id: win
            required property var modelData
            screen: modelData
            readonly property bool summoned: root.summonedOn !== "" && root.summonedOn === modelData.name
            anchors { left: true; right: true; bottom: true }
            implicitHeight: column.implicitHeight + 24
            color: "transparent"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.namespace: "bombadil-bar"
            // Keys on demand (click the pill), and at once after Super: Exclusive focuses the layer
            // immediately; going back to OnDemand on Enter or Esc hands the keyboard back.
            WlrLayershell.keyboardFocus: summoned ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand
            exclusiveZone: 64
            // Clicks go through the transparent parts of the bar to the windows behind it.
            mask: Region {
                Region { item: statusLine }
                Region { item: setupChips.visible ? setupChips : null }   // (a hidden item keeps its last place)
                Region { item: chips }
                Region { item: pillBox }
            }

            onSummonedChanged: {
                if (summoned) input.forceActiveFocus()
                grab.active = summoned
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
                interval: input.text === "" ? 20000 : 60000
                running: win.summoned
                onTriggered: root.release()
            }

            ColumnLayout {
                id: column
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
                spacing: 8

                StatusLine {
                    id: statusLine
                    pill: pillState
                    Layout.fillWidth: true
                    Layout.maximumWidth: 900
                    Layout.alignment: Qt.AlignHCenter
                }

                // Which AI, Sign in, Show sign-in: the choices under the setup line.
                SetupChips {
                    id: setupChips
                    pill: pillState
                    Layout.fillWidth: false
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 900
                }

                QueueChips {
                    id: chips
                    pill: pillState
                    // A layout fills the width by default; this one stays as wide as its chips,
                    // so the input mask lets clicks beside them through.
                    Layout.fillWidth: false
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 900
                }

                // Prompt bar
                Rectangle {
                    id: pillBox
                    Layout.fillWidth: true
                    Layout.maximumWidth: 900
                    Layout.alignment: Qt.AlignHCenter
                    implicitHeight: 52
                    radius: 26
                    color: "#f01a1d21"
                    border.color: pillState.busy ? "#d97757" : (win.summoned ? "#4a525c" : (root.connected ? "#2a2f36" : "#7a2e2e"))
                    border.width: 1.5
                    Behavior on border.color { ColorAnimation { duration: 300 } }

                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                        spacing: 8

                        // The dot. While a turn runs it is orange; hover turns it into Stop.
                        Rectangle {
                            id: dotBox
                            readonly property bool stoppable: pillState.stoppable && dotHover.hovered
                            implicitWidth: stoppable ? stopRow.implicitWidth + 16 : 24
                            implicitHeight: 24
                            radius: 12
                            color: stoppable ? "#3a2a26" : "transparent"
                            Behavior on implicitWidth { NumberAnimation { duration: 120 } }
                            Rectangle {
                                visible: !dotBox.stoppable
                                anchors.centerIn: parent
                                width: 10; height: 10; radius: 5
                                color: pillState.busy ? "#d97757" : (root.connected ? "#5fb36b" : "#c04a4a")
                                SequentialAnimation on opacity {
                                    running: pillState.busy; loops: Animation.Infinite
                                    NumberAnimation { to: 0.3; duration: 500 } NumberAnimation { to: 1; duration: 500 }
                                }
                            }
                            Row {
                                id: stopRow
                                visible: dotBox.stoppable
                                anchors.centerIn: parent
                                spacing: 6
                                Rectangle { width: 9; height: 9; radius: 2; color: "#d97757"; anchors.verticalCenter: parent.verticalCenter }
                                Text { text: "Stop"; color: "#f2c4b3"; font.pixelSize: 13 }
                            }
                            HoverHandler { id: dotHover; cursorShape: pillState.busy ? Qt.PointingHandCursor : Qt.ArrowCursor }
                            TapHandler { enabled: pillState.stoppable; onTapped: pillState.stop() }
                        }

                        Item {
                            Layout.fillWidth: true
                            implicitHeight: input.implicitHeight

                            TextField {
                                id: input
                                anchors.fill: parent
                                placeholderText: root.connected ? "Ask anything" : "Waiting for agentd…"
                                color: "#e6e8eb"
                                placeholderTextColor: "#8b939c"
                                font.pixelSize: 16
                                background: null
                                focus: true
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
                                // Esc stops a running turn; otherwise it clears, then gives the keyboard back.
                                Keys.onEscapePressed: {
                                    if (pillState.stoppable) pillState.stop()
                                    else if (text !== "") text = ""
                                    else { pillState.dismiss(); root.release() }
                                }
                            }

                            // The rest of a name Tab would complete, drawn after what was typed.
                            Row {
                                x: input.leftPadding
                                anchors.verticalCenter: parent.verticalCenter
                                visible: input.text !== "" && ghost.text !== ""
                                Text { text: input.text; font: input.font; color: "transparent"; textFormat: Text.PlainText }
                                Text { id: ghost; text: pillState.completion(input.text); font: input.font; color: "#5d646c"; textFormat: Text.PlainText }
                            }
                        }

                        // An exact launcher word: say it opens here, without the model.
                        Text {
                            readonly property string target: pillState.exact(input.text)
                            visible: target !== ""
                            text: "↵ " + target
                            color: "#8b939c"
                            font.pixelSize: 12
                        }

                        Text {
                            id: clock
                            text: Qt.formatTime(new Date(), "HH:mm"); color: "#8b939c"; font.pixelSize: 13
                            Timer { interval: 30000; running: true; repeat: true; onTriggered: clock.text = Qt.formatTime(new Date(), "HH:mm") }
                        }
                    }
                }
            }
        }
    }
}
