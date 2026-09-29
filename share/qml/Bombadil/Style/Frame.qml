import QtQuick
import QtQuick.Templates as T
import Bombadil

// A hairline-outlined group with no fill.
T.Frame {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: Theme.pad

    background: Rectangle {
        radius: Theme.radius
        color: "transparent"
        border.color: Theme.border
    }
}
