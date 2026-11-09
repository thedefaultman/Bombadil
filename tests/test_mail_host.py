"""bin/bombadil-mail-host: the native-messaging host, run as the real program between a fake service and pipes.

The service's side is a Unix socket in a temp dir that the test listens on (BOMBADIL_MAIL_SOCKET points the host
at it); Thunderbird's side is the host's stdin and stdout, written and read here in native-messaging frames. Every
wait has a deadline, so a host that hangs fails a test instead of stopping the run, and the host's stderr goes to
a file so it can never block on a full pipe, and the host's own log (`mail-host.log`) is in the temp dir's state
directory. Nothing here leaves the temp dir or touches a real mail service.

What the file holds the host to: it says hello first and relays both ways without changing a line's bytes; it
passes a frame of 999 999 bytes and refuses one of 1 000 000 (to Thunderbird: it kills the port above 1 MiB);
it keeps nothing for a service that is faster than Thunderbird; it ends cleanly when either side goes, with
a non-zero status only for a stream that cannot be read (or a service that was never there); and what it says
on stderr or in its log can never decide whether it ends.
"""

import importlib.machinery
import importlib.util
import json
import os
import select
import signal
import socket
import stat
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1] / "bin" / "bombadil-mail-host"
WAIT = 10.0


def read_n(fd: int, n: int, timeout: float = WAIT) -> bytes:
    """Exactly n bytes from a pipe, or an AssertionError at the deadline (b"" at end of input)."""
    deadline = time.monotonic() + timeout
    buf = b""
    while len(buf) < n:
        left = deadline - time.monotonic()
        assert left > 0, f"timed out with {len(buf)} of {n} bytes"
        if not select.select([fd], [], [], left)[0]:
            continue
        chunk = os.read(fd, min(1 << 20, n - len(buf)))
        if not chunk:
            return buf
        buf += chunk
    return buf


class Line:
    """Lines from the service's end of the socket."""

    def __init__(self, conn: socket.socket):
        self.conn = conn
        self.buf = b""

    def next(self, timeout: float = WAIT) -> bytes | None:
        """The next line without its newline, None when the host hung up, an AssertionError at the deadline."""
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            assert left > 0, "timed out waiting for a line"
            self.conn.settimeout(left)
            try:
                chunk = self.conn.recv(1 << 20)
            except TimeoutError:
                continue
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line


class Host:
    """The host as a process, with the fake service's socket and Thunderbird's pipes. `stderr` is a descriptor to
    give it in place of the file; `stdin` is what Thunderbird's end is (a pipe the test writes to by default)."""

    def __init__(self, tmp_path: Path, serve: bool = True, stderr: int | None = None, stdin=subprocess.PIPE,
                 stuck: bool = False):
        self.sock_path = tmp_path / "mail.sock"
        self.err_path = tmp_path / "host.err"
        self.log_path = tmp_path / "state" / "mail-host.log"
        self.server = None
        if serve or stuck:
            self.listen(0, fill=True) if stuck else self.listen()
        env = {**os.environ, "BOMBADIL_MAIL_SOCKET": str(self.sock_path), "HOME": str(tmp_path),
               "BOMBADIL_STATE": str(tmp_path / "state")}
        with open(self.err_path, "wb") as err:
            self.proc = subprocess.Popen([str(HOST), "/ignored/manifest.json", "bombadil-mail@bombadil.local"],
                                         env=env, stdin=stdin, stdout=subprocess.PIPE,
                                         stderr=err if stderr is None else stderr)
        self.service: Line | None = None

    def listen(self, backlog: int = 4, fill: bool = False) -> None:
        """Start listening. `fill` queues connections nobody accepts until the queue is full, which is what a
        service whose loop is stuck looks like from outside: connecting to it neither succeeds nor fails."""
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.sock_path))
        self.server.listen(backlog)
        self.fillers: list[socket.socket] = []
        while fill:
            filler = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            filler.settimeout(0.3)
            try:
                filler.connect(str(self.sock_path))
            except OSError:
                filler.close()
                break
            self.fillers.append(filler)

    def accept(self) -> Line:
        self.server.settimeout(WAIT)
        conn, _ = self.server.accept()
        self.service = Line(conn)
        return self.service

    # Thunderbird's side
    def frame(self, timeout: float = WAIT) -> bytes | None:
        """The body of the next frame the host wrote, None at the end of its output."""
        fd = self.proc.stdout.fileno()
        head = read_n(fd, 4, timeout)
        if not head:
            return None
        (size,) = struct.unpack("=I", head)
        body = read_n(fd, size, timeout)
        assert len(body) == size, "a frame cut short"
        return body

    def write(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send_frame(self, obj) -> None:
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.write(struct.pack("=I", len(body)) + body)

    def wait(self, timeout: float = WAIT) -> int:
        return self.proc.wait(timeout)

    def stderr(self) -> str:
        return self.err_path.read_text(errors="replace")

    def log(self) -> str:
        return self.log_path.read_text(errors="replace") if self.log_path.exists() else ""

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                if f is not None:
                    f.close()
            except (OSError, ValueError):
                pass
        if self.server is not None:
            self.server.close()
        if self.service is not None:
            self.service.conn.close()
        for filler in getattr(self, "fillers", []):
            filler.close()


@pytest.fixture
def host(tmp_path):
    h = Host(tmp_path)
    try:
        yield h
    finally:
        h.close()


@pytest.fixture
def connected(host):
    """A host that has connected and said hello, which is read here."""
    service = host.accept()
    hello = service.next()
    assert json.loads(hello)["op"] == "engine_hello"
    return host, service


def line_of(size: int, **fields) -> bytes:
    """A JSON object line of exactly `size` bytes, padded in a string field."""
    base = json.dumps({**fields, "pad": ""}, separators=(",", ":"), ensure_ascii=False).encode()
    pad = size - len(base)
    assert pad >= 0
    line = base[:-2] + b"a" * pad + base[-2:]
    assert len(line) == size
    return line


# -- hello and relay --

def test_the_host_says_hello_first_with_its_own_pid(host):
    service = host.accept()
    assert json.loads(service.next()) == {"op": "engine_hello", "pid": host.proc.pid}


def test_frames_from_the_addon_are_lines_for_the_service_and_back(connected):
    host, service = connected
    host.send_frame({"event": "hello", "version": "1", "caps": ["a", "b"]})
    assert json.loads(service.next()) == {"event": "hello", "version": "1", "caps": ["a", "b"]}
    service.conn.sendall(b'{"id":1,"op":"info"}\n')
    assert json.loads(host.frame()) == {"id": 1, "op": "info"}
    host.send_frame({"id": 1, "ok": True, "result": {"version": "157.0"}})
    assert json.loads(service.next()) == {"id": 1, "ok": True, "result": {"version": "157.0"}}


def test_text_stays_text_on_the_line_and_a_lines_bytes_are_the_frames(connected):
    host, service = connected
    text = "Grüße 日本語 ☃"
    host.send_frame({"subject": text})
    line = service.next()
    assert text.encode() in line, "the line has the characters, not six bytes of escape for each"
    body = ('{"id": 2,  "subject": "' + text + '"}').encode()   # odd spacing on purpose: it is not rewritten
    service.conn.sendall(body + b"\n")
    assert host.frame() == body


def test_a_lone_surrogate_in_a_frame_does_not_end_the_host(connected):
    host, service = connected
    host.send_frame(b'{"subject":"half \\ud83d of an emoji"}')
    line = json.loads(service.next())
    assert line["subject"] == "half \ud83d of an emoji"
    host.send_frame({"after": 1})
    assert json.loads(service.next()) == {"after": 1}
    assert host.proc.poll() is None


def test_lines_split_and_joined_by_the_network_are_still_whole_frames(connected):
    host, service = connected
    service.conn.sendall(b'{"a":1}\n{"b"')
    assert json.loads(host.frame()) == {"a": 1}
    service.conn.sendall(b':2}\n\n\r\n{"c":3}\r\n')
    assert json.loads(host.frame()) == {"b": 2}
    assert json.loads(host.frame()) == {"c": 3}, "empty lines are nothing, and a carriage return is not part of a line"


def test_a_large_frame_from_the_addon_is_one_line(connected):
    host, service = connected
    host.send_frame({"event": "blob", "data": "x" * (5 << 20)})
    line = json.loads(service.next())
    assert len(line["data"]) == 5 << 20


# -- the limit on what goes to Thunderbird --

def test_a_line_of_999999_bytes_passes_and_one_of_a_million_does_not(connected):
    host, service = connected
    ok = line_of(999_999, id=5, op="send")
    service.conn.sendall(ok + b"\n")
    assert host.frame() == ok
    too_big = line_of(1_000_000, id=7, op="send")
    service.conn.sendall(too_big + b"\n")
    err = json.loads(host.frame())
    assert err["event"] == "host_error" and err["id"] == 7 and "1000000" in err["error"].replace(" ", "")
    assert "limit" in host.stderr() and "limit" in host.log(), "said where somebody can read it"
    answer = json.loads(service.next())
    assert answer["id"] == 7 and answer["ok"] is False and answer["code"] == "too_big", \
        "the service asked, and is told at once that this did not go, not left to time out"
    assert "." in answer["error"] and "\n" not in answer["error"]
    service.conn.sendall(b'{"id":8,"op":"info"}\n')
    assert json.loads(host.frame()) == {"id": 8, "op": "info"}, "the host goes on after refusing a line"


def test_a_huge_line_is_thrown_away_without_being_kept(connected):
    host, service = connected
    big = b'{"id":"x9","pad":"' + b"a" * (9 << 20) + b'"}\n'
    sender = threading.Thread(target=service.conn.sendall, args=(big,), daemon=True)
    sender.start()
    err = json.loads(host.frame(30))
    assert err["event"] == "host_error" and "id" not in err and "limit" in err["error"]
    assert f"a line of {len(big) - 1} bytes" in err["error"], "the whole size of what was thrown away, not its tail"
    sender.join(10)
    service.conn.sendall(b'{"after":1}\n')
    assert json.loads(host.frame()) == {"after": 1}
    assert host.proc.poll() is None


def test_a_line_that_is_not_an_object_is_refused_not_framed(connected):
    host, service = connected
    service.conn.sendall(b"not json at all\n")
    err = json.loads(host.frame())
    assert err["event"] == "host_error" and "id" not in err
    service.conn.sendall(b'{"ok":1}\n')
    assert json.loads(host.frame()) == {"ok": 1}


# -- nothing piles up --

def vm_rss_mb(pid: int) -> float:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    raise AssertionError("no VmRSS")


def test_a_service_faster_than_thunderbird_waits_instead_of_filling_the_host(connected):
    """100 frames of 900 KB go out while nobody reads the host's stdout: the host's memory stays flat, the other
    direction still works while the first is held up, and when the reading starts every frame arrives, in order."""
    host, service = connected
    count, size = 100, 900_000
    lines = [line_of(size, id=i, op="x") for i in range(count)]

    def feed():
        try:
            for line in lines:
                service.conn.sendall(line + b"\n")
        except OSError:
            pass

    threading.Thread(target=feed, daemon=True).start()
    time.sleep(1.5)
    assert vm_rss_mb(host.proc.pid) < 60, "the host is keeping what it cannot deliver"
    host.send_frame({"event": "new_mail"})
    assert json.loads(service.next())["event"] == "new_mail"
    for i in range(count):
        body = host.frame(30)
        assert body == lines[i], f"frame {i} was lost or changed"
    assert vm_rss_mb(host.proc.pid) < 60


# -- how it ends --

def test_end_of_input_from_thunderbird_ends_the_host_with_zero_and_closes_the_socket(connected):
    host, service = connected
    host.proc.stdin.close()
    assert host.wait() == 0
    assert service.next() is None, "the socket is closed"


def test_the_service_hanging_up_ends_the_host_with_zero(connected):
    host, service = connected
    service.conn.close()
    assert host.wait() == 0


def test_the_service_hanging_up_mid_line_ends_it_too(connected):
    host, service = connected
    service.conn.sendall(b'{"half":')
    service.conn.close()
    assert host.wait() == 0


def test_a_service_that_dies_with_something_unread_resets_the_connection_and_the_host_still_ends_with_zero(connected):
    host, service = connected
    host.send_frame({"event": "hello"})              # sent to the service, which never reads it
    service.conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    service.conn.close()                              # a reset, not a hang-up
    assert host.wait() == 0, host.stderr()


def test_a_service_that_hangs_up_before_reading_the_hello_ends_it_with_zero(host):
    service = host.accept()
    service.conn.close()
    assert host.wait() == 0, host.stderr()


@pytest.fixture
def module(tmp_path, monkeypatch):
    """The host's own functions, loaded in this process (its state directory is the test's)."""
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "state"))
    loader = importlib.machinery.SourceFileLoader("bombadil_mail_host_unit", str(HOST))
    loaded = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(loaded)
    return loaded


def test_a_hello_that_cannot_be_sent_ends_the_host_with_zero_not_with_a_traceback(module, monkeypatch, capsys):
    """The one moment a subprocess test cannot hit twice running: the service gone between connect and hello."""
    def leave(code):
        raise SystemExit(code)

    monkeypatch.setattr(module, "leave", leave)
    here, there = socket.socketpair()
    there.close()
    with here, pytest.raises(SystemExit) as ended:
        module.greet(here)
    assert ended.value.code == 0 and "went away" in capsys.readouterr().err


def test_sigterm_ends_the_host_with_zero(connected):
    host, _ = connected
    host.proc.send_signal(signal.SIGTERM)
    assert host.wait() == 0


def test_a_closed_stdout_ends_it_with_zero(connected):
    host, service = connected
    host.proc.stdout.close()
    service.conn.sendall(b'{"a":1}\n')
    assert host.wait() == 0


@pytest.mark.parametrize("raw, why", [
    (b"\xff\xff\xff\xff", "a length of four gigabytes"),
    (b"\x00\x00\x00\x05" + b"x" * 5, "a body that is not JSON"),
    (b"\x02\x00\x00\x00[]", "a JSON array, not an object"),
    (b"\x10\x00\x00\x00{\"a\":", "a body cut short by the end of input"),
    (b"\x10\x00", "a length prefix cut short by the end of input"),
])
def test_a_stream_that_cannot_be_read_ends_the_host_with_a_status_and_no_hang(connected, raw, why):
    host, _ = connected
    host.write(raw)
    host.proc.stdin.close()
    assert host.wait(5) == 1, why
    assert "bad frame" in host.stderr() and "bad frame" in host.log()


def test_a_length_prefix_alone_over_the_limit_ends_it_while_stdin_stays_open(connected):
    host, _ = connected
    host.write(struct.pack("=I", 100 << 20))   # more than the 64 MiB a frame may be: refused without waiting for it
    assert host.wait(5) == 1


def test_a_frame_of_the_largest_size_the_addon_may_send_is_read(connected):
    """Not the full 64 MiB (the point is that a long frame is a loop, not a hang), but one far over a megabyte."""
    host, service = connected
    host.send_frame({"data": "y" * (20 << 20)})
    assert len(json.loads(service.next(30))["data"]) == 20 << 20


# -- before the service is there --

def test_a_service_that_starts_a_moment_late_is_waited_for(tmp_path):
    h = Host(tmp_path, serve=False)
    try:
        time.sleep(1.0)
        h.listen()
        service = h.accept()
        assert json.loads(service.next())["op"] == "engine_hello"
    finally:
        h.close()


def test_no_service_at_all_ends_the_host_non_zero_after_a_few_seconds(tmp_path):
    h = Host(tmp_path, serve=False)
    try:
        started = time.monotonic()
        assert h.wait(15) == 2
        assert 3 < time.monotonic() - started < 12, "it waited, and not forever"
        assert "cannot reach" in h.stderr() and "cannot reach" in h.log(), "the one case nobody is told of otherwise"
    finally:
        h.close()


def test_sigterm_while_waiting_for_the_service_ends_it(tmp_path):
    h = Host(tmp_path, serve=False)
    try:
        time.sleep(0.5)
        h.proc.send_signal(signal.SIGTERM)
        assert h.wait(5) == 0
    finally:
        h.close()


# -- stdout --

def test_fd_one_is_stderr_and_the_frames_have_a_descriptor_of_their_own(connected):
    """What a stray print would write to is stderr, not the stream Thunderbird parses."""
    host, _ = connected
    fd1 = os.stat(f"/proc/{host.proc.pid}/fd/1")
    fd2 = os.stat(f"/proc/{host.proc.pid}/fd/2")
    assert (fd1.st_dev, fd1.st_ino) == (fd2.st_dev, fd2.st_ino)
    mine = os.stat(f"/proc/{host.proc.pid}/fd/1")
    frames = [os.stat(f"/proc/{host.proc.pid}/fd/{n}") for n in os.listdir(f"/proc/{host.proc.pid}/fd")
              if n.isdigit() and int(n) > 2]
    assert any(s.st_mode & 0o170000 == 0o010000 and (s.st_dev, s.st_ino) != (mine.st_dev, mine.st_ino)
               for s in frames), "the frames' pipe is held on another descriptor"


def test_only_frames_reach_stdout_whatever_the_host_says_on_stderr(connected):
    host, service = connected
    service.conn.sendall(b"garbage\n")          # makes the host say something on stderr
    assert json.loads(host.frame())["event"] == "host_error"
    host.proc.stdin.close()
    rest = read_n(host.proc.stdout.fileno(), 1 << 20)
    assert rest == b"", "after the one frame, nothing but the end of the stream"


# -- a service that is there but does not answer, and a Thunderbird that has gone meanwhile --

def test_a_service_that_never_accepts_is_no_service_and_the_host_gives_up_when_it_said_it_would(tmp_path):
    h = Host(tmp_path, stuck=True)
    try:
        started = time.monotonic()
        assert h.wait(12) == 2, "a connect that has no end is no wait at all"
        assert time.monotonic() - started < 9
        assert "cannot reach" in h.log()
    finally:
        h.close()


def test_a_host_whose_thunderbird_has_gone_before_the_service_appears_ends_with_zero_at_once(tmp_path):
    h = Host(tmp_path, serve=False, stdin=subprocess.DEVNULL)
    try:
        started = time.monotonic()
        assert h.wait(4) == 0, "not 2, after five seconds of waiting for a service nobody is left to talk to"
        assert time.monotonic() - started < 3
        assert "closed the port" in h.log()
    finally:
        h.close()


def test_a_host_whose_thunderbird_has_gone_does_not_introduce_itself_to_a_service_that_is_there(tmp_path):
    h = Host(tmp_path, stdin=subprocess.DEVNULL)
    try:
        assert h.wait(5) == 0
        h.server.settimeout(0.5)
        try:
            conn, _ = h.server.accept()
        except TimeoutError:
            return
        with conn:
            assert Line(conn).next(2) is None, "an engine_hello would be taken for a Thunderbird that is there"
    finally:
        h.close()


def test_what_the_addon_posted_while_there_was_no_service_is_still_there_when_there_is_one(tmp_path):
    """The add-on does not know that the host has no service: its first frame is posted at once, and looking at stdin
    to see whether Thunderbird is still there must not take it."""
    h = Host(tmp_path, serve=False)
    try:
        h.send_frame({"event": "hello", "version": "1"})
        time.sleep(1.0)
        h.listen()
        service = h.accept()
        assert json.loads(service.next())["op"] == "engine_hello"
        assert json.loads(service.next()) == {"event": "hello", "version": "1"}
    finally:
        h.close()


# -- what the host says can never decide what it does --

def closed_pipe() -> int:
    """The write end of a pipe whose read end is gone: a stderr that nobody can write to."""
    r, w = os.pipe()
    os.close(r)
    return w


def test_a_stderr_that_cannot_be_written_to_does_not_keep_the_host_from_ending_when_the_service_goes(tmp_path):
    w = closed_pipe()
    h = Host(tmp_path, stderr=w)
    os.close(w)
    try:
        service = h.accept()
        assert json.loads(service.next())["op"] == "engine_hello"
        service.conn.close()
        assert h.wait(5) == 0, "the add-on would be left with a host that hears nothing and does not say so"
        assert "closed the connection" in h.log()
    finally:
        h.close()


def test_a_stderr_that_cannot_be_written_to_does_not_keep_it_from_ending_when_thunderbird_goes(tmp_path):
    w = closed_pipe()
    h = Host(tmp_path, stderr=w)
    os.close(w)
    try:
        service = h.accept()
        assert json.loads(service.next())["op"] == "engine_hello"
        h.proc.stdin.close()
        assert h.wait(5) == 0
    finally:
        h.close()


def test_a_stderr_that_is_full_does_not_hold_the_host_up(tmp_path):
    """Thunderbird reads a host's stderr, but nothing here depends on it: with the pipe full, the line is lost."""
    r, w = os.pipe()
    filler = os.open(f"/proc/self/fd/{w}", os.O_WRONLY | os.O_NONBLOCK)     # another description of the same pipe
    try:
        while True:
            os.write(filler, b"x")
    except BlockingIOError:
        pass
    os.close(filler)
    h = Host(tmp_path, stderr=w)
    os.close(w)
    try:
        service = h.accept()
        assert json.loads(service.next())["op"] == "engine_hello"
        service.conn.close()
        assert h.wait(5) == 0
        assert "closed the connection" in h.log()
    finally:
        os.close(r)
        h.close()


def test_the_log_is_private_capped_and_rotated_and_failing_to_write_it_is_nothing(module, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "LOG_CAP", 600)
    for i in range(30):
        module.note(f"line {i} " + "x" * 80)
    log = tmp_path / "state" / "mail-host.log"
    assert 0 < log.stat().st_size <= 600 and (tmp_path / "state" / "mail-host.log.1").exists()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600 and stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    assert "line 29" in log.read_text() and not (tmp_path / "state" / "mail-host.log.2").exists()
    (tmp_path / "elsewhere").mkdir()
    log.unlink()
    log.symlink_to(tmp_path / "elsewhere" / "target")
    module.note("through a link")                    # refused (O_NOFOLLOW), silently
    assert not (tmp_path / "elsewhere" / "target").exists()
    monkeypatch.setenv("BOMBADIL_STATE", str(log))     # a state directory that is a file
    module.note("nowhere")
    module.say("still says it on stderr")


# -- a line over the limit, whatever it is --

def test_a_line_nested_deeper_than_the_parser_goes_is_refused_with_its_id_and_the_host_lives(connected):
    host, service = connected
    deep = b'{"id":4,"a":' + b"[" * 600_000 + b"]" * 600_000 + b"}"
    assert len(deep) >= 1_000_000
    service.conn.sendall(deep + b"\n")
    err = json.loads(host.frame(30))
    assert err["event"] == "host_error" and err["id"] == 4
    assert json.loads(service.next())["id"] == 4
    service.conn.sendall(b'{"id":5,"op":"info"}\n')
    assert json.loads(host.frame()) == {"id": 5, "op": "info"} and host.proc.poll() is None


@pytest.mark.parametrize("line_id, wanted", [(b'"k-12"', "k-12"), (b"-9", -9), (b"31", 31)])
def test_an_oversize_line_that_is_not_json_still_gives_its_id_from_the_front(connected, line_id, wanted):
    host, service = connected
    service.conn.sendall(b'{"op":"send","id":' + line_id + b',"pad":"' + b"a" * 1_100_000 + b"\n")   # cut short
    assert json.loads(host.frame(30))["id"] == wanted
    assert json.loads(service.next())["id"] == wanted


def test_an_id_that_is_not_a_number_or_a_string_or_is_far_too_long_to_be_the_services_is_no_id(connected):
    host, service = connected
    for line in (b'{"id":true,"x":"' + b"a" * 1_000_000 + b'"}', b'{"id":[1],"x":"' + b"a" * 1_000_000 + b'"}',
                 b'{"id":"' + b"i" * 300 + b'","x":"' + b"a" * 1_000_000 + b'"}'):
        service.conn.sendall(line + b"\n")
        assert "id" not in json.loads(host.frame(30))
    service.conn.sendall(b'{"done":1}\n')
    assert json.loads(host.frame()) == {"done": 1}


def test_refusing_lines_while_the_addon_talks_never_breaks_a_line_in_two(connected):
    """Two threads write to the service (the add-on's frames and the answers to refused lines); each line is one
    write, so every line that arrives is whole."""
    host, service = connected
    frames, refused = 120, 40

    def addon():
        for i in range(frames):
            host.send_frame({"event": "blob", "seq": i, "data": "z" * 150_000})

    def svc():
        for i in range(refused):
            service.conn.sendall(line_of(1_000_000, id=1000 + i, op="send") + b"\n")

    threads = [threading.Thread(target=addon, daemon=True), threading.Thread(target=svc, daemon=True)]
    drained = threading.Thread(target=lambda: [host.frame(60) for _ in range(refused)], daemon=True)
    for t in threads + [drained]:
        t.start()
    got = [json.loads(service.next(60)) for _ in range(frames + refused)]
    assert sorted(g["id"] for g in got if "id" in g) == list(range(1000, 1000 + refused))
    assert sorted(g["seq"] for g in got if "seq" in g) == list(range(frames))
