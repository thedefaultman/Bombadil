import QtQuick
import QtQuick.Templates as T
import Bombadil

// A panel-colored card surface.
T.Pane {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: Theme.pad

    background: Rectangle {
        radius: Theme.radius
        color: Theme.panel
        border.color: Theme.border
    }
}
