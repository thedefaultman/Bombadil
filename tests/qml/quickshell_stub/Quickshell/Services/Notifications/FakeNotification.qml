import QtQuick

// What shell/Notifications.qml is handed by the real server: the same names, and every call written down in `calls`.
QtObject {
    property int id: 1
    property string appName: ""
    property string appIcon: ""
    property string summary: ""
    property string body: ""
    property int urgency: 1
    property bool tracked: false
    property var actions: []
    property var calls: []
    signal closed(int reason)
    function dismiss() { calls = calls.concat(["dismiss"]); closed(2) }
    function expire() { calls = calls.concat(["expire"]); closed(1) }
}
