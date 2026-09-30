import QtQuick
import QtQuick.Templates as T
import Bombadil

// Plain text in the OS font and ink; links in accent.
T.Label {
    color: Theme.fg
    linkColor: Theme.accent
    font: Theme.font
}
