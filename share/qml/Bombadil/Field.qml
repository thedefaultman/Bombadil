import QtQuick
import QtQuick.Layouts
import QtQuick.Templates as T

// A label, one control (the child, stretched to the field's width) and a hint or error.
// Inside a Form with `labelWidth`, the label sits to the left of the control.
GridLayout {
    id: root

    property string label
    property string hint
    property string error
    property bool required: false
    default property alias content: slot.data

    readonly property int _labelWidth: parent && parent.labelWidth !== undefined ? parent.labelWidth : 0
    readonly property bool _inline: _labelWidth > 0

    Layout.fillWidth: true
    columns: _inline ? 2 : 1
    columnSpacing: Theme.gap
    rowSpacing: Theme.gapSmall

    Row {
        visible: root.label !== ""
        Layout.preferredWidth: root._inline ? root._labelWidth : -1
        Layout.fillWidth: !root._inline
        Layout.minimumHeight: root._inline ? Theme.controlHeight : 0
        Layout.alignment: Qt.AlignTop | Qt.AlignLeft
        spacing: 6
        Text {
            id: labelText
            anchors.verticalCenter: root._inline ? parent.verticalCenter : undefined
            width: Math.min(implicitWidth, (root._inline ? root._labelWidth : root.width) - (dot.visible ? 12 : 0))
            text: root.label
            color: Theme.fg
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
            font.weight: Font.Medium
            elide: Text.ElideRight
        }
        Rectangle {
            id: dot
            visible: root.required
            anchors.verticalCenter: labelText.verticalCenter
            width: 6
            height: 6
            radius: 3
            color: Theme.accent
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.gapSmall

        ColumnLayout {
            id: slot
            Layout.fillWidth: true
            spacing: Theme.gapSmall
            // Inputs stretch to the field's width; buttons, checkboxes and switches keep theirs.
            onChildrenChanged: {
                for (const c of children)
                    if (!(c instanceof T.AbstractButton))
                        c.Layout.fillWidth = true
            }
        }
        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: root.error !== "" ? root.error : root.hint
            color: root.error !== "" ? Theme.bad : Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.captionSize
            wrapMode: Text.Wrap
        }
    }
}
