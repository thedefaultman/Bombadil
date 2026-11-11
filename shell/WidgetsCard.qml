import QtQuick
import QtQuick.Layouts
import Bombadil as Kit

// The card the word "widgets" opens above the pill, in the AI card's shape: a title, one row for each
// widget with its name, the question it answers and a three-part control (Keep, When needed, Off), and
// "Reset the desk". The rows are agentd's, and so is what each part may be: the raised part follows the
// desk, never the press, and a part the widget cannot take is dimmed and does nothing. Nothing here is
// orange: orange is the machine acting, and this is you choosing. Esc, a click elsewhere or typing puts
// the card away (shell.qml, which also keeps it open only while the pill holds the keyboard); so does
// the small x.
Rectangle {
    id: card
    objectName: "widgetsCard"
    required property var desk       // a DeskState
    property bool active: true       // the bar of the screen that holds the keyboard; the others draw nothing

    readonly property bool shown: active && desk.widgetsOpen && desk.widgetList.length > 0

    implicitWidth: 360
    implicitHeight: shown ? body.implicitHeight + 24 : 0
    radius: Kit.Theme.radiusLine
    color: Kit.Theme.glassLine
    border.width: 1
    border.color: Kit.Theme.border
    opacity: shown ? 1 : 0
    visible: opacity > 0
    clip: true
    Behavior on opacity { NumberAnimation { duration: Kit.Theme.normal } }
    Behavior on implicitHeight { NumberAnimation { duration: Kit.Theme.fast; easing.type: Easing.OutCubic } }

    ColumnLayout {
        id: body
        // A card on its way out takes no press: it is already gone as far as the desk is concerned.
        enabled: card.shown
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12; leftMargin: 18; rightMargin: 12 }
        spacing: 4

        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "widgetsTitle"
                font.family: Kit.Theme.fontFamily
                Layout.fillWidth: true
                text: "Widgets"
                color: Kit.Theme.fg
                font.pixelSize: Kit.Theme.textSize
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            Item {
                objectName: "widgetsClose"
                implicitWidth: 24
                implicitHeight: 24
                Text {
                    font.family: Kit.Theme.fontFamily
                    anchors.centerIn: parent
                    text: "×"
                    color: closeHover.hovered ? Kit.Theme.fg : Kit.Theme.muted
                    font.pixelSize: 15
                    textFormat: Text.PlainText
                }
                HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onTapped: card.desk.closeWidgets()
                }
            }
        }

        // By position, so a row that agentd sends again is the same row and nothing is built again.
        Repeater {
            model: card.desk.widgetList.length
            Item {
                id: row
                required property int index
                readonly property var spec: card.desk.widgetList[index] || ({})
                readonly property string wid: spec.id || ""
                readonly property string current: card.desk.stateOf(wid)
                objectName: "widgetsRow-" + wid
                Layout.fillWidth: true
                implicitHeight: 56

                // The name and the control on one line, the question under them at the row's full width.
                RowLayout {
                    y: 4
                    width: parent.width
                    height: 24
                    spacing: 8
                    Text {
                        objectName: "widgetsName"
                        font.family: Kit.Theme.fontFamily
                        Layout.fillWidth: true
                        text: row.spec.title || ""
                        color: Kit.Theme.fg
                        font.pixelSize: Kit.Theme.smallSize
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        maximumLineCount: 1
                    }
                    Rectangle {
                        id: control
                        objectName: "widgetsControl-" + row.wid
                        implicitWidth: parts.implicitWidth + 2
                        implicitHeight: 24
                        radius: height / 2
                        color: "transparent"
                        border.width: 1
                        border.color: Kit.Theme.border

                        Row {
                            id: parts
                            x: 1
                            y: 1
                            Repeater {
                                model: ["keep", "needed", "off"]
                                Rectangle {
                                    id: part
                                    required property string modelData
                                    objectName: "widgetsPart-" + row.wid + "-" + modelData
                                    readonly property bool current: row.current === modelData
                                    readonly property bool allowed: (row.spec.can || []).indexOf(modelData) >= 0
                                    // The one that is already so, and one the widget cannot be, have nothing to press.
                                    readonly property bool tappable: allowed && !current
                                    width: label.implicitWidth + 20
                                    height: 22
                                    radius: height / 2
                                    color: current ? Kit.Theme.raised : "transparent"

                                    Text {
                                        id: label
                                        font.family: Kit.Theme.fontFamily
                                        anchors.centerIn: parent
                                        text: part.modelData === "keep" ? "Keep" : part.modelData === "needed" ? "When needed" : "Off"
                                        color: !part.allowed ? Kit.Theme.faint
                                             : part.current || hover.hovered ? Kit.Theme.fg : Kit.Theme.muted
                                        font.pixelSize: Kit.Theme.captionSize
                                        textFormat: Text.PlainText
                                    }
                                    HoverHandler { id: hover; enabled: part.tappable; cursorShape: Qt.PointingHandCursor }
                                    TapHandler {
                                        // What was under the finger at the press: a desk message may reorder the rows before
                                        // the release, and the press was for this widget and this state.
                                        property string armedId: ""
                                        property string armedState: ""
                                        enabled: part.tappable
                                        gesturePolicy: TapHandler.ReleaseWithinBounds
                                        onPressedChanged: if (pressed) {
                                            armedId = row.wid
                                            armedState = part.modelData
                                        }
                                        onTapped: card.desk.setWidget(armedId, armedState)
                                    }
                                }
                            }
                        }
                    }
                }
                Text {
                    objectName: "widgetsQuestion"
                    font.family: Kit.Theme.fontFamily
                    y: 32
                    width: parent.width
                    text: row.spec.question || ""
                    color: Kit.Theme.muted
                    font.pixelSize: Kit.Theme.captionSize
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }
            }
        }

        Item {
            objectName: "widgetsReset"
            Layout.alignment: Qt.AlignLeft
            implicitWidth: resetLabel.implicitWidth
            implicitHeight: 28
            Text {
                id: resetLabel
                font.family: Kit.Theme.fontFamily
                anchors.verticalCenter: parent.verticalCenter
                text: "Reset the desk"
                color: resetHover.hovered ? Kit.Theme.fg : Kit.Theme.muted
                font.pixelSize: Kit.Theme.captionSize
                textFormat: Text.PlainText
            }
            HoverHandler { id: resetHover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                gesturePolicy: TapHandler.ReleaseWithinBounds
                onTapped: card.desk.resetDesk()
            }
        }
    }
}
