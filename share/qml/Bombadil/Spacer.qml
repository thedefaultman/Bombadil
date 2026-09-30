import QtQuick
import QtQuick.Layouts

// Takes the free space along its layout: horizontal in a RowLayout, vertical in a
// ColumnLayout (so a Spacer in a row never makes that row taller).
Item {
    Layout.fillWidth: !(parent instanceof ColumnLayout)
    Layout.fillHeight: !(parent instanceof RowLayout)
}
