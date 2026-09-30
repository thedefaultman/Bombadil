import QtQuick
import QtQuick.Templates as T
import Bombadil

// Hairline between groups of menu items.
T.MenuSeparator {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    horizontalPadding: 6
    verticalPadding: 4

    contentItem: Rectangle {
        implicitWidth: 180
        implicitHeight: 1
        color: Theme.borderStrong
    }
}
