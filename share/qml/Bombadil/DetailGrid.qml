import QtQuick
import QtQuick.Layouts

// Label/value pairs in two aligned columns:
// rows: [{ label: "PID", value: "1234", mono: true, copyable: true, secret: false }, ...]
GridLayout {
    id: root

    property var rows: []

    columns: 2
    columnSpacing: Theme.pad
    rowSpacing: 10

    function _show(v) {
        return v === null || v === undefined || v === "" ? "—" : String(v)
    }

    Repeater {
        model: root.rows
        delegate: Text {
            required property var modelData
            required property int index
            Layout.row: index
            Layout.column: 0
            Layout.alignment: Qt.AlignTop | Qt.AlignLeft
            Layout.topMargin: modelData.copyable ? 4 : 0
            text: modelData.label || ""
            color: Theme.muted
            font.family: Theme.fontFamily
            font.pixelSize: Theme.textSize
        }
    }

    Repeater {
        model: root.rows
        delegate: RowLayout {
            id: cell
            required property var modelData
            required property int index
            property bool copied: false
            Layout.row: index
            Layout.column: 1
            Layout.fillWidth: true
            spacing: Theme.gapSmall

            TextEdit {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                text: root._show(cell.modelData.value)
                color: Theme.fg
                font: cell.modelData.mono ? Theme.monoFont : Theme.font
                readOnly: true
                selectByMouse: true
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.WrapAtWordBoundaryOrAnywhere
                selectionColor: Theme.accentSoft
                selectedTextColor: Theme.fg
            }
            IconButton {
                visible: !!cell.modelData.copyable
                Layout.alignment: Qt.AlignTop
                size: 24
                icon: cell.copied ? "check" : "copy"
                tone: cell.copied ? "good" : ""
                tooltip: cell.copied ? "Copied" : "Copy"
                onClicked: {
                    Clipboard.copy(root._show(cell.modelData.value), cell.modelData.secret ? 30 : 0)
                    cell.copied = true
                    copiedTimer.restart()
                }
                Timer {
                    id: copiedTimer
                    interval: 1500
                    onTriggered: cell.copied = false
                }
            }
        }
    }
}
