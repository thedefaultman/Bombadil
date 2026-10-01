"""`Agent`: the app's line to agentd, the same session the user talks to in the bar.

Newline-delimited JSON over agentd's Unix socket (see agentd.py). The singleton is made,
and connects, the first time an app's QML mentions `Agent`; it retries every 3 s while
agentd is away. `check` never connects or sends.

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


class Agent(QObject):
    connectedChanged = Signal()
    busyChanged = Signal()
    providerChanged = Signal()
    noteChanged = Signal()
    readyChanged = Signal()
    replyChanged = Signal()
    replied = Signal(str, arguments=["text"])

    def __init__(self, ctx: AppContext):
        super().__init__()
        self._ctx = ctx
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

    connected = Property(bool, lambda self: self._connected, notify=connectedChanged)
    busy = Property(bool, lambda self: self._busy, notify=busyChanged)
    provider = Property(str, lambda self: self._provider, notify=providerChanged)
    note = Property(str, lambda self: self._note, notify=noteChanged)
    ready = Property(bool, lambda self: self._ready, notify=readyChanged)
    reply = Property(str, lambda self: self._reply, notify=replyChanged)


_instance: Agent | None = None      # the latest one made, for Python callers and tests


def register(ctx: AppContext) -> None:
    def make(engine):
        global _instance
        _instance = Agent(ctx)
        _instance.start()
        return _instance

    qmlRegisterSingletonType(Agent, URI, MAJOR, MINOR, "Agent", make)
