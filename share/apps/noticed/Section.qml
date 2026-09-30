import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Bombadil

// One list of the window: a card with its title, hidden while it has nothing, unless everything is
// empty, when each says so in one sentence.
Panel {
    id: root

    property int count: 0
    property bool everythingEmpty: false
    property string emptyText: ""
    property string hint: ""          // the line under the title, while there is something to say it about

    Layout.fillWidth: true
    visible: count > 0 || (everythingEmpty && emptyText !== "")
    spacing: Theme.gapSmall
    subtitle: count > 0 ? hint : ""

    Caption {
        objectName: "empty"
        visible: root.count === 0
        text: root.emptyText
        textFormat: Text.PlainText
    }
}
