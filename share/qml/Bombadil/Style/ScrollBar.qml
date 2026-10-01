import QtQuick
import QtQuick.Templates as T
import Bombadil

// Thin rounded thumb that fades in while scrolling or hovered and fades out after.
T.ScrollBar {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 2
    visible: policy !== T.ScrollBar.AlwaysOff
    minimumSize: orientation === Qt.Horizontal ? height / width : width / height

    contentItem: Rectangle {
        implicitWidth: control.interactive ? 6 : 3
        implicitHeight: control.interactive ? 6 : 3
        radius: Math.min(width, height) / 2
        color: control.pressed ? Theme.muted : control.hovered ? Theme.faint : Theme.borderStrong
        opacity: 0

        Behavior on color { ColorAnimation { duration: Theme.fast } }

        states: State {
            name: "active"
            when: control.policy === T.ScrollBar.AlwaysOn || (control.active && control.size < 1.0)
            PropertyChanges { control.contentItem.opacity: 1 }
        }
        transitions: [
            Transition {
                to: "active"
                NumberAnimation { property: "opacity"; duration: Theme.fast }
            },
            Transition {
                from: "active"
                SequentialAnimation {
                    PauseAnimation { duration: 600 }
                    NumberAnimation { property: "opacity"; to: 0; duration: Theme.normal }
                }
            }
        ]
    }
}
