import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Every kit component in its states and prop combinations. Render it with:
//   bin/bombadil-app check tests/qml/components_gallery.qml --screenshot components.png
Item {
    id: root
    width: 1500
    height: 1300

    readonly property var entries: [
        { id: 1, title: "GitHub", subtitle: "daniel@latchkey.dev", icon: "globe", when: "2 days ago" },
        { id: 2, title: "Arch Wiki", subtitle: "daniel", icon: "globe", when: "yesterday" },
        { id: 3, title: "Router admin", subtitle: "admin", icon: "wifi", when: "5 min ago" },
        { id: 4, title: "SSH key passphrase", icon: "key", when: "3 Sep" },
        { id: 5, title: "Bank", subtitle: "a long user name that gets elided at the edge", icon: "lock", when: "1 Aug" }
    ]
    readonly property var procs: {
        const names = ["firefox", "Hyprland", "quickshell", "python3", "bombadil-agentd", "pipewire",
                       "systemd", "kitty", "code", "Xwayland", "wireplumber", "dbus-broker"]
        const out = []
        for (let i = 0; i < 500; i++)
            out.push({ pid: 1000 + i * 7, name: names[i % names.length], user: i % 5 ? "daniel" : "root",
                       cpu: (i * 37 % 100) / 400, rss: Math.round(4096 * Math.pow(2, (i * 7919 % 500) / 26)),
                       state: i % 9 ? "S" : "R" })
        return out
    }

    component Title: Text {
        color: Theme.muted
        font.family: Theme.fontFamily
        font.pixelSize: Theme.captionSize
        font.weight: Font.DemiBold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.6
    }

    ColumnLayout {
        anchors { fill: parent; margins: Theme.pad }
        spacing: Theme.pad

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: Theme.pad

        // Text, icons, buttons, badges, dividers
        ColumnLayout {
            Layout.preferredWidth: 290
            Layout.minimumWidth: 290
            Layout.fillHeight: true
            spacing: Theme.pad

            Panel {
                Layout.fillWidth: true
                title: "Text"
                subtitle: "Heading, Body, Caption, Mono"
                Heading { text: "Heading 1" }
                Heading { text: "Heading 2"; level: 2 }
                Heading { text: "Heading 3"; level: 3 }
                Body { text: "Body text wraps to the width of its layout, so a long sentence like this one stays inside the card." }
                Caption { text: "Caption: secondary text for hints and units." }
                Mono { text: "/usr/lib/firefox/firefox -contentproc" }
            }

            Panel {
                Layout.fillWidth: true
                title: "Icons and buttons"
                RowLayout {
                    spacing: 10
                    Icon { name: "copy" }
                    Icon { name: "trash"; color: Theme.bad }
                    Icon { name: "check"; color: Theme.good }
                    Icon { name: "alert-triangle"; color: Theme.warn }
                    Icon { name: "info"; color: Theme.info }
                    Icon { name: "star"; color: Theme.accent; size: 24 }
                    Icon { name: "cpu"; color: Theme.muted; size: 14 }
                }
                Divider {}
                RowLayout {
                    spacing: 4
                    IconButton { icon: "copy"; tooltip: "Copy" }
                    IconButton { icon: "trash"; tone: "bad"; tooltip: "Delete" }
                    IconButton { icon: "star"; checkable: true; checked: true; tooltip: "Favorite" }
                    IconButton { icon: "pencil"; enabled: false }
                    IconButton { icon: "refresh"; size: 24 }
                    IconButton { icon: "more-horizontal"; size: 36 }
                }
                Divider {}
                Flow {
                    Layout.fillWidth: true
                    spacing: 6
                    Badge { text: "neutral" }
                    Badge { text: "weak"; tone: "bad" }
                    Badge { text: "strong"; tone: "good"; icon: "check" }
                    Badge { text: "reused"; tone: "warn"; icon: "alert-triangle" }
                    Badge { text: "new"; tone: "accent" }
                    Badge { text: "info"; tone: "info" }
                    Badge { text: "muted"; tone: "muted" }
                }
            }

            Panel {
                Layout.fillWidth: true
                title: "Layout"
                subtitle: "Divider and Spacer in a row"
                actions: [
                    IconButton { icon: "refresh"; tooltip: "Refresh" },
                    IconButton { icon: "more-vertical"; tooltip: "More" }
                ]
                RowLayout {
                    Layout.preferredHeight: 24
                    spacing: 10
                    Caption { text: "Left"; Layout.fillWidth: false }
                    Divider { vertical: true }
                    Caption { text: "Middle"; Layout.fillWidth: false }
                    Spacer {}
                    Badge { text: "right" }
                }
            }

            Panel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: "Flat panel and ScrollPane"
                padding: 0
                Panel {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    flat: true
                    padding: 0
                    ScrollPane {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        padding: Theme.pad
                        spacing: Theme.gapSmall
                        Repeater {
                            model: 12
                            Body {
                                required property int index
                                text: "Scrolled line " + (index + 1)
                            }
                        }
                    }
                }
            }
        }

        // Lists
        ColumnLayout {
            Layout.preferredWidth: 290
            Layout.minimumWidth: 290
            Layout.fillHeight: true
            spacing: Theme.pad

            SearchField { id: search }
            SearchField { text: "git" }

            Panel {
                Layout.fillWidth: true
                Layout.preferredHeight: 290
                padding: Theme.gapSmall
                ItemList {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: root.entries
                    iconRole: "icon"
                    trailingRole: "when"
                    currentIndex: 2
                }
            }

            Panel {
                Layout.fillWidth: true
                padding: Theme.gapSmall
                spacing: 2
                ListRow { Layout.fillWidth: true; title: "Title only" }
                ListRow { Layout.fillWidth: true; title: "With a badge"; icon: "shield"; trailingItem: Badge { text: "weak"; tone: "bad" } }
                ListRow { Layout.fillWidth: true; title: "With a button"; subtitle: "and a subtitle"; icon: "key"; trailingItem: IconButton { icon: "copy"; size: 28; tooltip: "Copy" } }
                ListRow { Layout.fillWidth: true; title: "Selected"; subtitle: "trailing text"; icon: "star"; selected: true; trailing: "12.4 MiB" }
            }

            Panel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: Theme.gapSmall
                ItemList {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: ["plain", "strings", "work", "too"].filter(s => s.includes("zzz"))
                    emptyText: "No entries match “zzz”"
                }
            }
        }

        // Numbers and tables
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.pad

            // Cards in a row share the tallest one's height by default.
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gap
                Panel {
                    Layout.fillWidth: true
                    Stat { Layout.fillWidth: true; label: "Memory used"; value: "11.2 GiB"; detail: "of 16 GiB"; delta: "+4%"; deltaTone: "warn"; trend: [3, 4, 4, 5, 6, 5, 7, 8, 7, 9, 10, 9, 11] }
                }
                Panel {
                    Layout.fillWidth: true
                    Stat { Layout.fillWidth: true; label: "Processes"; value: Fmt.number(312); delta: "-3"; deltaTone: "good"; detail: "1,204 threads" }
                }
                Panel {
                    Layout.fillWidth: true
                    Stat { Layout.fillWidth: true; label: "Uptime"; value: Fmt.duration(3 * 86400 + 4 * 3600) }
                }
            }

            // Bare Stats in one card line up by their labels.
            Panel {
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.pad
                    Stat { Layout.fillWidth: true; label: "CPU"; value: Fmt.percent(0.37); trend: [0.2, 0.25, 0.3, 0.28, 0.41, 0.35, 0.37] }
                    Divider { vertical: true }
                    Stat { Layout.fillWidth: true; label: "Load"; value: "1.42"; detail: "5 min 1.10" }
                    Divider { vertical: true }
                    Stat { Layout.fillWidth: true; label: "Network"; value: Fmt.bytes(2.4e6) + "/s"; delta: "↓"; deltaTone: "info"; trend: [1, 3, 2, 5, 4, 6, 5, 7] }
                    Divider { vertical: true }
                    Stat { Layout.fillWidth: true; label: "Temp"; value: "54 °C"; delta: "hot"; deltaTone: "bad" }
                }
            }

            Panel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: "DataTable"
                subtitle: "500 rows, sorted by memory"
                padding: Theme.gap
                actions: [ SearchField { Layout.preferredWidth: 180; Layout.fillWidth: false; placeholderText: "Filter" } ]
                DataTable {
                    id: table
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    columns: [
                        { key: "name", title: "Process", width: 3 },
                        { key: "pid", title: "PID", width: "72px", mono: true },
                        { key: "user", title: "User", width: 1.5 },
                        { key: "state", title: "State", width: "64px", align: "center", sortable: false },
                        { key: "cpu", title: "CPU", format: v => Fmt.percent(v, 1) },
                        { key: "rss", title: "Memory", width: 1.4, format: v => Fmt.bytes(v) }
                    ]
                    rows: root.procs
                    sortKey: "rss"
                    sortDescending: true
                    currentIndex: 1
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gap
                Panel {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    title: "DetailGrid"
                    DetailGrid {
                        Layout.fillWidth: true
                        rows: [
                            { label: "PID", value: table.current ? table.current.pid : null, mono: true, copyable: true },
                            { label: "Name", value: table.current ? table.current.name : null },
                            { label: "Memory", value: table.current ? Fmt.bytes(table.current.rss) : null },
                            { label: "Command", value: "/usr/lib/firefox/firefox -contentproc -childID 12", mono: true },
                            { label: "Parent", value: null }
                        ]
                    }
                }
                Panel {
                    Layout.preferredWidth: 220
                    Layout.fillHeight: true
                    padding: 0
                    DataTable {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.margins: Theme.gap
                        columns: [{ key: "name", title: "Name" }, { key: "size", title: "Size" }]
                        rows: []
                        emptyText: "No files"
                    }
                }
            }
        }

        // Forms, editor, empty state
        ColumnLayout {
            Layout.preferredWidth: 330
            Layout.minimumWidth: 330
            Layout.fillHeight: true
            spacing: Theme.pad

            Panel {
                Layout.fillWidth: true
                title: "Form"
                Form {
                    Field { label: "Website"; required: true; TextField { placeholderText: "github.com" } }
                    Field { label: "Username"; hint: "Email or login name"; TextField { text: "daniel" } }
                    Field { label: "Password"; error: "Too weak: add length or symbols"; PasswordField { text: "hunter22"; showStrength: true } }
                    Field { label: "Remember on this device"; Switch { checked: true } }
                }
            }

            Panel {
                Layout.fillWidth: true
                title: "Form with labelWidth"
                Form {
                    labelWidth: 84
                    Field { label: "Host"; TextField { text: "example.org" } }
                    Field { label: "Port"; hint: "1 to 65535"; SpinBox { value: 22; to: 65535 } }
                    Field { label: "Secret"; PasswordField { text: "Tr0ub4dor&3xyz!"; revealed: true; showStrength: true } }
                }
            }

            Editor {
                Layout.fillWidth: true
                Layout.fillHeight: true
                language: "python"
                text: "import json\n\n\ndef load(path):\n    \"\"\"Read the entries.\"\"\"\n    with open(path) as f:  # utf-8\n        return json.load(f)\n\n\nprint(load(\"vault.json\")[0][\"title\"], 42)\n"
            }

            Panel {
                Layout.fillWidth: true
                Layout.preferredHeight: 190
                EmptyState {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    icon: "lock"
                    title: "Vault is locked"
                    text: "Enter your master password to see your entries."
                    actionText: "Unlock"
                }
            }
        }
    }

    RowLayout {
        id: bottomStrip
        Layout.fillWidth: true
        Layout.preferredHeight: 260
        spacing: Theme.pad

        Panel {
            id: dialogSlot
            Layout.preferredWidth: 452
            Layout.fillHeight: true
            title: "ConfirmDialog"
            subtitle: "danger: true (shown non-modal here)"
        }

        Panel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            title: "Fmt"
            GridLayout {
                Layout.fillWidth: true
                columns: 4
                columnSpacing: Theme.pad
                rowSpacing: 6
                Repeater {
                    model: [
                        ["bytes(512)", Fmt.bytes(512)], ["bytes(1.5e9)", Fmt.bytes(1.5e9)],
                        ["bytes(16 GiB, 0)", Fmt.bytes(17179869184, 0)], ["percent(0.423)", Fmt.percent(0.423)],
                        ["percent(0.4237, 1)", Fmt.percent(0.4237, 1)], ["number(12840)", Fmt.number(12840)],
                        ["number(3.14159, 2)", Fmt.number(3.14159, 2)], ["compact(12840)", Fmt.compact(12840)],
                        ["compact(4.2e6)", Fmt.compact(4.2e6)], ["compact(999999)", Fmt.compact(999999)],
                        ["duration(273600)", Fmt.duration(273600)], ["duration(725)", Fmt.duration(725)],
                        ["duration(0.8)", Fmt.duration(0.8)], ["time(2026-09-27 14:05)", Fmt.time("2026-09-27T14:05:00")],
                        ["date(2026-09-27)", Fmt.date("2026-09-27T14:05:00")], ["dateTime(ms)", Fmt.dateTime(new Date(2026, 8, 27, 14, 5).getTime())],
                        ["relative(-5 min)", Fmt.relative(Date.now() - 300000)], ["relative(-1 day)", Fmt.relative(Date.now() - 86400000)],
                        ["relative(Unix s, -3 d)", Fmt.relative(Date.now() / 1000 - 3 * 86400)], ["bytes(undefined)", Fmt.bytes(undefined)]
                    ]
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 8
                        Caption { text: modelData[0]; Layout.fillWidth: false }
                        Mono { text: modelData[1]; Layout.fillWidth: false }
                    }
                }
            }
        }
    }
    }

    // Shown non-modal at a fixed spot so it can sit in the gallery.
    ConfirmDialog {
        id: confirm
        modal: false
        dim: false
        closePolicy: Popup.NoAutoClose
        width: 420
        x: 2 * Theme.pad
        y: Theme.pad + bottomStrip.y + 76
        title: "Delete entry?"
        text: "This removes “GitHub” from the vault. This cannot be undone."
        confirmText: "Delete"
        danger: true
    }
    Component.onCompleted: confirm.open()
}
