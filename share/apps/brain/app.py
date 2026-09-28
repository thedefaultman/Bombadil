"""The Brain app's line to bombadil-brain, and the few things QML cannot do alone.

The brain answers over $XDG_RUNTIME_DIR/bombadil/brain.sock in JSON lines: a request
{"id", "op", ...} gets {"id", "ok", "result" | "error"}, and after "subscribe" it pushes
"changed" (things that changed), "show" (the launcher asked for a thing) and "status".
QLocalSocket keeps all of it on Qt's event loop, so nothing here blocks the window.

What QML sees: `focus` (the whole Focus answer for the thing in the middle), `trail`
(where you have been), `results` (search), `status`, `connected`; and go(ref), back(),
jump(i), search(q), more(), open…(), ask(). The brain is never required: while it is away
the window says so and keeps trying every two seconds.
"""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

TRAIL_MAX = 8
TIMEOUT_S = 6.0
MORE_LIMIT = 50   # items per slot once "+N more" was clicked
CHECKING = os.environ.get("BOMBADIL_CHECK") == "1"


def _runtime() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    rt = os.environ.get("BOMBADIL_RUNTIME")
    return Path(rt).expanduser() if rt else Path(base) / "bombadil"


def brain_socket() -> str:
    return os.environ.get("BOMBADIL_BRAIN_SOCKET") or str(_runtime() / "brain.sock")


def agent_socket() -> str:
    return os.environ.get("BOMBADIL_SOCKET") or str(_runtime() / "agentd.sock")


def _home() -> str:
    return str(Path.home())


def tilde(path: str) -> str:
    """~/Documents/lease.pdf: how a path reads to a person."""
    home = _home()
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


class Backend(QObject):
    focusChanged = Signal()
    trailChanged = Signal()
    resultsChanged = Signal()
    statusChanged = Signal()
    connectedChanged = Signal()
    loadingChanged = Signal()
    errorChanged = Signal()
    selectChanged = Signal()
    previewReady = Signal(str, arguments=["path"])
    showRequested = Signal()          # the launcher asked for a thing: slide the window in
    toast = Signal(str, str, arguments=["text", "tone"])

    def __init__(self):
        super().__init__()
        self._focus = None
        self._trail: list[dict] = []
        self._results: list = []
        self._status = ""
        self._connected = False
        self._loading = False
        self._error = ""
        self._select = ""             # a child to highlight in a folder (Show in folder)
        self._expanded = False        # "+N more" was clicked for the thing in the middle
        self._seed = False            # a fresh open: put the thing's area before it in the trail
        self._next_id = 0
        self._pending: dict[int, tuple[float, str, object]] = {}
        self._gen = 0                 # only the newest focus request may land
        self._show_seq = -1
        self._buffer = b""
        self._search_q = ""
        self._previews: dict[str, str] = {}
        self._rendering: set[str] = set()

        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.errorOccurred.connect(self._on_error)
        self._socket.readyRead.connect(self._on_ready_read)
        self._retry = QTimer(self)
        self._retry.setInterval(2000)
        self._retry.timeout.connect(self._connect)
        self._tick = QTimer(self)     # requests the brain never answered
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._expire)
        self._tick.start()
        self._refetch = QTimer(self)  # a burst of "changed" pushes becomes one refresh
        self._refetch.setSingleShot(True)
        self._refetch.setInterval(300)
        self._refetch.timeout.connect(self.refresh)
        self._searcher = QTimer(self)
        self._searcher.setSingleShot(True)
        self._searcher.setInterval(150)
        self._searcher.timeout.connect(self._search_now)
        self._connect()
        self._retry.start()

    # ---- what QML reads -------------------------------------------------------------

    @Property("QVariant", notify=focusChanged)
    def focus(self):
        return self._focus

    @Property("QVariant", notify=trailChanged)
    def trail(self):
        return self._trail

    @Property("QVariant", notify=resultsChanged)
    def results(self):
        return self._results

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=connectedChanged)
    def connected(self):
        return self._connected

    @Property(bool, notify=loadingChanged)
    def loading(self):
        return self._loading

    @Property(str, notify=errorChanged)
    def error(self):
        return self._error

    @Property(str, notify=selectChanged)
    def select(self):
        return self._select

    @Property(bool, notify=trailChanged)
    def canBack(self):
        return len(self._trail) > 1

    @Property(bool, notify=focusChanged)
    def expanded(self):
        return self._expanded

    @Property(str, constant=True)
    def home(self):
        return _home()

    # ---- the socket -----------------------------------------------------------------

    def _connect(self):
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(brain_socket())

    def _set(self, attr: str, value, signal):
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _on_connected(self):
        self._retry.stop()
        self._buffer = b""
        self._set("_connected", True, self.connectedChanged)
        self._set("_error", "", self.errorChanged)
        self._request("subscribe")
        self._request("status", on=self._on_status)
        # What the launcher asked for, if it asked while we were starting; else what we
        # showed before the brain went away; else home.
        self._request("requested", on=self._on_requested)

    def _on_disconnected(self):
        self._set("_connected", False, self.connectedChanged)
        self._set("_loading", False, self.loadingChanged)
        for _t, _op, cb in self._pending.values():
            if cb is not None:
                cb(None, "The brain went away.")
        self._pending.clear()
        if not self._retry.isActive():
            self._retry.start()

    def _on_error(self, _err):
        if self._socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            self._on_disconnected()

    def _request(self, op: str, on=None, **args) -> int:
        self._next_id += 1
        rid = self._next_id
        if self._socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            if on is not None:
                on(None, "The brain is not running yet.")
            return rid
        self._pending[rid] = (time.monotonic(), op, on)
        self._socket.write((json.dumps({"id": rid, "op": op, **args}) + "\n").encode())
        self._socket.flush()
        return rid

    def _expire(self):
        now = time.monotonic()
        for rid in [r for r, (t, _op, _cb) in self._pending.items() if now - t > TIMEOUT_S]:
            _t, _op, cb = self._pending.pop(rid)
            if cb is not None:
                cb(None, "The brain did not answer.")

    def _on_ready_read(self):
        self._buffer += bytes(self._socket.readAll().data())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self._handle(msg)

    def _handle(self, msg: dict):
        push = msg.get("push")
        if push == "changed":
            ids = set(msg.get("things") or [])
            if ids & self._shown_ids():
                self._refetch.start()
            return
        if push == "show":
            self._show(msg)
            return
        if push == "status":
            self._set_status(msg)
            return
        rid = msg.get("id")
        if rid in self._pending:
            _t, _op, cb = self._pending.pop(rid)
            if cb is not None:
                cb(msg.get("result"), None) if msg.get("ok") else cb(None, str(msg.get("error") or "Something went wrong."))

    # ---- status, show -----------------------------------------------------------------

    def _set_status(self, s):
        """The subtitle says something only while it matters: the first index, a catch-up,
        or saves not being watched. "Knows 12,034 things" every time would be noise."""
        text = ""
        if isinstance(s, dict) and (s.get("walking") or s.get("watching") is False):
            text = str(s.get("text") or "")
        self._set("_status", text, self.statusChanged)

    def _on_status(self, result, _err):
        if result is not None:
            self._set_status(result)

    def _on_requested(self, result, _err):
        if isinstance(result, dict) and result.get("ref"):
            self._show(result, bring=False)   # the window is opening anyway
        elif self._trail:
            self.refresh()
        else:
            self._open_fresh(_home())

    def _show(self, msg: dict, bring: bool = True):
        seq = msg.get("seq")
        if isinstance(seq, int):
            if seq <= self._show_seq:
                return
            self._show_seq = seq
        ref = msg.get("ref")
        if ref:
            self._open_fresh(str(ref))
            if bring:
                self.showRequested.emit()

    def _open_fresh(self, ref: str):
        """A new start (the launcher, or the first open): the trail begins at the thing."""
        self._trail = [{"ref": ref, "title": ""}]
        self._seed = True
        self._set("_select", "", self.selectChanged)
        self.trailChanged.emit()
        self._fetch(ref, expanded=False)

    def _shown_ids(self) -> set:
        f = self._focus
        if not isinstance(f, dict):
            return set()
        ids = set()
        if isinstance(f.get("thing"), dict):
            ids.add(f["thing"].get("id"))
        for slot in (f.get("slots") or {}).values():
            for it in (slot or {}).get("items") or []:
                ids.add(it.get("id"))
        for it in ((f.get("children") or {}).get("items") or []):
            ids.add(it.get("id"))
        ids.discard(None)
        return ids

    # ---- focus ------------------------------------------------------------------------

    def _fetch(self, ref: str, expanded: bool):
        self._gen += 1
        gen = self._gen
        self._set("_loading", True, self.loadingChanged)

        def done(result, err):
            if gen != self._gen:
                # Too late to show, but it still names its step in the trail.
                if err is None and self._name_step(ref, result):
                    self.trailChanged.emit()
                return
            self._set("_loading", False, self.loadingChanged)
            if err is not None:
                self._set("_error", err, self.errorChanged)
                shown = ((self._focus or {}).get("thing") or {}).get("ref")
                if shown != ref:   # a refresh that failed keeps what is there; a new thing does not
                    self._focus = None
                    self.focusChanged.emit()
                    if self._name_step(ref, {"thing": {"title": ref.rstrip("/").rsplit("/", 1)[-1] or ref}}):
                        self.trailChanged.emit()
                return
            self._set("_error", "", self.errorChanged)
            self._landed(ref, result, expanded)

        self._request("focus", on=done, ref=ref, limit=MORE_LIMIT if expanded else 5)

    def _name_step(self, ref: str, result) -> bool:
        thing = (result or {}).get("thing") or {}
        for i, step in enumerate(self._trail):
            if step["ref"] == ref and not step["title"]:
                self._trail[i] = {"ref": ref, "title": thing.get("title") or ref}
                return True
        return False

    def _landed(self, ref: str, result, expanded: bool):
        self._expanded = expanded
        self._focus = result
        thing = (result or {}).get("thing") or {}
        if self._trail and self._trail[-1]["ref"] == ref:
            self._trail[-1] = {"ref": ref, "title": thing.get("title") or ref}
        if self._seed:
            self._seed = False
            area = thing.get("area") or None
            if area and area.get("ref") and area.get("ref") != thing.get("ref") and len(self._trail) == 1:
                self._trail.insert(0, {"ref": area["ref"], "title": area.get("title") or area["ref"]})
        self.trailChanged.emit()
        self.focusChanged.emit()

    @Slot(str)
    def go(self, ref: str):
        """Slide a thing to the middle; the trail remembers where you were."""
        if not ref:
            return
        if self._trail and self._trail[-1]["ref"] == ref:
            self.refresh()
            return
        self._trail.append({"ref": ref, "title": ""})
        del self._trail[:-TRAIL_MAX]
        self._set("_select", "", self.selectChanged)
        self.trailChanged.emit()
        self._fetch(ref, expanded=False)

    @Slot(str)
    def open(self, ref: str):
        """A fresh start on a thing (a search result)."""
        if ref:
            self._open_fresh(ref)

    @Slot(int)
    def jump(self, index: int):
        if 0 <= index < len(self._trail):
            del self._trail[index + 1:]
            self._set("_select", "", self.selectChanged)
            self.trailChanged.emit()
            self._fetch(self._trail[-1]["ref"], expanded=False)

    @Slot()
    def back(self):
        if len(self._trail) > 1:
            self.jump(len(self._trail) - 2)

    @Slot()
    def refresh(self):
        if self._trail:
            self._fetch(self._trail[-1]["ref"], expanded=self._expanded)
        elif self._connected:
            self._open_fresh(_home())

    @Slot()
    def more(self):
        if self._trail:
            self._fetch(self._trail[-1]["ref"], expanded=True)

    @Slot(str, str)
    def showInFolder(self, path: str, folder: str):
        """The folder the thing is in, with the thing selected."""
        if folder:
            self.go(folder)
            self._set("_select", path, self.selectChanged)

    # ---- search -----------------------------------------------------------------------

    @Slot(str)
    def search(self, q: str):
        self._search_q = q.strip()
        if not self._search_q:
            self._searcher.stop()
            self._set("_results", [], self.resultsChanged)
            return
        self._searcher.start()

    def _search_now(self):
        q = self._search_q

        def done(result, _err):
            if q != self._search_q:
                return
            items = result.get("items") if isinstance(result, dict) else result
            self._results = items if isinstance(items, list) else []
            self.resultsChanged.emit()

        self._request("search", on=done, q=q, limit=12)

    # ---- opening, previews, asking -----------------------------------------------------

    @Slot(str, result=bool)
    def openFile(self, path: str) -> bool:
        """Open a file with the app the desktop says opens it (gio, which Files brings)."""
        if CHECKING or not path or not os.path.exists(path):
            return False
        gio = shutil.which("gio")
        if gio:
            ok, _pid = QProcess.startDetached(gio, ["open", path])
            return bool(ok)
        xdg = shutil.which("xdg-open")
        if xdg:
            ok, _pid = QProcess.startDetached(xdg, [path])
            return bool(ok)
        return False

    @Slot(str, result=bool)
    def openApp(self, name: str) -> bool:
        if CHECKING or not name:
            return False
        prog = shutil.which("bombadil-app")
        if not prog:
            return False
        ok, _pid = QProcess.startDetached(prog, ["run", name])
        return bool(ok)

    @Slot(str, result=bool)
    def terminalHere(self, folder: str) -> bool:
        if CHECKING or not os.path.isdir(folder):
            return False
        foot = shutil.which("foot")
        if not foot:
            return False
        ok, _pid = QProcess.startDetached(foot, [f"--working-directory={folder}"])
        return bool(ok)

    @Slot(str, result=bool)
    def ask(self, about: str) -> bool:
        """Put "About <thing>: " in the pill and hand it the keyboard; you finish the question."""
        if CHECKING or not about:
            return False
        sock = QLocalSocket(self)
        sock.connectToServer(agent_socket())
        if not sock.waitForConnected(300):
            sock.deleteLater()
            return False
        sock.write((json.dumps({"type": "summon", "text": f"About {about}: "}) + "\n").encode())
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        sock.deleteLater()
        return True

    @Slot(str, result=str)
    def tilde(self, path: str) -> str:
        return tilde(path)

    @Slot(str, float, result=str)
    def pdfPage(self, path: str, mtime: float) -> str:
        """The first page of a PDF as a picture (pdftoppm), cached by path and mtime. Returns
        "" while it is being drawn; previewReady(path) says when to ask again."""
        if not path:
            return ""
        key = hashlib.sha1(f"{path}\0{mtime}".encode()).hexdigest()
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "bombadil" / "brain"
        png = cache / f"{key}.png"
        if png.exists():
            return QUrl.fromLocalFile(str(png)).toString()
        prog = shutil.which("pdftoppm")
        if not prog or key in self._rendering or CHECKING:
            return ""
        cache.mkdir(parents=True, exist_ok=True)
        self._rendering.add(key)
        proc = QProcess(self)

        def finished(_code, _status):
            self._rendering.discard(key)
            proc.deleteLater()
            if png.exists():
                self.previewReady.emit(path)

        proc.finished.connect(finished)
        proc.start(prog, ["-png", "-f", "1", "-l", "1", "-singlefile", "-scale-to", "1100", path, str(png)[:-4]])
        return ""
