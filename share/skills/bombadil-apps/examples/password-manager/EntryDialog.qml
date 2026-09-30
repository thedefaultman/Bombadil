import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Add or edit one entry: openFor(null) starts a new one, saved(entry) hands back the result.
Dialog {
    id: dialog
    required property var vault          // for generatePassword()
    property var entry: null
    signal saved(var entry)

    function openFor(e) {
        entry = e
        nameField.text = e ? e.name : ""
        userField.text = e ? e.username : ""
        passwordField.text = e ? e.password : ""
        passwordField.revealed = false
        urlField.text = e ? e.url : ""
        notesField.text = e ? e.notes : ""
        open()
        nameField.forceActiveFocus()
    }

    title: entry ? "Edit " + entry.name : "New password"
    width: 520

    Form {
        anchors.fill: parent
        labelWidth: 90
        Field { label: "Name"; required: true; TextField { id: nameField; placeholderText: "GitHub" } }
        Field { label: "Username"; TextField { id: userField; placeholderText: "you@example.com" } }
        Field {
            label: "Password"
            RowLayout {
                spacing: Theme.gapSmall
                PasswordField { id: passwordField; showStrength: true; font: Theme.monoFont; Layout.fillWidth: true }
                Button {
                    Layout.alignment: Qt.AlignTop   // level with the field, not with its strength meter
                    text: "Generate"; icon.source: Theme.icon("wand")
                    onClicked: { passwordField.text = dialog.vault.generatePassword(20, true); passwordField.revealed = true }
                }
            }
        }
        Field { label: "Website"; TextField { id: urlField; placeholderText: "https://github.com" } }
        Field { label: "Notes"; TextArea { id: notesField; placeholderText: "Recovery codes, security questions…" } }
    }

    standardButtons: Dialog.Save | Dialog.Cancel
    // Save stays disabled until the entry has a name.
    Component.onCompleted: standardButton(Dialog.Save).enabled = Qt.binding(() => nameField.text.trim() !== "")

    onAccepted: saved({
        id: entry ? entry.id : Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        name: nameField.text.trim(),
        username: userField.text.trim(),
        password: passwordField.text,
        url: urlField.text.trim(),
        notes: notesField.text,
        changed: Date.now()
    })
}
