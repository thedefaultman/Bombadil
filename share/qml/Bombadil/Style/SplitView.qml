import QtQuick
import QtQuick.Templates as T
import Bombadil

// Panes split by a 1 px hairline with a wider invisible grab area; accent while dragged.
T.SplitView {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    handle: Rectangle {
        id: handle

        readonly property bool horizontal: control.orientation === Qt.Horizontal

        implicitWidth: horizontal ? 1 : control.width
        implicitHeight: horizontal ? control.height : 1
        color: T.SplitHandle.pressed ? Theme.accent
             : T.SplitHandle.hovered ? Theme.borderStrong : Theme.border

        containmentMask: Item {
            x: handle.horizontal ? (handle.width - width) / 2 : 0
            y: handle.horizontal ? 0 : (handle.height - height) / 2
            width: handle.horizontal ? 9 : handle.width
            height: handle.horizontal ? handle.height : 9
        }
    }
}
