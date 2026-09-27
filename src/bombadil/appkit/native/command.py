"""`Command`: run a program from QML without blocking the UI (QProcess underneath).

Output properties change when a run finishes, so a poller never shows half an answer;
until then they keep the previous run's values. A program that keeps running (a `tail -f`)
shows its output as it arrives.
"""

import json
import os
import shlex
import shutil
import signal

from PySide6.QtCore import Property, QObject, QProcess, QTimer, Signal, Slot

from ..context import AppContext
from . import MAJOR, MINOR, URI
from .files import from_js, js_value


def _decode(data) -> str:
    return bytes(data).decode(errors="replace")


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
        self._proc: QProcess | None = None
        self._out = bytearray()
        self._err = bytearray()
        self._ready = False       # QML has set every property; before that `running: true` waits
        self._pending = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # A program that keeps running shows its output as it comes, a few times a second.
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
            cmd = self._command
            if extra:
                cmd += " " + " ".join(shlex.quote(a) for a in extra)
            return "/bin/sh", ["-c", cmd]
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
        proc.readyReadStandardOutput.connect(lambda: self._out.extend(proc.readAllStandardOutput().data()))
        proc.readyReadStandardError.connect(lambda: self._err.extend(proc.readAllStandardError().data()))
        proc.finished.connect(lambda code, status: self._done(proc, code, status))
        proc.errorOccurred.connect(lambda err: self._failed(proc, err))
        self._out, self._err = bytearray(), bytearray()
        self._proc = self._current[0] = proc
        proc.start(*argv)
        self._live.start()
        if not was_running:
            self.runningChanged.emit()

    @Slot()
    def _publish_live(self):
        out, err = _decode(self._out), _decode(self._err)
        if (out or err) and (out, err) != (self._stdout, self._stderr):
            self._stdout, self._stderr = out, err
            self._js.pop("lines", None)
            self.outputChanged.emit()

    def _done(self, proc: QProcess, code: int, status):
        if proc is not self._proc:
            return
        self._out.extend(proc.readAllStandardOutput().data())
        self._err.extend(proc.readAllStandardError().data())
        crashed = status == QProcess.ExitStatus.CrashExit
        self._finish(proc, -1 if crashed else code)

    def _failed(self, proc: QProcess, err):
        if proc is not self._proc or err != QProcess.ProcessError.FailedToStart:
            return
        if shutil.which(proc.program()) is None:   # the shell's words and exit code
            self._err.extend(f"{proc.program()}: command not found\n".encode())
            self._finish(proc, 127)
        else:
            self._err.extend(f"{proc.program()}: {proc.errorString()}\n".encode())
            self._finish(proc, -1)

    def _finish(self, proc: QProcess, code: int):
        self._proc = self._current[0] = None
        self._live.stop()
        proc.deleteLater()
        self._set_result(_decode(self._out), _decode(self._err), code)
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
        if self._proc is not None:
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
    stdout = Property(str, lambda self: self._stdout, notify=outputChanged)
    stderr = Property(str, lambda self: self._stderr, notify=outputChanged)
    exitCode = Property(int, lambda self: self._exit_code, notify=outputChanged)
    lines = Property("QVariant", lambda self: self._cached("lines", self._stdout.splitlines),
                     notify=outputChanged)
    json = Property("QVariant", lambda self: self._cached("json", lambda: self._json), notify=outputChanged)


def register(ctx: AppContext) -> None:
    from PySide6.QtQml import qmlRegisterType

    qmlRegisterType(Command, URI, MAJOR, MINOR, "Command")
