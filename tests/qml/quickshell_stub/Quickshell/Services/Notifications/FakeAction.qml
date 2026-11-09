import QtQuick

// One action of a fake notification: invoking it is written down on the notification.
QtObject {
    property string identifier: ""
    property string text: ""
    property var owner: null
    function invoke() { owner.calls = owner.calls.concat(["invoke:" + identifier]) }
}
