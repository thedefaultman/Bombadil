"""bin/bombadil-mail-host: the native-messaging host, run as the real program between a fake service and pipes.

The service's side is a Unix socket in a temp dir that the test listens on (BOMBADIL_MAIL_SOCKET points the host
at it); Thunderbird's side is the host's stdin and stdout, written and read here in native-messaging frames. Every
wait has a deadline, so a host that hangs fails a test instead of stopping the run, and the host's stderr goes to
a file so it can never block on a full pipe. Nothing here leaves the temp dir or touches a real mail service.

What the file holds the host to: it says hello first and relays both ways without changing a line's bytes; it
passes a frame of 999 999 bytes and refuses one of 1 000 000 (to Thunderbird: it kills the port above 1 MiB);
it keeps nothing for a service that is faster than Thunderbird; and it ends cleanly when either side goes, with
a non-zero status only for a stream that cannot be read.
"""

import importlib.machinery
import importlib.util
import json
import os
import select
import signal
import socket
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
    """The host as a process, with the fake service's socket and Thunderbird's pipes."""

    def __init__(self, tmp_path: Path, serve: bool = True):
        self.sock_path = tmp_path / "mail.sock"
        self.err_path = tmp_path / "host.err"
        self.server = None
        if serve:
            self.listen()
        env = {**os.environ, "BOMBADIL_MAIL_SOCKET": str(self.sock_path), "HOME": str(tmp_path)}
        with open(self.err_path, "wb") as err:
            self.proc = subprocess.Popen([str(HOST), "/ignored/manifest.json", "bombadil-mail@bombadil.local"],
                                         env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
        self.service: Line | None = None

    def listen(self) -> None:
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.sock_path))
        self.server.listen(4)

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

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except (OSError, ValueError):
                pass
        if self.server is not None:
            self.server.close()
        if self.service is not None:
            self.service.conn.close()


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
    assert "limit" in host.stderr()
    service.conn.sendall(b'{"id":8,"op":"info"}\n')
    assert json.loads(host.frame()) == {"id": 8, "op": "info"}, "the host goes on after refusing a line"


def test_a_huge_line_is_thrown_away_without_being_kept(connected):
    host, service = connected
    big = b'{"id":"x9","pad":"' + b"a" * (9 << 20) + b'"}\n'
    sender = threading.Thread(target=service.conn.sendall, args=(big,), daemon=True)
    sender.start()
    err = json.loads(host.frame(30))
    assert err["event"] == "host_error" and "id" not in err and "limit" in err["error"]
    sender.join(10)
    service.conn.sendall(b'{"after":1}\n')
    assert json.loads(host.frame()) == {"after": 1}


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


def test_a_hello_that_cannot_be_sent_ends_the_host_with_zero_not_with_a_traceback(monkeypatch, capsys):
    """The one moment a subprocess test cannot hit twice running: the service gone between connect and hello."""
    loader = importlib.machinery.SourceFileLoader("bombadil_mail_host_unit", str(HOST))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)

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
    assert "bad frame" in host.stderr()


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
        assert "cannot reach" in h.stderr()
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
