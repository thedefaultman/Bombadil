import QtQuick
import Bombadil

// The body of a mail: plain text you can select and copy, never rich text and never a link that runs.
TextEdit {
    readOnly: true
    selectByMouse: true
    persistentSelection: false
    textFormat: TextEdit.PlainText
    wrapMode: TextEdit.Wrap
    color: Theme.fg
    // The selection is a light wash of the ink: the accent in this window belongs to Send.
    selectionColor: Theme.alpha(Theme.fg, 0.28)
    selectedTextColor: Theme.fg
    font.family: Theme.fontFamily
    font.pixelSize: Theme.textSize
}
