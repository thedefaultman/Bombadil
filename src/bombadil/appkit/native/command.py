"""`Command`: run a program from QML without blocking the UI (QProcess underneath).

A poller's output properties change when a run finishes, so it never shows half an answer;
until then they keep the previous run's values. Any other run (a `tail -f`) shows its output
as it arrives. Only the last MiB or so of each channel is kept.
"""

import codecs
import json
import os
import shutil
import signal

from PySide6.QtCore import Property, QObject, QProcess, QTimer, Signal, Slot

from ..context import AppContext
from . import MAJOR, MINOR, URI
from .files import from_js, js_value


LIMIT = 1 << 20          # characters of stdout (and of stderr) kept


class _Output:
    """One channel's output, decoded once as it arrives, trimmed to its last LIMIT characters."""

    def __init__(self):
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._parts: list[str] = []
        self._size = 0
        self.fresh = False       # something arrived since text() was last read

    def add(self, data: bytes, final: bool = False):
        text = self._decoder.decode(data, final)
        if text:
            self._parts.append(text)
            self._size += len(text)
            self.fresh = True
            if self._size > 2 * LIMIT:
                self._compact()

    def _compact(self):
        s = "".join(self._parts)
        if len(s) > LIMIT:
            s = s[-LIMIT:]
            cut = s.find("\n", 0, 65536)       # start at a whole line when there is one near
            s = s[cut + 1:] if cut != -1 else s
        self._parts, self._size = [s], len(s)

    def text(self) -> str:
        self.fresh = False
        if len(self._parts) > 1 or self._size > LIMIT:
            self._compact()
        return self._parts[0] if self._parts else ""


def _comment_at_end(cmd: str) -> int:
    """Where a `# comment` that runs to the end of `cmd` starts, or -1. Quotes and escapes count."""
    quote, i = "", 0
    while i < len(cmd):
        c = cmd[i]
        if quote == "'":
            quote = "" if c == "'" else quote
        elif c == "\\":
            i += 1
        elif quote:
            quote = "" if c == '"' else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|()<>"):
            end = cmd.find("\n", i)
            if end == -1:
                return i
            i = end
        i += 1
    return -1


def _with_args(cmd: str) -> str:
    """`cmd "$@"`, for `sh -c` with the extra args as positional parameters: appended to the last
    command, never re-parsed by the shell. A trailing newline, `;` or comment would otherwise
    make them a command of their own or comment them out."""
    while True:
        before = cmd
        cmd = cmd.rstrip()
        at = _comment_at_end(cmd)
        if at != -1:
            cmd = cmd[:at]
        elif cmd.endswith(";") and not cmd.endswith((";;", "\\;")):
            cmd = cmd[:-1]
        if cmd == before:
            return cmd + ' "$@"'


def _kill(proc: QProcess):
    """Kill the program and everything it started: `sh -c "a | b"` forks, so killing sh is not enough."""
    pid = proc.processId()
    if pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)   # each run is its own session, so its group is pid
        except OSError:
            pass
    proc.kill()
    proc.waitForFinished(1000)


def _reap(proc: QProcess | None):
    """The Command is going away (hot reload, quit): end its program without Qt's warning."""
    if proc is None:
        return
    try:
        proc.blockSignals(True)
        _kill(proc)
    except RuntimeError:
        pass


class Command(QObject):
    commandChanged = Signal()
    programChanged = Signal()
    argsChanged = Signal()
    runningChanged = Signal()
    intervalChanged = Signal()
    stdinChanged = Signal()
    interactiveChanged = Signal()
    outputChanged = Signal()     # stdout, stderr, lines, exitCode, json
    # Not named exitCode/stdout: a handler that reads those properties would get the
    # (deprecated) injected parameters instead, with a warning.
    finished = Signal(int, str, arguments=["code", "output"])

    def __init__(self, parent=None):
        super().__init__(parent)
        self._command = ""
        self._program = ""
        self._args: list[str] = []
        self._stdout = ""
        self._stderr = ""
        self._exit_code = -1
        self._json = None
        self._js: dict = {}                # json/lines/args as JS values, until they change
        self._stdin = ""
        self._interactive = False
        self._proc: QProcess | None = None
        self._out = _Output()
        self._err = _Output()
        self._ready = False       # QML has set every property; before that `running: true` waits
        self._pending = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # A run that is not a poller shows its output as it comes, a few times a second.
        self._live = QTimer(self)
        self._live.setInterval(250)
        self._live.timeout.connect(self._publish_live)
        # The destroyed hook cannot touch `self`, so it gets the running process through this.
        self._current: list[QProcess | None] = [None]
        self.destroyed.connect(lambda *_, cur=self._current: _reap(cur[0]))
        QTimer.singleShot(0, self, self._begin)

    @Slot()
    def _begin(self):
        self._ready = True
        if self._pending or self._timer.interval() > 0:
            self.run()
        if self._timer.interval() > 0:
            self._timer.start()

    @Slot()
    def _tick(self):
        if self._proc is None:
            self.run()

    def _argv(self, extra: list[str]) -> tuple[str, list[str]] | None:
        if self._command:
            if extra:
                return "/bin/sh", ["-c", _with_args(self._command), "sh", *extra]
            return "/bin/sh", ["-c", self._command]
        if self._program:
            args = [os.path.expanduser(a) for a in self._args + extra]
            return os.path.expanduser(self._program), args
        return None

    @Slot()
    @Slot("QVariant")
    def run(self, extra=None):
        """Start now; a run still going is stopped first. `extra` args are appended."""
        extra = from_js(extra)
        if extra is None:
            extra = []
        elif not isinstance(extra, list):
            extra = [extra]
        was_running = self._get_running()
        self._pending = False
        self._stop(emit=False)
        argv = self._argv([str(a) for a in extra])
        if argv is None:
            self._set_result("", "Command: set `command` or `program`", -1)
            if was_running:
                self.runningChanged.emit()
            return
        proc = QProcess(self)
        proc.setWorkingDirectory(os.path.expanduser("~"))
        proc.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
        out, err = self._out, self._err = _Output(), _Output()
        proc.readyReadStandardOutput.connect(lambda: out.add(proc.readAllStandardOutput().data()))
        proc.readyReadStandardError.connect(lambda: err.add(proc.readAllStandardError().data()))
        proc.finished.connect(lambda code, status: self._done(proc, code, status))
        proc.errorOccurred.connect(lambda error: self._failed(proc, error))
        self._proc = self._current[0] = proc
        proc.start(*argv)
        if self._stdin:
            proc.write(self._stdin.encode())
        if not self._interactive:
            proc.closeWriteChannel()      # EOF once `stdin` is written: `sort`, `wc`, `ssh` finish
        if self._timer.interval() == 0:
            self._live.start()
        if not was_running:
            self.runningChanged.emit()

    @Slot()
    def _publish_live(self):
        if self._out.fresh or self._err.fresh:
            self._stdout, self._stderr = self._out.text(), self._err.text()
            self._js.pop("lines", None)
            self.outputChanged.emit()

    def _done(self, proc: QProcess, code: int, status):
        if proc is not self._proc:
            return
        self._out.add(proc.readAllStandardOutput().data())
        self._err.add(proc.readAllStandardError().data())
        crashed = status == QProcess.ExitStatus.CrashExit
        self._finish(proc, -1 if crashed else code)

    def _failed(self, proc: QProcess, err):
        if proc is not self._proc or err != QProcess.ProcessError.FailedToStart:
            return
        if shutil.which(proc.program()) is None:   # the shell's words and exit code
            self._err.add(f"{proc.program()}: command not found\n".encode())
            self._finish(proc, 127)
        else:
            self._err.add(f"{proc.program()}: {proc.errorString()}\n".encode())
            self._finish(proc, -1)

    def _finish(self, proc: QProcess, code: int):
        self._proc = self._current[0] = None
        self._live.stop()
        proc.deleteLater()
        self._out.add(b"", final=True)
        self._err.add(b"", final=True)
        self._set_result(self._out.text(), self._err.text(), code)
        self.runningChanged.emit()
        self.finished.emit(code, self._stdout)

    def _set_result(self, out: str, err: str, code: int):
        self._stdout, self._stderr, self._exit_code = out, err, code
        try:
            self._json = json.loads(out) if out.strip() else None
        except ValueError:
            self._json = None
        self._js.pop("lines", None)
        self._js.pop("json", None)
        self.outputChanged.emit()

    def _stop(self, emit: bool = True):
        proc, self._proc, self._current[0] = self._proc, None, None
        self._live.stop()
        if proc is not None:
            _kill(proc)
            proc.deleteLater()
            if emit:
                self.runningChanged.emit()

    @Slot()
    def kill(self):
        self._pending = False
        self._stop()

    @Slot(str)
    def write(self, text: str):
        """Send `text` to a running program's stdin; only with `interactive: true` (else stdin is closed)."""
        if not self._interactive:
            print("Command.write: stdin is closed; set `interactive: true` to write to a running program")
        elif self._proc is not None:
            self._proc.write(text.encode())

    def _get_running(self):
        return self._proc is not None or self._pending

    def _set_running(self, value: bool):
        if value and not self._get_running():
            if self._ready:
                self.run()
            else:
                self._pending = True
                self.runningChanged.emit()
        elif not value and self._get_running():
            self.kill()

    running = Property(bool, _get_running, _set_running, notify=runningChanged)

    def _get_interval(self):
        return self._timer.interval()

    def _set_interval(self, ms: int):
        ms = max(0, int(ms))
        if 0 < ms < 100:
            ms = 100
        if ms == self._timer.interval():
            return
        self._timer.setInterval(ms)
        if ms == 0:
            self._timer.stop()
        elif self._ready:
            self._timer.start()
        self.intervalChanged.emit()

    interval = Property(int, _get_interval, _set_interval, notify=intervalChanged)

    def _set_command(self, value: str):
        if value != self._command:
            self._command = value
            self.commandChanged.emit()

    def _set_program(self, value: str):
        if value != self._program:
            self._program = value
            self.programChanged.emit()

    def _set_stdin(self, value: str):
        if value != self._stdin:
            self._stdin = value
            self.stdinChanged.emit()

    def _set_interactive(self, value: bool):
        if value != self._interactive:
            self._interactive = value
            self.interactiveChanged.emit()

    def _set_args(self, value):
        value = [str(a) for a in (from_js(value) or [])]
        if value != self._args:
            self._args = value
            self._js.pop("args", None)
            self.argsChanged.emit()

    def _cached(self, name: str, make):
        if name not in self._js:
            self._js[name] = js_value(self, make())
        return self._js[name]

    command = Property(str, lambda self: self._command, _set_command, notify=commandChanged)
    program = Property(str, lambda self: self._program, _set_program, notify=programChanged)
    args = Property("QVariant", lambda self: self._cached("args", lambda: self._args), _set_args,
                    notify=argsChanged)
    stdin = Property(str, lambda self: self._stdin, _set_stdin, notify=stdinChanged)
    interactive = Property(bool, lambda self: self._interactive, _set_interactive, notify=interactiveChanged)
    stdout = Property(str, lambda self: self._stdout, notify=outputChanged)
    stderr = Property(str, lambda self: self._stderr, notify=outputChanged)
    exitCode = Property(int, lambda self: self._exit_code, notify=outputChanged)
    lines = Property("QVariant", lambda self: self._cached("lines", self._stdout.splitlines),
                     notify=outputChanged)
    json = Property("QVariant", lambda self: self._cached("json", lambda: self._json), notify=outputChanged)


def register(ctx: AppContext) -> None:
    from PySide6.QtQml import qmlRegisterType

    qmlRegisterType(Command, URI, MAJOR, MINOR, "Command")
