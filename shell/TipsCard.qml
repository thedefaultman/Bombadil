pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Bombadil as Kit
import "DeskTheme.js" as T

// Tips: what can I ask it to do? One tip at a time, for a new person's first week: a picture of the result,
// one line, and a button that reads exactly what to type. A still card. It changes only when the person
// turns it (the arrows and the wheel) or after a tip was tried (agentd sends the next one), and it never
// plays, loops or turns by itself: the Tips app is where a tip moves. Nothing here runs a tip: the button
// reports the press and the shell puts the words in the pill, where the person's own Enter runs them.
// model is DeskState's tipsModel: {id, line, words, marks, why, index, count, tip}, where `tip` is the whole
// row the picture draws its poster from (Kit.TipScene at card density). A tip with no button (its words only
// make sense mid-turn) draws its marks, which are not buttons, in the button's place.
//
// 300 x 290 for every tip, so the cards above it never jump: the title, the why line, the 272 x 153 picture,
// one line, and a row with the button (or the marks) and "All tips ›". The arrows and the x fade in over
// 120 ms while the pointer is on the card; the wheel turns tips. A turn crossfades the picture, the line and
// the button over 200 ms. Both honour reduced motion through the Theme's durations.
DeskCard {
    id: card
    objectName: "tipsCard"
    namePrefix: "tips"
    property var model: ({})
    // The arrows and the wheel (1 on, -1 back); the title, the picture and "All tips ›" (the app on that
    // tip, playing from the picture); the button (the tip's words, to the pill); the x.
    signal turnRequested(int dir)
    signal openRequested(string id, bool play)
    signal tryRequested(string id)
    signal hideRequested()

    readonly property var m: model || ({})
    readonly property string tipId: m.id || ""
    readonly property int bodyHeight: 222          // the card is 50 + this + 18 = 290
    readonly property int row: 272                 // the card's room for a line or a row: 300 less 14 each side
    readonly property var keys: ["Esc", "Tab", "Enter", "Super"]

    title: "Tips"
    why: m.why || ""
    whyFigures: true
    implicitHeight: 50 + bodyHeight + 18
    onTitleClicked: card.openRequested(card.tipId, false)

    // The picture, the line and the button live on two faces that cross over when the tip changes. The model
    // of the one in front is updated in place when the same tip says something new (its place in the list).
    property var _a: ({})
    property var _b: ({})
    property bool front: true              // face A is in front
    property string _shown: ""
    function _sync() {
        const id = m.id || ""
        if (id === "") return              // nothing to say: it keeps what it last drew while it folds away
        if (_shown === "") { _shown = id; _a = m; front = true; return }
        if (id !== _shown) {
            _shown = id
            if (front) _b = m; else _a = m
            front = !front
        } else if (front) _a = m
        else _b = m
    }
    onMChanged: _sync()
    Component.onCompleted: _sync()

    // One tip's picture, line and button, or its marks.
    component Face: Item {
        id: face
        property var m: ({})
        property bool shown: false
        property real room: 150            // what the bottom row leaves for the button or the marks
        signal pictureTapped()
        signal buttonTapped()

        readonly property string words: m.words || ""
        readonly property var marks: m.marks || []

        width: card.row
        height: card.bodyHeight
        opacity: shown ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Kit.Theme.normal } }

        // The poster: the tip's payoff, still, at card density. A press plays it in the app.
        Item {
            objectName: "tipsPicture"
            y: 8
            width: 272; height: 153
            // Drawn only while the face is on show, so a card that is away (or the face behind) costs nothing.
            Loader {
                active: face.visible
                sourceComponent: Kit.TipScene {
                    tip: face.m.tip || ({})
                    density: "card"
                    reducedMotion: Kit.Theme.reducedMotion
                }
            }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: face.pictureTapped() }
        }

        Text {
            objectName: "tipsLine"
            y: 171
            width: parent.width; height: 18
            font.family: T.fontFamily
            text: face.m.line || ""
            color: T.fg
            font.pixelSize: 13
            textFormat: Text.PlainText
            elide: Text.ElideRight
            maximumLineCount: 1
        }

        // The sentence you would type, as a button. Its words elide when they do not fit beside "All tips ›".
        Rectangle {
            id: button
            objectName: "tipsButton"
            visible: face.words !== ""
            y: 198
            width: Math.min(label.implicitWidth + 26, face.room); height: 28
            radius: 14
            color: tap.pressed ? T.borderStrong : hover.hovered ? T.border : Kit.Theme.glassRaised
            border.width: 1
            border.color: T.borderStrong
            Text {
                id: label
                objectName: "tipsButtonText"
                x: 13
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - 26
                font.family: T.fontFamily
                text: face.words
                color: T.fg
                font.pixelSize: 13
                font.weight: Font.Medium
                textFormat: Text.PlainText
                elide: Text.ElideRight
                maximumLineCount: 1
            }
            HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
            TapHandler { id: tap; gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: face.buttonTapped() }
        }

        // A tip whose words only make sense mid-turn: the keys or words to use, drawn as marks. Nothing here
        // is a button (no raised fill, no pointer, no press).
        Row {
            id: marksRow
            objectName: "tipsMarks"
            visible: face.words === "" && face.marks.length > 0
            y: 200
            spacing: 6
            Repeater {
                model: face.marks
                Rectangle {
                    id: mark
                    required property string modelData
                    objectName: "tipsMark"
                    readonly property bool key: card.keys.indexOf(modelData) >= 0
                    width: Math.min(markText.implicitWidth + 20, Math.max(28, (face.room - 6 * (face.marks.length - 1)) / face.marks.length))
                    height: 24
                    radius: key ? 6 : 12
                    color: "transparent"
                    border.width: 1
                    border.color: T.border
                    Text {
                        id: markText
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, parent.width - 20)
                        font.family: T.fontFamily
                        text: mark.modelData
                        color: T.fg
                        font.pixelSize: 13
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        maximumLineCount: 1
                    }
                }
            }
        }
    }

    Item {
        Layout.fillWidth: true
        implicitHeight: card.bodyHeight

        Face {
            id: faceA
            objectName: "tipsFaceA"
            x: 14
            m: card._a
            shown: card.front
            room: card.row - allTips.implicitWidth - 10
            onPictureTapped: card.openRequested(card.tipId, true)
            onButtonTapped: card.tryRequested(card.tipId)
        }
        Face {
            id: faceB
            objectName: "tipsFaceB"
            x: 14
            m: card._b
            shown: !card.front
            room: card.row - allTips.implicitWidth - 10
            onPictureTapped: card.openRequested(card.tipId, true)
            onButtonTapped: card.tryRequested(card.tipId)
        }

        // The Tips app, on this tip.
        Text {
            id: allTips
            objectName: "tipsAll"
            x: 14 + card.row - implicitWidth
            y: 198 + (28 - implicitHeight) / 2
            font.family: T.fontFamily
            text: "All tips ›"
            color: allHover.hovered ? T.fg : T.muted
            font.pixelSize: 12
            textFormat: Text.PlainText
            HoverHandler { id: allHover; cursorShape: Qt.PointingHandCursor; margin: 6 }
            TapHandler {
                margin: 6
                gesturePolicy: TapHandler.ReleaseWithinBounds
                onTapped: card.openRequested(card.tipId, false)
            }
        }
    }

    // On the whole card, header included: where the pointer is decides whether the arrows and the x show, and
    // the wheel turns tips. (A tip is never turned by a timer: only a press, the wheel, or a tried tip.)
    Item {
        id: over
        objectName: "tipsOver"
        parent: card
        width: card.width; height: card.height

        HoverHandler { id: cardHover; onHoveredChanged: wheel.turned = 0 }
        // One tip for a notch (120); a touchpad's small steps add up to it, and a turn the other way starts over.
        WheelHandler {
            id: wheel
            objectName: "tipsWheel"
            property real turned: 0
            onWheel: event => {
                if (turned * event.angleDelta.y < 0) turned = 0
                turned += event.angleDelta.y
                if (Math.abs(turned) < 120) return
                card.turnRequested(turned < 0 ? 1 : -1)
                turned = 0
            }
        }

        // ‹ › and ×: 24 px, muted, top right, in over 120 ms.
        Row {
            id: controls
            objectName: "tipsControls"
            x: card.width - 8 - width
            y: 8
            opacity: cardHover.hovered ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: Kit.Theme.fast } }

            component Control: Item {
                id: control
                property string icon: ""
                signal activated()
                width: 24; height: 24
                Kit.Icon {
                    anchors.centerIn: parent
                    name: control.icon
                    size: 16
                    color: controlHover.hovered ? T.fg : T.muted
                }
                HoverHandler { id: controlHover; cursorShape: Qt.PointingHandCursor }
                // Takes the press (not just the release), so the title line under the arrows does not open the app.
                TapHandler { gesturePolicy: TapHandler.WithinBounds; onTapped: control.activated() }
            }
            Control { objectName: "tipsPrev"; icon: "chevron-left"; onActivated: card.turnRequested(-1) }
            Control { objectName: "tipsNext"; icon: "chevron-right"; onActivated: card.turnRequested(1) }
            Control { objectName: "tipsHide"; icon: "x"; onActivated: card.hideRequested() }
        }
    }
}
