import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// A fake list+detail app on AppWindow: header actions, Stats, a DataTable, a detail
// Panel, a Form in a Panel and a toast. Render it with:
//   bin/bombadil-app check tests/qml/appwindow_demo.qml --screenshot demo.png
AppWindow {
    id: app
    width: 940
    height: 720
    title: "Memory"
    subtitle: procs.length + " processes"
    icon: "memory-stick"
    actions: [
        SearchField {
            id: search
            Layout.preferredWidth: 200
            Layout.fillWidth: false
            placeholderText: "Filter"
        },
        IconButton {
            icon: "refresh"
            tooltip: "Refresh"
            onClicked: app.toast("Refreshed")
        }
    ]

    readonly property var procs: [
        { pid: 1812, name: "firefox", user: "daniel", cpu: 0.124, rss: 1.92e9, threads: 96, command: "/usr/lib/firefox/firefox" },
        { pid: 2240, name: "firefox (Web Content)", user: "daniel", cpu: 0.061, rss: 7.4e8, threads: 31, command: "/usr/lib/firefox/firefox -contentproc -childID 4" },
        { pid: 911, name: "Hyprland", user: "daniel", cpu: 0.032, rss: 3.1e8, threads: 18, command: "Hyprland" },
        { pid: 1204, name: "quickshell", user: "daniel", cpu: 0.018, rss: 2.6e8, threads: 12, command: "qs -c bombadil" },
        { pid: 1377, name: "bombadil-agentd", user: "daniel", cpu: 0.009, rss: 1.8e8, threads: 9, command: "python3 -m bombadil.agentd" },
        { pid: 1502, name: "code", user: "daniel", cpu: 0.041, rss: 6.9e8, threads: 44, command: "/opt/visual-studio-code/code" },
        { pid: 733, name: "pipewire", user: "daniel", cpu: 0.004, rss: 2.1e7, threads: 3, command: "/usr/bin/pipewire" },
        { pid: 1, name: "systemd", user: "root", cpu: 0.0, rss: 1.4e7, threads: 1, command: "/sbin/init" },
        { pid: 2931, name: "kitty", user: "daniel", cpu: 0.002, rss: 9.8e7, threads: 14, command: "kitty" },
        { pid: 3107, name: "python3", user: "daniel", cpu: 0.27, rss: 4.2e8, threads: 6, command: "python3 train.py --epochs 40" },
        { pid: 612, name: "dbus-broker", user: "root", cpu: 0.001, rss: 6.2e6, threads: 1, command: "dbus-broker --scope system" },
        { pid: 745, name: "wireplumber", user: "daniel", cpu: 0.003, rss: 3.3e7, threads: 5, command: "/usr/bin/wireplumber" }
    ]
    readonly property var shown: procs.filter(p => p.name.toLowerCase().includes(search.text.toLowerCase()))
    readonly property var selected: table.current

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gap

        Panel {
            Layout.fillWidth: true
            Stat {
                Layout.fillWidth: true
                label: "Memory used"
                value: Fmt.bytes(11.2 * 1073741824)
                detail: "of 16 GiB"
                delta: "+4%"
                deltaTone: "warn"
                trend: [8.1, 8.4, 8.3, 9.0, 9.6, 9.2, 9.9, 10.4, 10.1, 10.8, 11.0, 10.9, 11.2]
            }
        }
        Panel {
            Layout.fillWidth: true
            Stat {
                Layout.fillWidth: true
                label: "Swap"
                value: Fmt.bytes(0)
                detail: "8 GiB free"
            }
        }
        Panel {
            Layout.fillWidth: true
            Stat {
                Layout.fillWidth: true
                label: "Pressure"
                value: Fmt.percent(0.023, 1)
                delta: "calm"
                deltaTone: "good"
                detail: "some avg10"
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Theme.gap

        Panel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: Theme.gapSmall
            DataTable {
                id: table
                Layout.fillWidth: true
                Layout.fillHeight: true
                columns: [
                    { key: "name", title: "Process", width: 3 },
                    { key: "pid", title: "PID", width: "64px", mono: true },
                    { key: "cpu", title: "CPU", format: v => Fmt.percent(v, 1) },
                    { key: "rss", title: "Memory", width: 1.3, format: v => Fmt.bytes(v) }
                ]
                rows: app.shown
                sortKey: "rss"
                sortDescending: true
                currentIndex: 0
                emptyText: "No process matches “" + search.text + "”"
                onActivated: row => app.toast("Opened " + row.name)
            }
        }

        ColumnLayout {
            Layout.preferredWidth: 300
            Layout.maximumWidth: 300
            Layout.fillHeight: true
            spacing: Theme.gap

            Panel {
                Layout.fillWidth: true
                title: app.selected ? app.selected.name : "Nothing selected"
                subtitle: app.selected ? app.selected.user : ""
                actions: [
                    IconButton { icon: "copy"; tooltip: "Copy command"; size: 28 }
                ]
                DetailGrid {
                    Layout.fillWidth: true
                    rows: app.selected ? [
                        { label: "PID", value: app.selected.pid, mono: true },
                        { label: "Memory", value: Fmt.bytes(app.selected.rss) },
                        { label: "CPU", value: Fmt.percent(app.selected.cpu, 1) },
                        { label: "Command", value: app.selected.command, mono: true }
                    ] : []
                }
            }

            Panel {
                Layout.fillWidth: true
                title: "Send a signal"
                Form {
                    Field {
                        label: "Signal"
                        ComboBox { model: ["TERM", "KILL", "STOP", "CONT", "HUP"] }
                    }
                    Field {
                        label: "Reason"
                        hint: "Shown in the log"
                        TextField { placeholderText: "Optional" }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Spacer {}
                    Button {
                        text: "Send"
                        highlighted: true
                        icon.source: Theme.icon("send")
                        onClicked: app.toast("Sent TERM to " + (app.selected ? app.selected.name : "nothing"), "good")
                    }
                }
            }

            Spacer {}
        }
    }

    Component.onCompleted: toast("Sent TERM to python3", "good")
}
