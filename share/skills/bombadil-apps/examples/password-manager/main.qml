import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

AppWindow {
    id: win
    title: "Password Manager"
    icon: "key"
    width: 760; height: 560
    subtitle: entries.length === 0 ? "" : entries.length === 1 ? "1 item" : entries.length + " items"

    actions: [
        SearchField { id: search; visible: vault.unlocked && entries.length > 0; Layout.preferredWidth: 200; Layout.fillWidth: false },
        IconButton { icon: "lock"; tooltip: "Lock"; visible: vault.unlocked; onClicked: vault.lock() },
        Button { text: "New"; icon.source: Theme.icon("plus"); highlighted: true; visible: vault.unlocked; onClicked: editor.openFor(null) }
    ]

    // Everything secret lives in the vault: data/passwords.vault, encrypted with the master password.
    // It locks itself after 5 idle minutes; nothing secret may stay on screen when it does.
    Vault {
        id: vault; name: "passwords"
        onUnlockedChanged: if (!unlocked) { editor.close(); confirmDelete.close() }
    }

    readonly property var entries: vault.unlocked ? vault.data : []
    readonly property var shown: entries
        .filter(e => [e.name, e.username, e.url].join(" ").toLowerCase().includes(search.text.toLowerCase()))
        .sort((a, b) => a.name.localeCompare(b.name))
    readonly property var strengthNames: ["Very weak", "Weak", "Fair", "Good", "Strong"]
    readonly property var strengthTones: ["bad", "bad", "warn", "good", "good"]

    function save(entry) {
        vault.data = entries.filter(e => e.id !== entry.id).concat([entry])
        list.currentIndex = shown.findIndex(e => e.id === entry.id)
        toast("Saved " + entry.name, "good")
    }
    function remove(entry) {
        const i = list.currentIndex
        vault.data = entries.filter(e => e.id !== entry.id)
        list.currentIndex = Math.min(i, shown.length - 1)   // select the next entry
        toast("Deleted " + entry.name)
    }

    Shortcut { sequence: "Ctrl+N"; enabled: vault.unlocked; onActivated: editor.openFor(null) }

    // One screen at a time: locked, unlocked but empty, or the list with its details.
    StackLayout {
        Layout.fillWidth: true; Layout.fillHeight: true
        currentIndex: !vault.unlocked ? 0 : entries.length === 0 ? 1 : 2

        LockScreen { vault: vault; onUnlocked: { search.text = ""; list.currentIndex = 0 } }

        EmptyState {
            icon: "key"
            title: "No passwords yet"
            text: "Save a login and it is encrypted with your master password. Copy it with one click when you need it."
            actionText: "Add password"
            onAction: editor.openFor(null)
        }

        RowLayout {
            spacing: Theme.gap

            ItemList {
                id: list
                Layout.preferredWidth: 260; Layout.fillHeight: true
                model: shown
                titleRole: "name"; subtitleRole: "username"
                emptyText: "No matches for “" + search.text + "”"
            }

            Panel {
                id: details
                readonly property var entry: list.current
                readonly property int strength: entry ? vault.strength(entry.password) : 0
                property bool revealed: false
                onEntryChanged: revealed = false
                Layout.fillWidth: true; Layout.fillHeight: true
                title: entry ? entry.name : ""
                subtitle: entry ? "Changed " + Fmt.relative(entry.changed) : ""
                actions: [
                    Badge {
                        text: strengthNames[details.strength]; tone: strengthTones[details.strength]; icon: "shield"
                        visible: !!details.entry
                    },
                    IconButton {
                        icon: "external-link"; tooltip: "Open website"
                        visible: !!details.entry && details.entry.url !== ""
                        onClicked: App.openUrl(details.entry.url)
                    },
                    IconButton { icon: "pencil"; tooltip: "Edit"; visible: !!details.entry; onClicked: editor.openFor(details.entry) },
                    IconButton { icon: "trash"; tooltip: "Delete"; tone: "bad"; visible: !!details.entry; onClicked: confirmDelete.open() }
                ]

                DetailGrid {
                    Layout.fillWidth: true
                    visible: !!details.entry
                    rows: details.entry ? [
                        { label: "Username", value: details.entry.username },
                        { label: "Password", value: details.revealed ? details.entry.password : "•".repeat(12), mono: true },
                        { label: "Website", value: details.entry.url },
                        { label: "Notes", value: details.entry.notes }
                    ] : []
                }
                Flow {   // wraps the buttons when the window is narrow
                    visible: !!details.entry
                    Layout.fillWidth: true
                    spacing: Theme.gapSmall
                    Button {
                        text: "Copy password"; icon.source: Theme.icon("copy"); highlighted: true
                        onClicked: { Clipboard.copy(details.entry.password, 30); win.toast("Password copied, clears in 30 s", "good") }
                    }
                    Button {
                        text: "Copy username"; icon.source: Theme.icon("user")
                        onClicked: { Clipboard.copy(details.entry.username); win.toast("Username copied", "good") }
                    }
                    Button {
                        text: details.revealed ? "Hide" : "Show"; flat: true
                        icon.source: Theme.icon(details.revealed ? "eye-off" : "eye")
                        onClicked: details.revealed = !details.revealed
                    }
                }
                EmptyState {
                    visible: !details.entry
                    Layout.fillWidth: true; Layout.fillHeight: true
                    icon: "key"; title: "Nothing selected"; text: "Pick an entry on the left to see its details."
                }
            }
        }
    }

    EntryDialog { id: editor; vault: vault; onSaved: entry => save(entry) }

    ConfirmDialog {
        id: confirmDelete
        title: details.entry ? "Delete “" + details.entry.name + "”?" : ""
        text: "This removes it from the vault and cannot be undone."
        confirmText: "Delete"; danger: true
        onConfirmed: remove(details.entry)
    }
}
