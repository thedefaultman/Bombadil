import QtQuick
import QtQuick.Templates as T
import Bombadil

// Side sheet on the panel surface with a hairline on its inner edge, over a dim backdrop.
T.Drawer {
    id: control

    parent: T.Overlay.overlay

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    topPadding: edge === Qt.BottomEdge ? 1 : 0
    leftPadding: edge === Qt.RightEdge ? 1 : 0
    rightPadding: edge === Qt.LeftEdge ? 1 : 0
    bottomPadding: edge === Qt.TopEdge ? 1 : 0

    enter: Transition { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
    exit: Transition { NumberAnimation { duration: Theme.normal; easing.type: Easing.InCubic } }

    background: Rectangle {
        color: Theme.panel

        Rectangle {
            readonly property bool horizontal: control.edge === Qt.LeftEdge || control.edge === Qt.RightEdge
            width: horizontal ? 1 : parent.width
            height: horizontal ? parent.height : 1
            x: control.edge === Qt.LeftEdge ? parent.width - 1 : 0
            y: control.edge === Qt.TopEdge ? parent.height - 1 : 0
            color: Theme.border
        }
    }

    T.Overlay.modal: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.6)
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }
    T.Overlay.modeless: Rectangle {
        color: Theme.alpha(Theme.sunken, 0.25)
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }
}
