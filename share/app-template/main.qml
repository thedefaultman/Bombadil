import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// Starter app: a list the user adds to, saved across runs and hot reloads.
// Keep the four imports and the AppWindow root; replace the rest.
// If the app has an app.py, its Backend is `backend` here.
AppWindow {
    id: win
    title: "Hello"
    icon: "list"
    width: 560
    height: 520

    Store {
        id: store
        property var items: []
    }

    function add() {
        const text = input.text.trim()
        if (text === "")
            return
        // Assign a new array: that is what Store sees and saves.
        store.items = store.items.concat([{ title: text, subtitle: Fmt.dateTime(new Date()) }])
        input.text = ""
        win.toast("Added", "good")
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.gapSmall
        TextField {
            id: input
            Layout.fillWidth: true
            placeholderText: "Add an item"
            onAccepted: win.add()
        }
        Button {
            text: "Add"
            icon.source: Theme.icon("plus")
            highlighted: true
            enabled: input.text.trim() !== ""
            onClicked: win.add()
        }
    }

    ItemList {
        Layout.fillWidth: true
        Layout.fillHeight: true
        model: store.items
        emptyText: "Nothing here yet"
    }
}
