import QtQuick
import QtQuick.Templates as T
import Bombadil

// Text tabs on a hairline; an accent underline slides to the current tab.
T.TabBar {
    id: control

    readonly property bool footer: position === T.TabBar.Footer
    // Tabs share the bar's width equally, so size the bar for the widest one (no elided tabs).
    readonly property real widestTab: {
        let w = 0
        for (let i = 0; i < count; ++i) {
            const tab = itemAt(i)
            if (tab)
                w = Math.max(w, tab.implicitWidth)
        }
        return w
    }

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            widestTab * count + spacing * Math.max(0, count - 1) + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    spacing: 0
    opacity: enabled ? 1 : 0.4

    contentItem: ListView {
        model: control.contentModel
        currentIndex: control.currentIndex

        spacing: control.spacing
        orientation: ListView.Horizontal
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.AutoFlickIfNeeded
        snapMode: ListView.SnapToItem

        highlightMoveDuration: Theme.normal
        highlightResizeDuration: Theme.normal
        highlightRangeMode: ListView.ApplyRange
        preferredHighlightBegin: 40
        preferredHighlightEnd: width - 40

        highlight: Item {
            z: 2
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                y: control.footer ? 0 : parent.height - height
                height: 2
                radius: 1
                color: Theme.accent
            }
        }
    }

    background: Item {
        implicitHeight: 40
        Rectangle {
            width: parent.width
            height: 1
            y: control.footer ? 0 : parent.height - 1
            color: Theme.border
        }
    }
}
