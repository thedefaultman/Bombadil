import QtQuick
import QtQuick.Layouts
import "DeskTheme.js" as T

// The chrome every card on the desk shares: the panel, a title with one line of why under it, an
// optional edge bar in the StatusLine's colours and a column for the card's own rows. A card is
// 300 wide and as tall as its rows; where it sits, and when it folds, is the rail's business.
// Plain Qt Quick, so it loads offscreen like PillState.
Item {
    id: card
    property string title: ""
    property string why: ""
    // "" none, else machine, ok, amber or red: the bar inside the left edge
    property string edge: ""
    // The objectNames of the title and why lines start with this ("nowTitle"), so a card that
    // wraps this one can be told apart from another.
    property string namePrefix: "deskCard"
    default property alias content: body.data
    signal titleClicked()

    readonly property color edgeColor: edge === "machine" ? T.machine
                                     : edge === "ok" ? T.ok
                                     : edge === "amber" ? T.amber
                                     : edge === "red" ? T.red
                                     : "transparent"
    // Room for the edge bar: title and why start 6 px further in
    readonly property real textLeft: edge !== "" ? 20 : 14
    readonly property real washLevel: washBar.opacity
    property color washColor: "transparent"

    // A row arrived: its colour washes over the left edge and fades. Never a bounce.
    function wash(colour) {
        washColor = colour
        washFade.restart()
    }

    implicitWidth: T.cardWidth
    implicitHeight: 50 + body.implicitHeight + 18
    width: implicitWidth
    height: implicitHeight

    // A ring around a dot that breathes: the machine's turn, a session working.
    component Ring: Rectangle {
        id: ring
        property real r: 8
        property color tone: T.machine
        property bool live: true
        width: 2 * r; height: 2 * r; radius: r
        color: "transparent"
        border.width: 1.5
        border.color: tone
        visible: live
        ParallelAnimation {
            running: ring.live
            loops: Animation.Infinite
            NumberAnimation { target: ring; property: "scale"; from: 0.7; to: 1.7; duration: 2000; easing.type: Easing.OutQuad }
            NumberAnimation { target: ring; property: "opacity"; from: 0.95; to: 0; duration: 2000; easing.type: Easing.OutQuad }
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: T.radius
        color: T.panel
        border.width: 1
        border.color: T.border
    }

    Rectangle {
        id: washBar
        objectName: card.namePrefix + "Wash"
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
        width: 64
        radius: T.radius
        opacity: 0
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Qt.alpha(card.washColor, 0.4) }
            GradientStop { position: 1; color: Qt.alpha(card.washColor, 0) }
        }
    }
    NumberAnimation {
        id: washFade
        target: washBar
        property: "opacity"
        from: 1; to: 0
        duration: T.washMs
        easing.type: Easing.OutQuad
    }

    Rectangle {
        // Inset, so it stays inside the rounded corners.
        objectName: card.namePrefix + "Edge"
        x: 8; y: 12
        width: 3; height: card.height - 24
        radius: 1.5
        color: card.edgeColor
        visible: card.edge !== ""
    }

    Text {
        id: titleLabel
        objectName: card.namePrefix + "Title"
        x: card.textLeft
        y: Math.round(26 - baselineOffset)
        width: card.width - x - 14
        text: card.title
        color: T.fg
        font.pixelSize: 14
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
        elide: Text.ElideRight
        maximumLineCount: 1
        // A tap opens the thing behind the card.
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: card.titleClicked() }
    }

    Text {
        objectName: card.namePrefix + "Why"
        x: card.textLeft
        y: Math.round(44 - baselineOffset)
        width: card.width - x - 14
        text: card.why
        color: T.muted
        font.pixelSize: 12
        textFormat: Text.PlainText
        elide: Text.ElideRight
        maximumLineCount: 1
    }

    ColumnLayout {
        id: body
        y: 50
        width: parent.width
        spacing: 0
    }
}
