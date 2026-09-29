import QtQuick
import QtQuick.Layouts

// A column of Fields. With `labelWidth` set, labels sit to the left at that width.
ColumnLayout {
    property int labelWidth: 0

    spacing: Theme.gap
    Layout.fillWidth: true
}
