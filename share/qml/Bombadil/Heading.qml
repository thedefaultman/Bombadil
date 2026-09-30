import QtQuick

// A title inside the content: level 1 for a screen, 2 for a section, 3 for a group.
Text {
    property int level: 1

    color: Theme.fg
    font.family: Theme.fontFamily
    font.pixelSize: level <= 1 ? Theme.titleSize : level === 2 ? Theme.headingSize : Theme.textSize
    font.weight: level <= 1 ? Font.Bold : Font.DemiBold
    elide: Text.ElideRight
}
