import QtQuick
import QtQuick.Layouts

// Running text. Fills the width of a layout so long text wraps instead of overflowing.
Text {
    color: Theme.fg
    font.family: Theme.fontFamily
    font.pixelSize: Theme.textSize
    wrapMode: Text.Wrap
    Layout.fillWidth: true
}
