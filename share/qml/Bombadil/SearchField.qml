import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A TextField for filtering: search icon, clear button, Ctrl+F focuses it and Esc
// clears it. Filter your model with its `text`.
TextField {
    id: root

    placeholderText: "Search"
    leftPadding: 36
    rightPadding: text ? 36 : 12
    selectByMouse: true
    Layout.fillWidth: true

    Icon {
        anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
        name: "search"
        size: 16
        color: root.activeFocus ? Theme.fg : Theme.muted
    }

    IconButton {
        anchors { right: parent.right; rightMargin: 5; verticalCenter: parent.verticalCenter }
        visible: root.text !== ""
        icon: "x"
        size: 26
        tooltip: "Clear"
        onClicked: {
            root.clear()
            root.forceActiveFocus()
        }
    }

    Keys.onShortcutOverride: event => event.accepted = event.key === Qt.Key_Escape && root.text !== ""
    Keys.onEscapePressed: event => {
        if (root.text === "") {
            event.accepted = false
            return
        }
        root.clear()
    }

    Shortcut {
        sequence: "Ctrl+F"
        context: Qt.WindowShortcut
        onActivated: {
            root.forceActiveFocus()
            root.selectAll()
        }
    }
}
