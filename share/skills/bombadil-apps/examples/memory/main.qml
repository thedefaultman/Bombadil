import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

AppWindow {
    id: win
    title: "Memory"
    subtitle: Fmt.bytes(mem.total) + " on " + System.hostname
    icon: "memory-stick"
    width: 1120; height: 760
    actions: [
        SearchField {
            id: search
            Layout.preferredWidth: 220; Layout.fillWidth: false
            placeholderText: "Search processes"
        }
    ]

    readonly property var mem: System.memory
    readonly property var info: System.meminfo
    // details() is a one-off read; mentioning procs.list re-reads it on every refresh.
    readonly property var proc: procs.list && table.current ? procs.details(table.current.pid) : null

    Series { id: used; value: win.mem.used; capacity: 180 }
    Series { id: cache; value: win.mem.cached; capacity: 180 }
    Processes { id: procs; sortBy: "memory"; filter: search.text }

    // Headline numbers, four equal cards
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gap
        uniformCellSizes: true
        Panel {
            Layout.fillWidth: true
            Stat {
                label: "Used"; value: Fmt.bytes(win.mem.used)
                detail: Fmt.percent(System.memoryUsage) + " of " + Fmt.bytes(win.mem.total)
            }
        }
        Panel {
            Layout.fillWidth: true
            Stat {
                label: "Available"; value: Fmt.bytes(win.mem.available)
                detail: Fmt.bytes(win.mem.free) + " free, the rest reclaimable cache"
            }
        }
        Panel {
            Layout.fillWidth: true
            Stat {
                label: "Swap"; value: win.mem.swapTotal > 0 ? Fmt.bytes(win.mem.swapUsed) : "Off"
                detail: win.mem.swapTotal > 0 ? "of " + Fmt.bytes(win.mem.swapTotal) : "No swap configured"
            }
        }
        Panel {
            Layout.fillWidth: true
            Stat {
                label: "Pressure"; value: System.pressure ? Fmt.percent(System.pressure.memory / 100, 1) : "—"
                detail: System.pressure ? "Stalled on memory, last 10 s" : "Kernel has no PSI"
            }
        }
    }

    // History and breakdown
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gap
        Panel {
            Layout.fillWidth: true
            title: "Memory use"
            subtitle: "Last 3 minutes"
            LineChart {
                Layout.fillWidth: true; Layout.preferredHeight: 150
                series: [{ name: "Used", values: used.values }, { name: "Cache", values: cache.values }]
                capacity: used.capacity
                yMax: win.mem.total
                format: v => Fmt.bytes(v)
                xFormat: i => i === used.capacity - 1 ? "now" : Fmt.duration(used.capacity - 1 - i) + " ago"
            }
        }
        Panel {
            Layout.preferredWidth: 360
            title: "Where it goes"
            subtitle: "Kernel counters from /proc/meminfo"
            StackedBar {
                Layout.fillWidth: true
                total: win.info.MemTotal
                format: v => Fmt.bytes(v)
                parts: [
                    { label: "Apps", value: win.info.AnonPages },
                    { label: "Page cache", value: win.info.Cached - win.info.Shmem },
                    { label: "Shared", value: win.info.Shmem },
                    { label: "Buffers", value: win.info.Buffers },
                    // Whatever is neither free nor counted above: slab, page tables, stacks, drivers
                    { label: "Kernel", value: Math.max(0, win.info.MemTotal - win.info.MemFree - win.info.AnonPages
                                                       - win.info.Cached - win.info.Buffers) }
                ]
            }
            DetailGrid {
                Layout.fillWidth: true
                rows: [
                    { label: "Dirty", value: Fmt.bytes(win.info.Dirty) },
                    { label: "Mapped", value: Fmt.bytes(win.info.Mapped) },
                    { label: "Committed", value: Fmt.bytes(win.info.Committed_AS) }
                ]
            }
        }
    }

    // Processes and the selected one
    RowLayout {
        Layout.fillWidth: true; Layout.fillHeight: true
        spacing: Theme.gap
        Panel {
            Layout.fillWidth: true; Layout.fillHeight: true
            title: "Processes"
            subtitle: procs.count + (procs.count === 1 ? " process" : " processes")
            DataTable {
                id: table
                Layout.fillWidth: true; Layout.fillHeight: true
                rows: procs.list
                sortKey: "memory"; sortDescending: true
                emptyText: "No process matches “" + search.text + "”"
                columns: [
                    { key: "name", title: "Process", width: 3 },
                    { key: "pid", title: "PID", width: "72px", mono: true },
                    { key: "user", title: "User", width: 1 },
                    { key: "memory", title: "Memory", width: "96px", format: v => Fmt.bytes(v) },
                    { key: "memoryPercent", title: "% RAM", width: "76px", format: v => Fmt.percent(v / 100, 1) },
                    { key: "cpu", title: "CPU", width: "64px", format: v => Fmt.percent(v) },
                    { key: "threads", title: "Threads", width: "76px" }
                ]
            }
        }
        ProcessDetails {
            Layout.preferredWidth: 360; Layout.fillHeight: true
            proc: win.proc
            onEndRequested: { confirm.target = win.proc; confirm.open() }
        }
    }

    ConfirmDialog {
        id: confirm
        property var target: null   // copied on open, so a refresh can't change what gets ended
        title: "End " + (target?.name ?? "process") + "?"
        text: "Frees about " + Fmt.bytes(target?.uss) + ". PID " + target?.pid
              + " is asked to quit (SIGTERM); unsaved work in it may be lost."
        confirmText: "End process"
        danger: true
        onConfirmed: {
            if (procs.kill(target.pid)) {
                win.toast(target.name + " ended", "good");
                procs.refresh();
            } else {
                win.toast("Couldn't end " + target.name, "bad");
            }
        }
    }
}
