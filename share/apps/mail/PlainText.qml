import QtQuick
import Bombadil

// Text, and only text: no markup, no links. Everything that came out of a mail, or that the service said about
// one (a name, a subject, a note), is drawn with this and never with a Text whose format is left to guess,
// which would draw "<b>" or "<a href>" in a stranger's subject as markup.
Text {
    textFormat: Text.PlainText
    color: Theme.fg
    font.family: Theme.fontFamily
    font.pixelSize: Theme.textSize
}
