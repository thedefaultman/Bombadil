//@ pragma UseQApplication
// The Bombadil bar: a Quickshell shell that is the whole visible UI at login.
// A thin bar at the bottom holds the prompt; the agent's reply grows above it and
// fades when done. Everything else on screen is a panel or an app the agent opened.
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ShellRoot {
    id: root
    property bool busy: false
    property string provider: "…"
    property string transcript: ""
    property bool connected: false

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
            onConnectionStateChanged: root.connected = connected
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
        if (ev.type === "status") { root.busy = ev.busy; root.provider = ev.provider; return }
        switch (ev.kind) {
        case "turn_start": root.transcript = "› " + ev.prompt + "\n"; break
        case "text": root.transcript += ev.text + "\n"; break
        case "tool": root.transcript += "  ⚙ " + ev.name + "\n"; break
        case "error": root.transcript += "✗ " + ev.text + "\n"; break
        case "turn_end": fade.restart(); break
        }
    }

    function send(text) {
        if (!text.trim()) return
        if (!root.connected) return
        root.agentd.write(JSON.stringify({ type: "prompt", text: text }) + "\n")
        root.agentd.flush()
    }

    Variants {
        model: Quickshell.screens
        PanelWindow {
            required property var modelData
            screen: modelData
            anchors { left: true; right: true; bottom: true }
            implicitHeight: column.implicitHeight + 24
            color: "transparent"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.namespace: "bombadil-bar"
            // Without this a layer surface never gets keys and the prompt cannot be typed in.
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand
            exclusiveZone: 64

            ColumnLayout {
                id: column
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 12 }
                spacing: 8

                // Reply area
                Rectangle {
                    id: replyBox
                    Layout.fillWidth: true
                    Layout.maximumWidth: 900
                    Layout.alignment: Qt.AlignHCenter
                    opacity: root.transcript.length > 0 ? 1 : 0
                    visible: opacity > 0
                    implicitHeight: Math.min(reply.implicitHeight + 24, 360)
                    radius: 14
                    color: "#e01a1d21"
                    Behavior on opacity { NumberAnimation { duration: 250 } }
                    Flickable {
                        anchors.fill: parent; anchors.margins: 12
                        contentHeight: reply.implicitHeight
                        clip: true
                        onContentHeightChanged: contentY = Math.max(0, contentHeight - height)
                        Text {
                            id: reply
                            width: parent.width
                            text: root.transcript
                            color: "#e6e8eb"
                            font.pixelSize: 15
                            wrapMode: Text.Wrap
                        }
                    }
                    Timer { id: fade; interval: 12000; onTriggered: root.transcript = "" }
                    TapHandler { onTapped: fade.stop() }
                }

                // Prompt bar
                Rectangle {
                    Layout.fillWidth: true
                    Layout.maximumWidth: 900
                    Layout.alignment: Qt.AlignHCenter
                    implicitHeight: 52
                    radius: 26
                    color: "#f01a1d21"
                    border.color: root.busy ? "#d97757" : (root.connected ? "#2a2f36" : "#7a2e2e")
                    border.width: 1.5
                    Behavior on border.color { ColorAnimation { duration: 300 } }

                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 12
                        spacing: 10
                        Rectangle {
                            width: 10; height: 10; radius: 5
                            color: root.busy ? "#d97757" : (root.connected ? "#5fb36b" : "#c04a4a")
                            SequentialAnimation on opacity {
                                running: root.busy; loops: Animation.Infinite
                                NumberAnimation { to: 0.3; duration: 500 } NumberAnimation { to: 1; duration: 500 }
                            }
                        }
                        TextField {
                            id: input
                            Layout.fillWidth: true
                            placeholderText: root.connected ? ("Ask " + root.provider + " anything…") : "Waiting for agentd…"
                            color: "#e6e8eb"
                            placeholderTextColor: "#8b939c"
                            font.pixelSize: 16
                            background: null
                            focus: true
                            onAccepted: { root.send(text); text = "" }
                            Keys.onEscapePressed: { if (root.busy && root.connected) { root.agentd.write(JSON.stringify({type: "cancel"}) + "\n"); root.agentd.flush() } root.transcript = "" }
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
