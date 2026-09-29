import QtQuick
import QtQuick.Controls.impl

// Right-click menu of text inputs (undo, cut, copy, paste, ...), drawn with this style's Menu.
Menu {
    id: menu

    required property Item editor

    UndoAction { editor: menu.editor }
    RedoAction { editor: menu.editor }
    MenuSeparator {}
    CutAction { editor: menu.editor }
    CopyAction { editor: menu.editor }
    PasteAction { editor: menu.editor }
    DeleteAction { editor: menu.editor }
    MenuSeparator {}
    SelectAllAction { editor: menu.editor }
}
