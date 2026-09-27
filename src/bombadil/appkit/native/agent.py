"""`Agent`: the app's line to agentd, the same session the user talks to in the bar.

Newline-delimited JSON over agentd's Unix socket (see agentd.py). The singleton is made,
and connects, the first time an app's QML mentions `Agent`; it retries every 3 s while
agentd is away. `check` never connects or sends.

agentd answers each prompt with {"type": "queued", "turn": id} on this connection only, and
tags every event with its turn id, so the app follows its own turns by id. That also
catches a turn that fails before it starts (provider missing): error and turn_end, no turn_start.
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
    replyChanged = Signal()
    replied = Signal(str, arguments=["text"])

    def __init__(self, ctx: AppContext):
        super().__init__()
        self._ctx = ctx
        self._started = False
        self._connected = False
        self._busy = False
        self._provider = ""
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
        if self._current in self._turns:
            self._add_error("lost the connection to agentd")
            self.replied.emit(self._reply)
        self._waiting, self._turns, self._current = 0, set(), None
        self._set("_connected", False, self.connectedChanged)
        self._set("_busy", False, self.busyChanged)
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

    def handle(self, msg: dict):
        """One message from agentd."""
        if msg.get("type") == "status":
            self._set("_busy", bool(msg.get("busy")), self.busyChanged)
            self._set("_provider", str(msg.get("provider") or ""), self.providerChanged)
            return
        if msg.get("type") == "queued":       # replies come in the order the prompts went out
            if self._waiting > 0:
                self._waiting -= 1
                self._turns.add(msg.get("turn"))
            return
        if msg.get("type") != "event":
            return
        kind, turn = msg.get("kind"), msg.get("turn")
        if kind == "turn_start":
            self._set("_busy", True, self.busyChanged)
        if turn is None or turn not in self._turns:
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
    reply = Property(str, lambda self: self._reply, notify=replyChanged)


_instance: Agent | None = None      # the latest one made, for Python callers and tests


def register(ctx: AppContext) -> None:
    def make(engine):
        global _instance
        _instance = Agent(ctx)
        _instance.start()
        return _instance

    qmlRegisterSingletonType(Agent, URI, MAJOR, MINOR, "Agent", make)
