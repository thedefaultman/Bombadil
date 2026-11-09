"""The Tips app's line to agentd and to tips.json, and the few things QML cannot do alone.

agentd says which tips this machine can back: {"type": "tips", "op": "list"} is answered, on that
connection only, with {"type": "tips", "list": [...]} (tips.py, docs/TIPS.md). The window shows what agentd
lists and nothing else, so a tip the machine cannot back is not in it, not even greyed.

Every request is a one-shot: a fresh QLocalSocket that connects, sends its line, reads the answer if there
is one and closes. An open socket would count as a bar for the machine sampler, and the Tips window stays
open for as long as the session does. The window never writes tips.json (agentd is its only writer); it
watches it, so that a tick appears when a tip is tried, and so that it opens on the tip a press on the desk
card named (`open_on`). It says it read that request ({"type": "tips", "op": "opened"}), one more one-shot,
so that the next press on the same tip is a request again.

What QML sees: `connected` (agentd answered the last request), `model` (the rows of the list, sections and
tips, in the order they are shown), `selected` (the id of the tip in front), `selectedTip` (its row, whole:
line, words, marks, captions, beats), `selectedRow`, `count`; and select(id), move(delta), draftOf(id),
refresh(). `openRequested(id)` says a press on the card named the tip already in front: play it again.
While agentd is away the window says so and keeps trying every two seconds, like the Brain.
"""

import json
import os
import re
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QFileSystemWatcher,
    QObject,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtNetwork import QLocalSocket

RETRY_MS = 2000      # while agentd does not answer
ANSWER_MS = 5000     # a request that is not answered by then is given up on
SETTLE_MS = 300      # a burst of writes to tips.json becomes one look
WATCH_MS = 5000      # a safety net under the file watcher: a look at the file and at what is watched

# The order of the list: agentd's sections, then New (tips an update brought that the person has not seen).
SECTIONS = (("start", "Start here"), ("work", "Work"), ("code", "Code"), ("machine", "This machine"))
NEW = ("new", "New")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
CHECKING = os.environ.get("BOMBADIL_CHECK") == "1"


def _runtime() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    rt = os.environ.get("BOMBADIL_RUNTIME")
    return Path(rt).expanduser() if rt else Path(base) / "bombadil"


def agent_socket() -> str:
    return os.environ.get("BOMBADIL_SOCKET") or str(_runtime() / "agentd.sock")


def tips_file() -> Path:
    """What agentd keeps of what the person has been taught (paths.tips_file)."""
    state = os.environ.get("BOMBADIL_STATE")
    if state:
        return Path(state).expanduser() / "tips.json"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "bombadil" / "tips.json"


def _text(value, limit: int) -> str:
    """One line of plain text: whatever came over the socket, collapsed and cut."""
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def clean(entry) -> dict | None:
    """One tip as the window holds it, or None for anything agentd could not have meant to list."""
    if not isinstance(entry, dict):
        return None
    tip_id, section, line = entry.get("id"), entry.get("section"), _text(entry.get("line"), 200)
    if not isinstance(tip_id, str) or not ID_RE.fullmatch(tip_id) or section not in dict(SECTIONS) or not line:
        return None
    marks = [m for m in (_text(m, 60) for m in entry.get("marks") or [] if isinstance(m, str)) if m][:4]
    captions = [_text(c, 40) for c in entry.get("captions") or []][:3] if isinstance(entry.get("captions"), list) else []
    beats = [b for b in entry.get("beats") or [] if isinstance(b, dict)] if isinstance(entry.get("beats"), list) else []
    order = entry.get("order")
    return {"id": tip_id, "section": section, "order": order if isinstance(order, int) else 0, "line": line,
            "words": _text(entry.get("words"), 300), "marks": marks, "captions": captions, "beats": beats,
            "card": entry.get("card") is not False, "tried": entry.get("tried") is True,
            "new": entry.get("new") is True}


def draft_of(words: str) -> str:
    """What a tip's button puts in the pill. The exact words, except that a "…" at the end is the start of an
    ask ("In Tickets, add…"): the dots go and a space stays, so the cursor waits where the person's own
    details go."""
    words = " ".join(str(words or "").split())
    if not words:
        return ""
    return words.removesuffix("…").rstrip() + " " if words.endswith("…") else words


class Once(QObject):
    """One request to agentd on a connection of its own, closed when it is answered (or at once when nothing
    answers a line of this kind). `finished(ok, message)`: the message that `answer` accepted, or None. Not
    ok when agentd is away or says nothing in time. Nothing happens until `start()`: a socket whose path is
    not there fails at once, and whoever listens for `finished` has to be connected by then."""

    finished = Signal(bool, object)

    def __init__(self, parent: QObject, message: dict, answer=None, timeout_ms: int | None = None):
        super().__init__(parent)
        self._line = (json.dumps(message) + "\n").encode()
        self._answer = answer
        self._buffer = b""
        self._over = False
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._write)
        self._socket.readyRead.connect(self._read)
        self._socket.bytesWritten.connect(self._written)
        self._socket.errorOccurred.connect(lambda _e: self._finish(False, None))
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._finish(False, None))
        self._timeout_ms = timeout_ms or ANSWER_MS

    def start(self) -> "Once":
        self._timeout.start(self._timeout_ms)
        self._socket.connectToServer(agent_socket())
        return self

    def _write(self):
        self._socket.write(self._line)
        self._socket.flush()

    def _written(self, _n):
        if self._answer is None and self._socket.bytesToWrite() == 0:
            self._finish(True, None)

    def _read(self):
        self._buffer += bytes(self._socket.readAll().data())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if self._answer is not None and isinstance(msg, dict) and self._answer(msg):
                self._finish(True, msg)
                return

    def _finish(self, ok: bool, message):
        if self._over:
            return
        self._over = True
        self._timeout.stop()
        if self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._socket.disconnectFromServer()
        else:
            self._socket.abort()
        self.finished.emit(ok, message)
        self.deleteLater()


def _is_list(msg: dict) -> bool:
    return msg.get("type") == "tips" and isinstance(msg.get("list"), list)


class Rows(QAbstractListModel):
    """The list as the window draws it: a heading row and then its tips, section after section. A tick or a
    new line is `dataChanged` on its row, so the list keeps its place; a different set of rows is a reset."""

    ROLES = ("kind", "key", "title", "line", "words", "marks", "tried")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []

    def roleNames(self):
        return {Qt.ItemDataRole.UserRole + 1 + i: name.encode() for i, name in enumerate(self.ROLES)}

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        i = role - Qt.ItemDataRole.UserRole - 1
        return self._rows[index.row()].get(self.ROLES[i]) if 0 <= i < len(self.ROLES) else None

    def rows(self) -> list[dict]:
        return self._rows

    def set(self, rows: list[dict]) -> None:
        if [r["key"] for r in rows] != [r["key"] for r in self._rows]:
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            return
        for i, (old, new) in enumerate(zip(self._rows, rows, strict=True)):
            if old != new:
                self._rows[i] = new
                self.dataChanged.emit(self.index(i), self.index(i))


class Backend(QObject):
    connectedChanged = Signal()
    tipsChanged = Signal()
    selectedChanged = Signal()        # a different tip is in front: the window plays it
    selectedTipChanged = Signal()     # the tip in front, or how it reads (its words, its tick)
    selectedRowChanged = Signal()
    openRequested = Signal(str, arguments=["id"])

    def __init__(self):
        super().__init__()
        self._connected = False
        self._tips: list[dict] = []          # what agentd listed, cleaned, in the order it is shown
        self._tried: set[str] = set()        # tip ids, from the list and from tips.json
        self._fresh: set[str] = set()        # ids that were new when listed: they stay in New until the app ends
        self._selected = ""
        self._row = -1
        self._asking: Once | None = None
        self._again = False                  # asked again while a request was out
        self._open = ""                      # a press on the desk card named this tip; no list yet to find it in
        self._open_seen: str | None = None   # the last `open_on` the file held, "" or an id
        self._seen_tried: frozenset | None = None
        self._model = Rows(self)

        self._retry = QTimer(self)
        self._retry.setInterval(RETRY_MS)
        self._retry.timeout.connect(self.refresh)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self._looked)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda _p: self._settle.start())
        self._watcher.directoryChanged.connect(lambda _p: self._settle.start())
        self._net = QTimer(self)
        self._net.setInterval(WATCH_MS)
        self._net.timeout.connect(self._looked)
        if CHECKING:                          # `check` is read-only and never talks to agentd
            return
        self._read_state(first=True)
        self._arm()
        self._net.start()
        self.refresh()
        self._retry.start()

    # ---- what QML reads -------------------------------------------------------------

    @Property(bool, notify=connectedChanged)
    def connected(self):
        return self._connected

    @Property(QObject, constant=True)
    def model(self):
        return self._model

    @Property(int, notify=tipsChanged)
    def count(self):
        return len(self._tips)

    @Property("QVariant", notify=tipsChanged)
    def tips(self):
        return self._tips

    @Property(str, notify=selectedChanged)
    def selected(self):
        return self._selected

    @Property("QVariant", notify=selectedTipChanged)
    def selectedTip(self):
        return self._row_of_tip(self._selected) or {}

    @Property(int, notify=selectedRowChanged)
    def selectedRow(self):
        return self._row

    # ---- choosing a tip -------------------------------------------------------------

    def _row_of_tip(self, tip_id: str):
        return next((t for t in self._tips if t["id"] == tip_id), None)

    def _set_selected(self, tip_id: str) -> bool:
        if tip_id == self._selected:
            return False
        self._selected = tip_id
        self.selectedTipChanged.emit()
        self.selectedChanged.emit()
        self._place()
        return True

    def _place(self):
        """Where the tip in front is among the rows (the headings count)."""
        row = next((i for i, r in enumerate(self._model.rows()) if r["kind"] == "tip" and r["key"] == self._selected),
                   -1)
        if row != self._row:
            self._row = row
            self.selectedRowChanged.emit()

    @Slot(str)
    def select(self, tip_id: str):
        if self._row_of_tip(tip_id) is not None:
            self._set_selected(tip_id)

    @Slot(int)
    def move(self, delta: int):
        """Up and Down: the next tip, or the one before, in the order the list shows. Not past either end."""
        if not self._tips:
            return
        ids = [t["id"] for t in self._tips]
        i = ids.index(self._selected) if self._selected in ids else -1 if delta > 0 else len(ids)
        self._set_selected(ids[max(0, min(len(ids) - 1, i + (1 if delta > 0 else -1)))])

    @Slot(str, result=str)
    def draftOf(self, tip_id: str) -> str:
        tip = self._row_of_tip(tip_id)
        return draft_of(tip["words"]) if tip else ""

    # ---- what agentd lists ----------------------------------------------------------

    @Slot()
    def refresh(self):
        """Ask agentd which tips this machine can back, once. Never in `check`."""
        if CHECKING:
            return
        if self._asking is not None:
            self._again = True
            return
        once = Once(self, {"type": "tips", "op": "list"}, answer=_is_list)
        self._asking = once
        once.finished.connect(self._answered)
        once.start()

    def _answered(self, ok: bool, msg):
        self._asking = None
        if ok and isinstance(msg, dict):
            self._set_connected(True)
            self._retry.stop()
            self._take(msg["list"])
        else:
            self._set_connected(False)
            if not self._retry.isActive():
                self._retry.start()
        if self._again:
            self._again = False
            self.refresh()

    def _set_connected(self, up: bool):
        if up != self._connected:
            self._connected = up
            self.connectedChanged.emit()

    def _take(self, listed: list):
        tips, seen = [], set()
        for entry in listed:
            tip = clean(entry)
            if tip is not None and tip["id"] not in seen:
                seen.add(tip["id"])
                tips.append(tip)
        self._tried = {t["id"] for t in tips if t["tried"]}
        self._fresh |= {t["id"] for t in tips if t["new"]}
        self._arrange(tips)
        if self._selected not in {t["id"] for t in self._tips}:      # first look, or the tip went away
            self._set_selected(self._tips[0]["id"] if self._tips else "")
        self._request_taken()

    def _arrange(self, tips: list[dict]):
        """The order the list shows: each section's tips by their order, then New. A tip an update brought is
        in New alone, not in its section as well."""
        for t in tips:
            t["tried"] = t["id"] in self._tried
            t["new"] = t["id"] in self._fresh
        rank = {name: i for i, (name, _title) in enumerate(SECTIONS)}
        tips.sort(key=lambda t: (t["new"], rank[t["section"]], t["order"]))
        shown = []
        for name, title in (*SECTIONS, NEW):
            group = [t for t in tips if (t["new"] if name == "new" else t["section"] == name and not t["new"])]
            if group:
                shown.append({"kind": "section", "key": f"section:{name}", "title": title, "line": "", "words": "",
                              "marks": [], "tried": False})
                shown += [{"kind": "tip", "key": t["id"], "title": "", "line": t["line"],
                           "words": t["words"] or "  ".join(t["marks"]), "marks": t["marks"],
                           "tried": t["tried"]} for t in group]
        order = [r["key"] for r in shown if r["kind"] == "tip"]
        self._tips = sorted(tips, key=lambda t: order.index(t["id"]))
        self._model.set(shown)
        self.tipsChanged.emit()
        self.selectedTipChanged.emit()   # the tip in front may read differently now (its words, its tick)
        self._place()

    # ---- tips.json: a tick when a tip is tried, and the tip a press on the card named ----

    def _arm(self):
        """Watch the file and the folder it is written into (agentd replaces the file, which drops a watch on
        it). Whatever is not there yet is watched when it is."""
        path = tips_file()
        have = set(self._watcher.files()) | set(self._watcher.directories())
        for want in (path.parent, path):
            if want.exists() and str(want) not in have:
                self._watcher.addPath(str(want))

    def _looked(self):
        self._arm()
        self._read_state()

    def _read_state(self, first: bool = False):
        try:
            data = json.loads(tips_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        # A request is a change of `open_on` (agentd clears it when it hears "opened"): the same value read
        # again is not a new press, or the window would jump back to it every time the file is looked at.
        open_on = data.get("open_on")
        if isinstance(open_on, str) and open_on != self._open_seen:
            self._open_seen = open_on
            if ID_RE.fullmatch(open_on):
                self._open = open_on
                self._request_taken()
        if not isinstance(data.get("tried"), list):      # not what agentd writes: it says nothing about ticks
            return
        tried = frozenset(i for i in data["tried"] if isinstance(i, str) and ID_RE.fullmatch(i))
        if tried != self._seen_tried:
            changed = self._seen_tried is not None
            self._seen_tried = tried
            if self._tips and tried != self._tried:
                self._tried = set(tried)
                self._arrange(self._tips)
            # What a turn tried may have changed what the machine backs (an app was made): look again.
            if changed and not first:
                self.refresh()

    def _request_taken(self):
        """A press on the desk card named a tip: show it, and say it was read."""
        if not self._open or not self._tips:
            return
        tip_id, self._open = self._open, ""
        if self._row_of_tip(tip_id) is not None and not self._set_selected(tip_id):
            self.openRequested.emit(tip_id)         # already in front: play it again
        if not CHECKING:
            Once(self, {"type": "tips", "op": "opened", "id": tip_id}).start()
