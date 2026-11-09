"""Tips written for the tests of the scene renderer (tests/test_tip_scene_qml.py, tests/test_tip_scene.py).

The renderer is built and tested against these, not against the twenty seed tips (whose beats are in
share/tips/tips.toml and are held by tests/test_tip_truth.py and tests/test_tips.py), so a seed tip's wording
can change without a renderer test failing. Between them they use every beat kind, every key and every
modifier of the vocabulary (docs/TIP-SCENES.md).
"""

TICKETS = [["Printer jams on floor 3", "High"], ["VPN drops every hour", "Open"], ["Order a new laptop", "Done"]]

# A build: the key, the words, the stone working with a counter, a window sliding in, the closing reply.
BUILD = {
    "id": "fixture-build", "line": "Say what you need. It gets made.", "words": "Make me a ticketing app", "marks": [],
    "captions": ["You ask", "It builds", "You have it"],
    "beats": [
        {"key": "super"},
        {"type": "Make me a ticketing app"},
        {"key": "enter", "phase": 1},
        {"stone": "working", "line": "On it", "tag": "template", "counter": True},
        {"line": "Building Tickets, 140 lines", "tag": "template"},
        {"window": "Tickets", "tag": "model", "rows": TICKETS, "phase": 2},
        {"line": "Made Tickets. It is open now.", "tag": "model", "buttons": ["Undo", "Details"], "stone": "done"},
    ],
}

# A picture drawn at once, with no AI turn: boxes stream in and one is lit.
PICTURE = {
    "id": "fixture-picture", "line": "Ask for a picture, not a paragraph.", "words": "How am I connected?", "marks": [],
    "captions": ["You ask", "It draws", "You see it"],
    "beats": [
        {"key": "super"},
        {"type": "How am I connected?"},
        {"key": "enter"},
        {"line": "Drawing how you're connected", "tag": "template"},
        {"picture": "How you're connected", "tag": "template", "boxes": ["Computer", "Wi-Fi", "Router", "Internet"],
         "lit": 2},
        {"line": "Showing how you're connected.", "tag": "template"},
    ],
}

# Esc: a step that touches the system (amber edge, its command), the key, the grey square.
ESC = {
    "id": "fixture-esc", "line": "Esc stops anything it is doing.", "words": "", "marks": ["Esc"],
    "captions": ["It works", "You press Esc", "It stops"],
    "beats": [
        {"type": "install docker and set it up"},
        {"key": "enter"},
        {"stone": "working", "line": "Installing docker", "tag": "template", "tone": "amber",
         "command": "pacman -S docker", "counter": True},
        {"hold": 400},
        {"key": "esc", "phase": 1},
        {"line": "Stopping", "tag": "template", "tone": "amber", "phase": 2},
        {"stone": "stopped", "line": "Stopped while installing docker.", "tag": "template"},
    ],
}

# A card rising, a window sliding over it from the left, the cards folding to strips.
TIMER = {
    "id": "fixture-timer", "line": "Set a timer. It counts on the desk.", "words": "Set a 25 minute timer", "marks": [],
    "captions": ["You ask", "It counts", "It steps aside"],
    "beats": [
        {"type": "Set a 25 minute timer"},
        {"key": "enter"},
        {"card": "watching", "tag": "template", "rows": [["Timer, 25 min", "25:00"]]},
        {"card": "watching", "tag": "template", "rows": [["Timer, 25 min", "24:59"]], "hold": 200},
        {"window": "Notes", "tag": "model", "doc": True, "from": "left"},
        {"fold": True, "strips": ["1 counting"], "tag": "template"},
    ],
}

# The Machine card: meters, no words in a poster.
MACHINE = {
    "id": "fixture-machine", "line": "Ask how the machine is doing.", "words": "How's the machine?", "marks": [],
    "captions": ["You ask", "It answers", "You see it"],
    "beats": [
        {"key": "super"},
        {"type": "How's the machine?"},
        {"key": "enter"},
        {"line": "Here is the machine.", "tag": "template"},
        {"card": "machine", "tag": "template", "why": "The machine is fine",
         "rows": [["memory", 42], ["disk", 61], ["network", 12]]},
    ],
}

# A command with no AI: its output on the line, in mono.
BANG = {
    "id": "fixture-bang", "line": "Run a command without the AI.", "words": "!uname -r", "marks": [],
    "captions": ["You type bang", "It runs", "You see it"],
    "beats": [
        {"key": "bang"},
        {"type": "!uname -r"},
        {"key": "enter"},
        {"line": "6.17.2-arch1-1", "tag": "output", "buttons": ["Details"]},
    ],
}

# Change an app: it is open, then reloads in place and gains a column.
CHANGE = {
    "id": "fixture-change", "line": "Change an app by saying what to add.", "words": "In Tickets, add...", "marks": [],
    "captions": ["You ask", "It changes", "Same place"],
    "beats": [
        {"window": "Tickets", "tag": "model", "rows": TICKETS},
        {"type": "In Tickets, add a due date column"},
        {"key": "enter"},
        {"stone": "working", "line": "Changing Tickets, 180 lines", "tag": "template", "counter": 7},
        {"window": "Tickets", "tag": "model", "reload": True, "columns": [["Due", "Oct 3", "Oct 6", "Oct 9"]]},
        {"stone": "done", "line": "Changed Tickets. It has a Due column.", "tag": "model"},
    ],
}

# Type a name, the ghost completes it, Tab takes it.
OPEN = {
    "id": "fixture-open", "line": "Type an app's name to open it.", "words": "Tickets", "marks": [],
    "captions": ["You type", "It finishes", "It opens"],
    "beats": [
        {"key": "super"},
        {"stone": "listening"},
        {"type": "tic", "ghost": "kets"},
        {"key": "tab"},
        {"key": "enter"},
        {"line": "Opened Tickets.", "tag": "template"},
        {"window": "Tickets", "tag": "model", "rows": ["Printer jams on floor 3", "VPN drops every hour"]},
        {"stone": "rest"},
    ],
}

# A window on its lock screen.
LOCK = {
    "id": "fixture-lock", "line": "A password manager that locks itself.", "words": "Make me a password manager",
    "marks": [], "captions": ["You ask", "It builds", "It locks"],
    "beats": [
        {"type": "Make me a password manager"},
        {"key": "enter"},
        {"stone": "working", "line": "Building Passwords, 210 lines", "tag": "template", "counter": True},
        {"window": "Passwords", "tag": "model", "lock": "Locked after 5 minutes idle"},
        {"stone": "done", "line": "Made Passwords. It is open.", "tag": "model", "buttons": ["Undo", "Details"]},
    ],
}

# The Now card with its route, a step that touches the system, and a stone that needs the user.
ROUTE = {
    "id": "fixture-route", "line": "Install a toolchain, watch each step.", "words": "Install rust and set it up",
    "marks": [], "captions": ["You ask", "Each step shows", "It is ready"],
    "beats": [
        {"type": "Install rust and set it up"},
        {"key": "enter"},
        {"stone": "working", "card": "now", "tag": "template", "title": "Install rust and set it up",
         "why": "step 1 of 3", "rows": [["Install rustup", "current"], ["Get stable Rust", "next"], ["Check cargo", "next"]]},
        {"line": "Installing rustup", "tag": "template", "tone": "red", "command": "pacman -S rustup"},
        {"card": "now", "tag": "template", "why": "step 2 of 3",
         "rows": [["Install rustup", "done"], ["Get stable Rust", "current"], ["Check cargo", "next"]]},
        {"stone": "needs", "line": "Rust asks which toolchain.", "tag": "model"},
    ],
}

# Bars: each disk and how full, the fullest amber; the pill rests as stopped, then resting.
DISK = {
    "id": "fixture-disk", "line": "Ask where your disk space went.", "words": "Where did my disk go?", "marks": [],
    "captions": ["You ask", "It draws", "You see it"],
    "beats": [
        {"type": "Where did my disk go?"},
        {"key": "enter"},
        {"line": "Drawing your disks", "tag": "template"},
        {"picture": "Your disks", "tag": "template", "bars": [["/", 62], ["/home", 91, "amber"], ["USB", 12]]},
        {"stone": "resting", "line": "Showing your disks.", "tag": "template", "hold": 300},
    ],
}

# A scene that ends on a working stone and an amber line with its command: the poster must stay in ink.
BUSY_END = {
    "id": "fixture-busy-end", "line": "Esc stops anything.", "words": "", "marks": ["Esc"],
    "captions": ["It works", "It works on", "Still working"],
    "beats": [
        {"stone": "working", "line": "Installing docker", "tag": "template", "tone": "amber",
         "command": "pacman -S docker", "counter": 3, "buttons": ["Undo", "Details"]},
        {"hold": 300},
    ],
}

# Two cards fold to two strips beside the pill: the pill is what they leave, and no strip runs under it.
FOLDS = {
    "id": "fixture-folds", "line": "Say desk to fold the cards, desk to unfold.", "words": "desk", "marks": [],
    "captions": ["Two cards", "You say desk", "Two strips"],
    "beats": [
        {"card": "now", "tag": "template", "title": "Install rust and set it up", "why": "step 2 of 2",
         "rows": [["Install rustup", "done"], ["Get stable Rust", "current"]]},
        {"card": "watching", "tag": "template", "rows": [["Timer, 25 min", "24:59"]], "phase": 1},
        {"type": "desk"},
        {"key": "enter", "phase": 2},
        {"fold": True, "strips": ["step 2 of 2", "1 counting"], "tag": "template"},
        {"line": "Folded the desk.", "tag": "template"},
    ],
}

# The reply to undo as the code says it: 112 characters, which the real line wraps to a few lines.
UNDO = {
    "id": "fixture-undo", "line": "Say undo to go back a step.", "words": "undo", "marks": ["undo"],
    "captions": ["You say undo", "It goes back", "Files return"],
    "beats": [
        {"type": "undo"},
        {"key": "enter", "phase": 1},
        {"stone": "done", "line": "Undone. System files go back to before \u201cinstall htop\u201d when you restart. "
                                  "Your home folder and apps stay as they are.", "tag": "template", "phase": 2},
    ],
}

# Asks that wait as grey chips while the AI rests, and then run.
WAITS = {
    "id": "fixture-waits", "line": "Out of Claude? Your apps still work.", "words": "pause Claude", "marks": [],
    "captions": ["You pause", "An ask waits", "It runs"],
    "beats": [
        {"type": "pause Claude"},
        {"key": "enter", "phase": 1},
        {"stone": "resting", "line": "Claude is paused. Your apps and files still work.", "tag": "template"},
        {"type": "tidy my downloads"},
        {"key": "enter"},
        {"queue": ["tidy my downloads"], "wait": "paused", "line": "Kept until you resume Claude.",
         "tag": "template"},
        {"type": "resume claude", "phase": 2},
        {"key": "enter"},
        {"queue": [], "stone": "working", "line": "Claude is back. Running your ask.", "tag": "template"},
    ],
}

# Things taken away again: the counter stopped, a line cleared, a window slid out.
CLEARS = {
    "id": "fixture-clears", "line": "Things leave when they are done.", "words": "", "marks": [],
    "captions": ["It counts", "It stops", "It is gone"],
    "beats": [
        {"stone": "working", "line": "Working on it", "tag": "template", "counter": True},
        {"window": "Notes", "tag": "model", "doc": True},
        {"hold": 1100},
        {"line": "Still working", "tag": "template", "counter": False},
        {"line": ""},
        {"window": False},
    ],
}

# A very short scene, for the tests that play in real time.
SHORT = {
    "id": "fixture-short", "line": "Short.", "words": "", "marks": [], "captions": ["One", "Two", "Three"],
    "beats": [{"key": "enter"}, {"hold": 100}, {"line": "Done.", "tag": "caption"}],
}

ALL = [BUILD, PICTURE, ESC, TIMER, MACHINE, BANG, CHANGE, OPEN, LOCK, ROUTE, DISK, BUSY_END, FOLDS, UNDO, WAITS,
       CLEARS, SHORT]
