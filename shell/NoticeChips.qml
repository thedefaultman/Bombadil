import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The parts of a notice's line that are not its words: the chips (the primary one is orange, a quiet
// one is plain), how many other notices wait behind this one, and the cross that puts it away. The
// line places two of these: the tail (count and cross) always at the end of the words, and the chips
// there too when the line is wide, or under the words when it is narrow (a window shares the stage).
//
// A chip sends its own notice's id with its own, and the cross the id of the notice that was on the
// line when the press began, so a newer notice that takes the line under the pointer is never handed
// a press meant for the one before. A notice agentd has already let go of keeps its line while it is
// read, but has no chips.
RowLayout {
    id: tail
    required property var pill
    required property var notice    // the notice on the line (null while none)
    property bool withChips: true
    property bool withTail: true

    spacing: 8

    Repeater {
        model: tail.withChips && tail.notice ? tail.notice.actions : []
        Rectangle {
            id: chip
            required property var modelData
            readonly property bool primary: modelData.style === "primary"
            objectName: "noticeChip"
            implicitWidth: label.implicitWidth + 28
            implicitHeight: Kit.Theme.chipHeight
            radius: implicitHeight / 2
            color: tap.pressed ? Kit.Theme.borderStrong : hover.hovered ? Kit.Theme.overlay : (primary ? Kit.Theme.glassRaised : Kit.Theme.glassChip)
            border.width: 1
            border.color: primary ? (hover.hovered ? Kit.Theme.accentHover : Kit.Theme.accent) : Kit.Theme.border
            Behavior on border.color { ColorAnimation { duration: Kit.Theme.fast } }

            Text {
                id: label
                anchors.centerIn: parent
                text: chip.modelData.label
                color: chip.primary ? Kit.Theme.fg : Kit.Theme.muted
                font.pixelSize: Kit.Theme.smallSize
                font.family: Kit.Theme.fontFamily
                textFormat: Text.PlainText
            }
            HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                id: tap
                gesturePolicy: TapHandler.ReleaseWithinBounds
                onTapped: tail.pill.noticeAction(chip.modelData.notice, chip.modelData.id)
            }
        }
    }

    Text {
        objectName: "noticeMore"
        visible: tail.withTail && tail.pill.notices.length > 1
        text: "+" + (tail.pill.notices.length - 1)
        color: Kit.Theme.muted
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.captionSize
        font.features: { "tnum": 1 }
    }

    Text {
        objectName: "noticeDismiss"
        visible: tail.withTail
        text: "×"
        color: away.hovered ? Kit.Theme.fg : Kit.Theme.muted
        font.family: Kit.Theme.fontFamily
        font.pixelSize: Kit.Theme.lineSize
        HoverHandler { id: away; cursorShape: Qt.PointingHandCursor; margin: 6 }
        TapHandler {
            // The notice that was on the line when the press began is the one it puts away.
            property int target: -1
            margin: 6
            gesturePolicy: TapHandler.ReleaseWithinBounds
            onPressedChanged: if (pressed) target = tail.notice ? tail.notice.id : -1
            onTapped: if (tail.notice && tail.notice.id === target) tail.pill.dismissNotice(target)
        }
    }
}
