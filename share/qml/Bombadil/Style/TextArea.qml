import QtQuick
import QtQuick.Controls.impl
import QtQuick.Templates as T
import Bombadil

// Multi-line input, same look as TextField. Inside a ScrollView the frame stays put while
// the text scrolls.
T.TextArea {
    id: control

    implicitWidth: Math.max(contentWidth + leftPadding + rightPadding,
                            implicitBackgroundWidth + leftInset + rightInset,
                            placeholder.implicitWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(contentHeight + topPadding + bottomPadding,
                             implicitBackgroundHeight + topInset + bottomInset,
                             placeholder.implicitHeight + topPadding + bottomPadding)

    padding: 9
    leftPadding: 12
    rightPadding: 12
    opacity: enabled ? 1 : 0.4

    color: Theme.fg
    selectionColor: Theme.alpha(Theme.accent, 0.45)
    selectedTextColor: Theme.fg
    placeholderTextColor: Theme.faint
    wrapMode: TextEdit.Wrap

    T.ContextMenu.menu: EditMenu { editor: control }

    PlaceholderText {
        id: placeholder
        x: control.leftPadding
        y: control.topPadding
        width: control.width - (control.leftPadding + control.rightPadding)
        height: control.height - (control.topPadding + control.bottomPadding)
        text: control.placeholderText
        font: control.font
        color: control.placeholderTextColor
        verticalAlignment: control.verticalAlignment
        visible: !control.length && !control.preeditText
                 && (!control.activeFocus || control.horizontalAlignment !== Qt.AlignHCenter)
        elide: Text.ElideRight
        renderType: control.renderType
    }

    background: Rectangle {
        implicitWidth: 200
        implicitHeight: 80
        radius: Theme.radiusSmall
        color: Theme.raised
        border.width: control.activeFocus ? 1.5 : 1
        border.color: control.activeFocus ? Theme.accent : control.hovered ? Theme.borderStrong : Theme.border

        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
}
