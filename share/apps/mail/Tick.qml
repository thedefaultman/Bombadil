import QtQuick
import Bombadil

// A tick box in white, not the kit's orange one. `checked` is owned by whoever uses it: it flips only when they
// say so (toggled), so the box can never show what the backend does not hold.
FocusScope {
    id: root

    property string text: ""
    property bool checked: false

    signal toggled(bool checked)

    implicitWidth: row.implicitWidth
    implicitHeight: 28
    activeFocusOnTab: true
    Accessible.role: Accessible.CheckBox
    Accessible.name: text
    Accessible.checked: checked
    Accessible.onToggleAction: root.toggled(!root.checked)

    HoverHandler { id: hover }
    TapHandler { onTapped: root.toggled(!root.checked) }

    Row {
        id: row
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 18
            radius: 5
            color: root.checked ? Theme.fg : "transparent"
            border.width: root.activeFocus ? 2 : 1
            border.color: root.activeFocus || root.checked ? Theme.fg : hover.hovered ? Theme.muted : Theme.borderStrong
            Icon {
                visible: root.checked
                anchors.centerIn: parent
                name: "check"
                size: 13
                color: Theme.bg
            }
        }
        PlainText {
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
        }
    }

    Keys.onSpacePressed: root.toggled(!root.checked)
    Keys.onReturnPressed: root.toggled(!root.checked)
}
