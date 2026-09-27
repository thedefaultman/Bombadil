import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A TextField for secrets: hidden text, an eye button to reveal it, and an optional
// 4-step strength meter under it.
TextField {
    id: root

    property bool revealed: false
    property bool showStrength: false
    // 0 (empty or very guessable) .. 4 (strong), from length, variety and common patterns.
    readonly property int strength: _estimate(text)

    echoMode: revealed ? TextInput.Normal : TextInput.Password
    passwordCharacter: "•"
    rightPadding: 40
    bottomInset: showStrength ? 10 : 0
    bottomPadding: topPadding + bottomInset
    implicitHeight: implicitBackgroundHeight + topInset + bottomInset
    inputMethodHints: Qt.ImhHiddenText | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
    selectByMouse: true
    Layout.fillWidth: true

    // The most used passwords, which score 0 whatever their length (a deny list, not credentials).
    readonly property var _common: ("123456 12345678 qwerty azerty letmein welcome admin iloveyou monkey "
        + "dragon football baseball master login abc123 111111 000000 sunshine princess trustno1 secret "
        + "shadow summer winter hello freedom password whatever starwars changeme default root test "
        + "hunter batman superman pokemon passw0rd").split(" ")

    function _estimate(pw) {
        if (!pw)
            return 0
        const lower = pw.toLowerCase()
        let pool = 0
        if (/[a-z]/.test(pw)) pool += 26
        if (/[A-Z]/.test(pw)) pool += 26
        if (/[0-9]/.test(pw)) pool += 10
        if (/[^a-zA-Z0-9]/.test(pw)) pool += 33
        let bits = pw.length * Math.log(Math.max(pool, 2)) / Math.LN2
        // Few distinct characters ("aaaa1111") carry less than their length suggests.
        const distinct = new Set(pw).size
        if (distinct < pw.length / 2)
            bits *= 0.6
        if (/(.)\1\1/.test(pw))
            bits -= 8
        if (/(0123|1234|2345|3456|4567|5678|6789|abcd|bcde|cdef|qwer|wert|asdf|sdfg|zxcv)/.test(lower))
            bits -= 12
        if (/(19|20)\d\d/.test(pw))
            bits -= 6
        const stripped = lower.replace(/[^a-z]/g, "")
        if (_common.some(w => lower.includes(w) || stripped === w)) {
            bits -= 20
            if (_common.includes(lower) || _common.includes(stripped))
                return 0
        }
        if (pw.length < 6 || bits < 28)
            return pw.length < 4 ? 0 : 1
        return bits < 40 ? 1 : bits < 60 ? 2 : bits < 80 ? 3 : 4
    }

    IconButton {
        anchors { right: parent.right; rightMargin: 5; verticalCenter: parent.background ? parent.background.verticalCenter : parent.verticalCenter }
        icon: root.revealed ? "eye-off" : "eye"
        size: 28
        tooltip: root.revealed ? "Hide password" : "Show password"
        onClicked: root.revealed = !root.revealed
    }

    Row {
        visible: root.showStrength
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        spacing: 4
        Repeater {
            model: 4
            Rectangle {
                required property int index
                readonly property int level: root.text ? Math.max(1, root.strength) : 0
                width: (parent.width - 12) / 4
                height: 4
                radius: 2
                color: index >= level ? Theme.raised
                     : level <= 1 ? Theme.bad : level === 2 ? Theme.warn : Theme.good
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }
        }
    }
}
