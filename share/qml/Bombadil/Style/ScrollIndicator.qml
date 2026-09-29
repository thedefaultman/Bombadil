import QtQuick
import QtQuick.Templates as T
import Bombadil

// Non-interactive 3 px scroll position hint that shows only while moving.
T.ScrollIndicator {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 2

    contentItem: Rectangle {
        implicitWidth: 3
        implicitHeight: 3
        radius: 1.5
        color: Theme.borderStrong
        visible: control.size < 1.0
        opacity: 0

        states: State {
            name: "active"
            when: control.active
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
