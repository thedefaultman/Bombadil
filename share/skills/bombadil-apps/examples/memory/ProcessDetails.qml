import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// What one process really costs, from Processes.details(pid).
Panel {
    id: root
    property var proc: null
    signal endRequested()

    title: proc ? proc.name : "Details"
    subtitle: proc ? "PID " + proc.pid + " · " + proc.user + " · " + proc.state : ""
    actions: [
        Button {
            visible: !!root.proc
            text: "End"
            icon.source: Theme.icon("x")
            danger: true
            onClicked: root.endRequested()
        }
    ]

    EmptyState {
        visible: !root.proc
        Layout.fillWidth: true; Layout.fillHeight: true
        icon: "memory-stick"
        title: "No process selected"
        text: "Select a process to see what it really costs."
    }
    // Scrolls when the window is short; the End button stays in the header.
    ScrollPane {
        visible: !!root.proc
        Layout.fillWidth: true; Layout.fillHeight: true
        Stat {
            label: "Freed if it ends"
            value: Fmt.bytes(root.proc?.uss)
            detail: "Memory only this process uses (USS)"
        }
        DetailGrid {
            Layout.fillWidth: true
            rows: root.proc ? [
                { label: "Resident (RSS)", value: Fmt.bytes(root.proc.rss) },
                { label: "Proportional (PSS)", value: Fmt.bytes(root.proc.pss) },
                { label: "Shared", value: Fmt.bytes(root.proc.shared) },
                { label: "Swapped", value: Fmt.bytes(root.proc.swap) },
                { label: "Open files", value: root.proc.fds },
                { label: "OOM score", value: root.proc.oomScore },
                { label: "Started", value: Fmt.relative(root.proc.started) }
            ] : []
        }
        Caption {
            text: root.proc?.command ?? ""
            font.family: Theme.monoFamily
            maximumLineCount: 3
            elide: Text.ElideRight
        }
    }
}
