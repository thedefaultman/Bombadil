"""`Agent`: the app's line to agentd, the same session the user talks to in the bar.

Newline-delimited JSON over agentd's Unix socket (see agentd.py). The singleton is made the first time
an app's QML mentions `Agent`, and connects the first time the app looks at it: a read of a property (a
binding or a handler), a handler on one of its signals, or `ask`. It retries every 3 s while agentd is
away. An app that only drafts (`draft`, below) never holds a connection open. `check` never connects or
sends.

`draft(text)` puts words in the pill and nothing more: a one-shot {"type": "summon", "text", "tip": true}
on a connection of its own that closes as soon as the line is written. The pill takes the keyboard with the
cursor at the end, keeps what the person had typed and not sent, and Enter sends the words as the person's
own ask. It is how a button can offer a sentence to say without saying it (the Tips app's buttons).

agentd answers each prompt with {"type": "queued", "turn": id} on this connection only, and
tags every event with its turn id, so the app follows its own turns by id. That also
catches a turn that fails before it starts (provider missing): error and turn_end, no turn_start.
A prompt that waits behind another turn is also announced to everyone as a "queued" event, which
is not the start of that turn; one dropped from the queue gets an "unqueued" event and nothing more.

While the AI rests (out of plan or spending, or paused by hand) every "status" carries "setup":
"resting" and a "rest" whose "note" ("At 15:00", "Paused") is what `note` holds; `ready` is False then.
An ask still goes out and waits in the queue like any other. A turn the limit stopped partway ends
with turn_end "requeued": it is not over, goes back to the queue under the same id and runs again
when the limit lifts, so it keeps its place in the app's turns and its partial reply is cleared for
the rerun. An ask that a newer ask of the same app replaces gets "unqueued" with "replaced" and goes
silently. The app never learns of a limit as an error string.
"""

import json

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtQml import qmlRegisterSingletonType

from ... import paths
from ..context import AppContext
from . import MAJOR, MINOR, URI

SHOT_SECONDS = 2.0      # a one-shot line that is not written by then is given up on


class _OneShot(QObject):
    """One line to agentd on a connection that exists for it alone: connect, write, close. It is its own
    object so that it outlives the call and is gone when it is done; an agentd that is away or slow costs
    nothing but the line."""

    def __init__(self, parent: QObject, path: str, line: bytes):
        super().__init__(parent)
        self._line = line
        self._over = False
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._write)
        self._socket.errorOccurred.connect(self._done)
        self._socket.bytesWritten.connect(self._written)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._done)
        self._timeout.start(int(SHOT_SECONDS * 1000))
        self._socket.connectToServer(path)

    def _write(self):
        self._socket.write(self._line)
        self._socket.flush()

    def _written(self, _n):
        if self._socket.bytesToWrite() == 0:
            self._done()

    def _done(self, *_):
        if self._over:
            return
        self._over = True
        self._timeout.stop()
        if self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._socket.disconnectFromServer()
        else:
            self._socket.abort()
        self.deleteLater()


class Agent(QObject):
    # What an app can look at without reading a property: a handler on one of these is a look too.
    SIGNALS = frozenset({"connectedChanged", "busyChanged", "providerChanged", "noteChanged", "readyChanged",
                         "replyChanged", "replied"})

    connectedChanged = Signal()
    busyChanged = Signal()
    providerChanged = Signal()
    noteChanged = Signal()
    readyChanged = Signal()
    replyChanged = Signal()
    replied = Signal(str, arguments=["text"])

    def __init__(self, ctx: AppContext, lazy: bool = False):
        super().__init__()
        self._ctx = ctx
        self._lazy = lazy               # the QML singleton: connects when the app first asks for its state
        self._started = False
        self._connected = False
        self._busy = False
        self._provider = ""
        self._note = ""                 # what an ask button says while the AI rests ("At 15:00", "Paused")
        self._ready = True              # False while the AI rests
        self._reply = ""
        self._outbox: list[str] = []    # asked before the connection was up
        self._waiting = 0               # prompts sent whose "queued" (with the turn id) has not come
        self._turns: set[int] = set()   # this app's turns that have not ended
        self._current = None            # the turn `reply` belongs to
        self._buffer = b""
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.errorOccurred.connect(self._on_disconnected)
        self._socket.readyRead.connect(self._on_ready_read)
        self._retry = QTimer(self)
        self._retry.setInterval(3000)
        self._retry.timeout.connect(self._connect)

    def start(self):
        """Connect, and keep reconnecting while agentd is away. Never during `check`."""
        if self._started or self._ctx.check:
            return
        self._started = True
        self._connect()
        self._retry.start()

    def _connect(self):
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(str(paths.socket_path()))

    def _set(self, attr: str, value, signal):
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _on_connected(self):
        self._retry.stop()
        self._set("_connected", True, self.connectedChanged)
        outbox, self._outbox = self._outbox, []
        for text in outbox:
            self._send(text)

    def _send(self, text: str):
        self._waiting += 1
        self._socket.write((json.dumps({"type": "prompt", "text": text}) + "\n").encode())
        self._socket.flush()

    def _on_disconnected(self, *_):
        if self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            return
        self._buffer = b""
        if self._turns or self._waiting:        # a turn of ours was queued or running
            if self._current not in self._turns:
                self._set("_reply", "", self.replyChanged)
            self._add_error("lost the connection to agentd")
            self.replied.emit(self._reply)
        self._waiting, self._turns, self._current = 0, set(), None
        self._set("_connected", False, self.connectedChanged)
        self._set("_busy", False, self.busyChanged)
        self._set_rest({})                      # agentd says again when it is back
        if self._started and not self._retry.isActive():
            self._retry.start()

    def _on_ready_read(self):
        self._buffer += self._socket.readAll().data()
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.handle(msg)

    def _add_error(self, text: str):
        sep = "\n\n" if self._reply else ""
        self._set("_reply", self._reply + sep + "Error: " + text, self.replyChanged)

    def _set_rest(self, status: dict):
        """`ready` and `note` from a status message ({} while agentd is away: not resting)."""
        rest = status.get("rest") if isinstance(status.get("rest"), dict) else {}
        self._set("_ready", not (rest or status.get("setup") == "resting"), self.readyChanged)
        self._set("_note", str(rest.get("note") or ""), self.noteChanged)

    def handle(self, msg: dict):
        """One message from agentd."""
        if msg.get("type") == "status":
            self._set("_busy", bool(msg.get("busy")), self.busyChanged)
            self._set("_provider", str(msg.get("provider") or ""), self.providerChanged)
            self._set_rest(msg)
            return
        if msg.get("type") == "queued":       # replies come in the order the prompts went out
            if self._waiting > 0:
                self._waiting -= 1
                self._turns.add(msg.get("turn"))
            return
        if msg.get("type") != "event":
            return
        kind, turn = msg.get("kind"), msg.get("turn")
        if kind == "queued":                  # a prompt waiting behind another turn: nothing has run yet
            return
        if kind == "turn_start":
            self._set("_busy", True, self.busyChanged)
        if turn is None or turn not in self._turns:
            return
        if kind == "unqueued":                # dropped from the queue: no turn_start or turn_end follows
            self._turns.discard(turn)
            if msg.get("replaced"):           # this app's newer ask took its place: nothing to tell
                return
            text = "Error: dropped from the queue"
            if self._current not in self._turns:    # no running turn of ours owns `reply`
                self._set("_reply", text, self.replyChanged)
            self.replied.emit(text)
            return
        if turn != self._current:             # the first event of a new turn of ours
            self._current = turn
            self._set("_reply", "", self.replyChanged)
        if kind == "text" and msg.get("text"):
            sep = "\n\n" if self._reply and not self._reply.endswith("\n") else ""
            self._set("_reply", self._reply + sep + str(msg["text"]), self.replyChanged)
        elif kind == "result" and msg.get("text") and not self._reply:
            self._set("_reply", str(msg["text"]), self.replyChanged)
        elif kind == "error" and msg.get("text"):
            self._add_error(str(msg["text"]))
        elif kind == "turn_end":
            if msg.get("requeued"):           # the limit stopped it: it waits and runs again under this id
                self._current = None
                self._set("_reply", "", self.replyChanged)
                return
            self._turns.discard(turn)
            self.replied.emit(self._reply)

    @Slot(str)
    def ask(self, prompt: str):
        """Send a prompt as the user would; it waits for the connection if agentd is not there yet."""
        prompt = prompt.strip()
        if not prompt or self._ctx.check:
            return
        self.start()
        text = f"[from app {self._ctx.name}] {prompt}"
        if self._connected:
            self._send(text)
        else:
            self._outbox.append(text)

    @Slot(str)
    @Slot(str, str)
    def draft(self, text: str, tip_id: str = ""):
        """Put these words in the pill, with the cursor at the end, and send nothing: the person's Enter
        sends them, Esc drops them. Never replaces words the person typed and has not sent. `tip_id` is the
        id of the Tips tip the words belong to (the wire's `id`; its `tip` is the boolean), so agentd can
        tell when they were sent as they were. A one-shot on a connection of its own, which closes when the
        line is written; nothing is queued, so with agentd away nothing happens. Never in `check`."""
        words = str(text) if text else ""
        if not words.strip() or self._ctx.check:
            return
        msg = {"type": "summon", "text": words, "tip": True}
        if tip_id:
            msg["id"] = str(tip_id)
        _OneShot(self, str(paths.socket_path()), (json.dumps(msg) + "\n").encode())

    def _wake(self):
        """The first look at the QML singleton connects it (an app that only drafts never looks). The look may
        be a binding in the middle of its evaluation, and a connection to a listening socket is made at once:
        announcing `connectedChanged` then would tell that binding about a change while it runs, which QML
        logs as a binding loop. So nothing is announced for it: the looker reads the value the connection has
        by the time it reads, as it did when the singleton connected as it was made. Later changes are
        announced as always."""
        if not self._lazy or self._started or self._ctx.check:
            return
        was = self.blockSignals(True)
        try:
            self.start()
        finally:
            self.blockSignals(was)

    def connectNotify(self, signal):
        """A binding or a handler on one of the signals is a look (it comes before the read, for a binding)."""
        if bytes(signal.name()).decode() in self.SIGNALS:
            self._wake()
        super().connectNotify(signal)

    def _live(self, attr: str):
        """What a property of the QML singleton says; reading it is a look."""
        self._wake()
        return getattr(self, attr)

    connected = Property(bool, lambda self: self._live("_connected"), notify=connectedChanged)
    busy = Property(bool, lambda self: self._live("_busy"), notify=busyChanged)
    provider = Property(str, lambda self: self._live("_provider"), notify=providerChanged)
    note = Property(str, lambda self: self._live("_note"), notify=noteChanged)
    ready = Property(bool, lambda self: self._live("_ready"), notify=readyChanged)
    reply = Property(str, lambda self: self._live("_reply"), notify=replyChanged)


_instance: Agent | None = None      # the latest one made, for Python callers and tests


def register(ctx: AppContext) -> None:
    def make(engine):
        global _instance
        _instance = Agent(ctx, lazy=True)
        return _instance

    qmlRegisterSingletonType(Agent, URI, MAJOR, MINOR, "Agent", make)
