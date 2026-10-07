import QtQuick
import QtQuick.Layouts
import Bombadil

// A one-line field in the window's own neutral colours. Where the keyboard is shows as a lighter outline and never
// as the accent: in this window the accent is the ring on Send and nothing else.
FocusScope {
    id: root

    property alias text: input.text
    property alias readOnly: input.readOnly
    property alias field: input
    property string placeholder: ""
    property string label: ""
    property bool search: false        // a magnifier at the left and a clear button at the right

    signal edited()                    // the person typed, pasted or cleared it (not a text set from outside)
    signal accepted()
    signal downPressed()

    function clear() {
        input.clear()
        root.edited()
    }

    implicitWidth: 220
    implicitHeight: Theme.controlHeight
    activeFocusOnTab: true
    Layout.fillWidth: true
    Accessible.role: Accessible.EditableText
    Accessible.name: root.label || root.placeholder

    HoverHandler { id: hover }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: Theme.raised
        border.width: input.activeFocus ? 1.5 : 1
        border.color: input.activeFocus ? Theme.borderActive : hover.hovered ? Theme.borderStrong : Theme.border
        opacity: root.enabled ? 1 : 0.5
    }

    Icon {
        visible: root.search
        anchors { left: parent.left; leftMargin: 12; verticalCenter: parent.verticalCenter }
        name: "search"
        size: 16
        color: input.activeFocus ? Theme.fg : Theme.muted
    }

    TextInput {
        id: input
        anchors {
            left: parent.left; leftMargin: root.search ? 36 : 12
            right: parent.right; rightMargin: root.search && text !== "" ? 36 : 12
            verticalCenter: parent.verticalCenter
        }
        focus: true
        clip: true
        color: Theme.fg
        selectionColor: Theme.alpha(Theme.fg, 0.28)
        selectedTextColor: Theme.fg
        selectByMouse: true
        font.family: Theme.fontFamily
        font.pixelSize: Theme.textSize
        enabled: root.enabled
        Accessible.name: root.label || root.placeholder
        onTextEdited: root.edited()
        onAccepted: root.accepted()
        Keys.onDownPressed: root.downPressed()
        // Esc empties a search that has words in it; with nothing in it Esc goes on to whoever is next.
        Keys.onShortcutOverride: event => event.accepted = event.key === Qt.Key_Escape && root.search && text !== ""
        Keys.onEscapePressed: event => {
            if (root.search && text !== "")
                root.clear()
            else
                event.accepted = false
        }
    }

    PlainText {
        visible: input.text === "" && input.preeditText === ""
        anchors { left: input.left; right: input.right; verticalCenter: parent.verticalCenter }
        text: root.placeholder
        color: Theme.faint
        elide: Text.ElideRight
    }

    IconButton {
        visible: root.search && input.text !== ""
        anchors { right: parent.right; rightMargin: 5; verticalCenter: parent.verticalCenter }
        icon: "x"
        size: 26
        tooltip: "Clear"
        onClicked: {
            root.clear()
            input.forceActiveFocus()
        }
    }
}
