import QtQuick
import Quickshell.Hyprland

// The keys that give the pill the keyboard, answered here in the bar's own process. hyprland.lua
// binds a tap on Super and Alt+Space to these two shortcuts (hl.dsp.global("bombadil:pill-release")
// and "bombadil:pill-press"), so the key reaches the pill at once. It used to run `bombadil pill`,
// a Python start and a round trip through agentd, and on a busy machine the first letters
// had gone to the window underneath before the pill had the keyboard.
//
// A name says which edge it listens to, and the bind has to give it that edge: a tap fires when Super
// is let go (a bind with release = true), Alt+Space when it goes down. Hyprland drops a shortcut nobody
// has registered without a word, so the names are the same in the lua; tests/test_iso_profile.py
// holds them together. Make this once: the protocol refuses a second registration of the same
// appid and name, and the client that comes second crashes, so it is not made per screen.
QtObject {
    id: keys
    required property var pill   // the PillState to summon

    // (A QtObject has no default property for children: each shortcut is a property.)
    property GlobalShortcut tap: GlobalShortcut {
        appid: "bombadil"
        name: "pill-release"
        description: "Tap Super: the pill takes the keyboard (a second tap gives it back)"
        onReleased: keys.pill.summon("")
    }
    property GlobalShortcut key: GlobalShortcut {
        appid: "bombadil"
        name: "pill-press"
        description: "Alt+Space: the pill takes the keyboard (a second press gives it back)"
        onPressed: keys.pill.summon("")
    }
}
