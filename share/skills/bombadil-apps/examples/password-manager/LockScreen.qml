import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// The gate in front of a Vault: the first run creates it, later runs unlock it.
ColumnLayout {
    id: root
    required property var vault
    signal unlocked()
    readonly property bool ready: vault.exists ? master.text !== "" : master.text.length >= 8 && confirm.text === master.text

    function submit() {
        if (!ready) return
        const ok = vault.exists ? vault.unlock(master.text) : vault.create(master.text)
        if (!ok) { master.selectAll(); return }
        master.text = ""; confirm.text = ""
        master.revealed = false; confirm.revealed = false   // the next lock starts hidden again
        unlocked()
    }

    spacing: Theme.gap
    Spacer {}
    EmptyState {
        Layout.fillWidth: true
        icon: root.vault.exists ? "lock" : "shield"
        title: root.vault.exists ? "Passwords are locked" : "Create your vault"
        text: root.vault.exists ? "Enter your master password to unlock."
                                : "Choose a master password. It encrypts everything you save here and cannot be recovered, so make it one you will remember."
    }
    Form {
        Layout.preferredWidth: 320; Layout.fillWidth: false; Layout.alignment: Qt.AlignHCenter
        Field {
            label: "Master password"
            hint: root.vault.exists ? "" : "At least 8 characters"
            error: root.vault.error === "wrong password" ? "Wrong password, try again" : ""
            PasswordField {
                id: master; focus: true; showStrength: !root.vault.exists
                onAccepted: root.vault.exists ? root.submit() : confirm.forceActiveFocus()
            }
        }
        Field {
            visible: !root.vault.exists
            label: "Confirm password"
            error: confirm.text && confirm.text !== master.text ? "Passwords don't match" : ""
            PasswordField { id: confirm; onAccepted: root.submit() }
        }
        Button {
            Layout.fillWidth: true
            text: root.vault.exists ? "Unlock" : "Create vault"
            icon.source: Theme.icon(root.vault.exists ? "unlock" : "shield")
            highlighted: true
            enabled: root.ready
            onClicked: root.submit()
        }
    }
    Spacer {}
}
