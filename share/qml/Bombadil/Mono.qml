import QtQuick
import QtQuick.Layouts

// Code, paths, ids and hashes; read-only but selectable so they can be copied.
TextEdit {
    color: Theme.fg
    font: Theme.monoFont
    readOnly: true
    selectByMouse: true
    textFormat: TextEdit.PlainText
    wrapMode: TextEdit.WrapAtWordBoundaryOrAnywhere
    selectionColor: Theme.accentSoft
    selectedTextColor: Theme.fg
    activeFocusOnPress: true
    Layout.fillWidth: true
}
