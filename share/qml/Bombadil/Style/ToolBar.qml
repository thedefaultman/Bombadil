import QtQuick
import QtQuick.Templates as T
import Bombadil

// A 44 px bar for a Page header/footer: transparent, with a hairline on the content side.
T.ToolBar {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 6
    horizontalPadding: 8

    background: Item {
        implicitHeight: Theme.rowHeight
        Rectangle {
            width: parent.width
            height: 1
            y: control.position === T.ToolBar.Footer ? 0 : parent.height - 1
            color: Theme.border
        }
    }
}
