import QtQuick
import QtQuick.Templates as T
import Bombadil

// Right-aligned row of dialog buttons, affirmative last (GNOME order). The affirmative
// standard button (Ok, Save, Yes, Open, Apply) is the default one, which is highlighted.
T.DialogButtonBox {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)
    contentWidth: (contentItem as ListView)?.contentWidth ?? 0

    spacing: 8
    padding: 20
    topPadding: 0
    alignment: Qt.AlignRight
    buttonLayout: T.DialogButtonBox.GnomeLayout

    defaultStandardButton: {
        const preferred = [T.DialogButtonBox.Ok, T.DialogButtonBox.Save, T.DialogButtonBox.Yes,
                           T.DialogButtonBox.Open, T.DialogButtonBox.Apply]
        for (const b of preferred) {
            if (standardButtons & b)
                return b
        }
        return T.DialogButtonBox.NoButton
    }

    delegate: Button {}

    contentItem: ListView {
        implicitWidth: contentWidth
        implicitHeight: Theme.controlHeight
        model: control.contentModel
        spacing: control.spacing
        orientation: ListView.Horizontal
        boundsBehavior: Flickable.StopAtBounds
        snapMode: ListView.SnapToItem
    }

    background: Item {}
}
