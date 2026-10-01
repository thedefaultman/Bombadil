"""bombadil-brain: the service between the watcher, the logs and everyone who asks.

Each test runs a real Brain on its own thread over a temp home, with a fake watcher (a Unix
socket that says hello and sends what the test gives it) where a watcher is needed, and asks
it through client.py, the way agentd, the launcher and the CLI do.
"""

import asyncio
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from bombadil import paths
from bombadil.brain import client, service

ROOT = Path(__file__).resolve().parents[1]
TURN_UNIT = "bombadil-turn-99-3-1727429990"
TURN_CG = f"/user.slice/user-1000.slice/user@1000.service/app.slice/{TURN_UNIT}.scope"
YOU_CG = "/user.slice/user-1000.slice/session-1.scope"
FOOT = [[5, "nvim", "nvim notes.md"], [4, "bash", "bash"], [3, "foot", "foot"], [2, "Hyprland", "Hyprland"]]
SH = [[9, "sh", "sh -c echo hi"], [8, "agentd", "agentd"]]
DESCRIPTION = "A small file made for a test."


def until(fn, timeout: float = 10.0, step: float = 0.05):
    """fn() once it is truthy; fails with its last value when it never is."""
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        try:
            value = fn()
        except (client.BrainUnavailable, client.BrainError) as e:
            value = e
        else:
            if value:
                return value
        time.sleep(step)
    raise AssertionError(f"never happened; last: {value!r}")


def ev(op, path, cgroup=YOU_CG, chain=FOOT, **extra):
    path = str(path)
    try:
        st = os.stat(path)
        ids = {"ino": st.st_ino, "size": st.st_size}
    except OSError:
        ids = {"ino": None, "size": None}
    return {"op": op, "t": time.time(), "path": path, "dir": False, **ids, "pid": chain[0][0], "uid": 1000,
            "comm": chain[0][1], "cgroup": cgroup, "chain": chain, "gone": False, **extra}


class FakeWatcher:
    """bombadil-brain-watch as the brain sees it: hello on connect, then JSON lines."""

    def __init__(self, path, watching: bool = True):
        self.path = str(path)
        self.hello = {"op": "hello", "v": 1, "watching": watching, "fs": "btrfs" if watching else "overlay",
                      "top": "/run/bombadil-brain/top" if watching else "",
                      "reason": "" if watching else "the root is not btrfs"}
        self.conns: list[socket.socket] = []
        self.lock = threading.Lock()
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            conn.sendall(json.dumps(self.hello).encode() + b"\n")
            with self.lock:
                self.conns.append(conn)

    def send(self, *events):
        until(lambda: self.conns)
        data = b"".join(json.dumps(e).encode() + b"\n" for e in events)
        with self.lock:
            for conn in self.conns:
                try:
                    conn.sendall(data)
                except OSError:
                    pass

    def drop(self):
        with self.lock:
            for conn in self.conns:
                conn.close()
            self.conns.clear()

    def close(self):
        self.srv.close()
        self.drop()


@pytest.fixture
def h(home):
    """The brain's home: a folder of its own, so the test's state and sockets are not in it."""
    d = home / "home"
    (d / "Documents").mkdir(parents=True)
    (d / "notes.md").write_text("old notes\n")
    return d


def make_brain(home, **kw):
    kw.setdefault("watch_path", home / "no-watcher.sock")
    kw.setdefault("describe_run", lambda prompt: DESCRIPTION)
    return service.Brain(home=str(home / "home"), xattrs=False, pacman_log=home / "pacman.log",
                         memory_files=[home / "home" / "memory.md"], history_profiles=[],
                         history_workdir=home / "run" / "brain-tmp", **kw)


@contextmanager
def running(brain, settle: bool = True):
    errors = []

    def main():
        try:
            asyncio.run(brain.serve())
        except BaseException as e:  # noqa: BLE001 - handed to the test
            errors.append(e)

    t = threading.Thread(target=main, daemon=True)
    t.start()
    try:
        until(lambda: errors or client.request("status"))
        if errors:
            raise errors[0]
        if settle and brain.walk:
            until(lambda: (lambda s: s["walking"] is None and s["indexed"])(client.request("status")))
        yield brain
    finally:
        try:
            brain.loop.call_soon_threadsafe(brain.stop)
        except (RuntimeError, AttributeError):
            pass
        t.join(10)
        assert not t.is_alive(), "the brain did not stop"
    if errors:
        raise errors[0]


def meta(key):
    db = sqlite3.connect(f"file:{paths.brain_db()}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    finally:
        db.close()
    return row[0] if row else None


def ask(op, **args):
    return client.request(op, timeout=5.0, **args)


def pushes(conn, kind, timeout=5.0):
    """Every push of `kind` that arrives within `timeout`."""
    out = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = conn.push(max(0.01, deadline - time.monotonic()))
        if p is not None and p.get("push") == kind:
            out.append(p)
    return out


def wait_push(conn, kind, match=lambda p: True, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = conn.push(max(0.01, deadline - time.monotonic()))
        if p is not None and p.get("push") == kind and match(p):
            return p
    raise AssertionError(f"no {kind} push")


# -- status and plain answers --

def test_status_says_what_it_knows(home, h):
    with running(make_brain(home)):
        s = ask("status")
        assert s["text"] == f"Knows {s['things']:,} things; not watching saves right now."
        assert s["things"] >= 3 and s["watching"] is False and s["walking"] is None


def test_status_words_while_walking_and_watching(home):
    b = make_brain(home)
    b._known = {"live": 12034}
    b.walking, b.progress = "first", 0.404
    assert b.status()["text"] == "Getting to know your files, 40%"
    b.progress = 1.0
    assert b.status()["text"] == "Getting to know your files, 99%"   # never 100% while still walking
    b.walking = "reconcile"
    assert b.status()["text"].startswith("Catching up on your files")
    b.walking = None
    b.watcher, b.watching = "here", True
    assert b.status()["text"] == "Knows 12,034 things; watching every save."
    b.watching = False
    assert b.status()["text"] == "Knows 12,034 things; saves are not watched on this system."
    b._known = {"live": 1}
    b.watcher, b.watching = "away", None
    assert b.status()["text"] == "Knows 1 thing; not watching saves right now."


def test_every_no_is_one_plain_sentence(home, h):
    with running(make_brain(home, walk=False)):
        with pytest.raises(client.BrainError, match="^The brain cannot answer “bogus”.$"):
            ask("bogus")
        with pytest.raises(client.BrainError, match="^Say which thing"):
            ask("focus")
        with pytest.raises(client.BrainError, match="^The brain does not know this yet.$"):
            ask("focus", ref=str(h / "nothing.txt"))
        assert ask("why", ref=str(h / "nothing.txt")) == "The brain does not know this yet."
        with pytest.raises(client.BrainError, match="takes no note"):
            ask("note", kind="lunch")
        with pytest.raises(client.BrainError, match="needs its number"):
            ask("note", kind="turn_start", n="seven")
        # Not JSON, not an object, far too long: an answer each, and the brain carries on.
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5)
            s.connect(str(paths.brain_socket()))
            f = s.makefile("rb")
            s.sendall(b"not json\n[1, 2]\n\n")
            assert json.loads(f.readline())["error"] == "That was not a request the brain understands."
            assert json.loads(f.readline())["ok"] is False
            s.sendall(b"x" * (service.LINE_LIMIT + 10) + b"\n")
            assert json.loads(f.readline())["error"] == "That request is too long."
        assert ask("status")["text"]


def test_search_recent_thing_and_children(home, h):
    (h / "Documents" / "lease-2026.pdf").write_bytes(b"%PDF-1.4 lease")
    with running(make_brain(home)):
        found = ask("search", q="lease")["items"]
        assert [i["title"] for i in found] == ["lease-2026.pdf"]
        assert found[0]["where"] == "Documents" and found[0]["when"]
        assert found[0]["ref"].endswith("lease-2026.pdf")
        # Under three letters: a prefix.
        assert ask("search", q="le")["items"][0]["title"] == "lease-2026.pdf"
        assert ask("search", q="   ")["items"] == []
        assert ask("search", q='" OR ') == {"items": []}
        thing = ask("thing", ref="~/notes.md")
        assert thing["path"] == str(h / "notes.md") and thing["ref"] == str(h / "notes.md")
        assert ask("thing", ref=thing["id"])["id"] == thing["id"]
        assert ask("thing", ref=str(thing["id"]))["id"] == thing["id"]
        kids = ask("children", ref=str(h), limit=1)
        assert kids["total"] == 2 and [i["name"] for i in kids["items"]] == ["Documents"]
        assert isinstance(ask("recent", limit=5)["items"], list)


# -- the launcher and the Brain app --

def test_show_pushes_and_is_remembered_for_a_while(home, h, monkeypatch):
    with running(make_brain(home, walk=False)):
        with client.Connection() as conn:
            assert conn.request("subscribe") == {"subscribed": True}
            shown = ask("show", ref=str(h / "notes.md"))
            assert shown["title"] == "notes.md" and shown["id"] is None
            p = wait_push(conn, "show")
            assert p["ref"] == str(h / "notes.md") and p["seq"] == shown["seq"]
        assert ask("requested")["ref"] == str(h / "notes.md")
        assert ask("show", ref="turn:41")["seq"] == shown["seq"] + 1
        monkeypatch.setattr(service, "REQUESTED_S", -1)
        assert ask("requested") is None   # stale: the Brain app opens on what it had instead


def test_show_numbers_never_go_back_when_the_brain_restarts(home, h):
    # The Brain app ignores a "show" whose number it has already seen, and it outlives the brain.
    with running(make_brain(home, walk=False)):
        first = ask("show", ref="turn:1")["seq"]
    time.sleep(0.01)
    with running(make_brain(home, walk=False)):
        assert ask("show", ref="turn:1")["seq"] > first


def test_refs_that_cannot_be_things_are_not_known(home, h):
    with running(make_brain(home, walk=False)):
        for ref in (10**30, "9" * 30, "\udcff/x"):   # past SQLite's integers; not UTF-8
            assert ask("why", ref=ref) == "The brain does not know this yet."
            for op in ("focus", "thing", "show", "describe"):
                with pytest.raises(client.BrainError, match="^The brain does not know this yet.$"):
                    ask(op, ref=ref)
        assert ask("status")["text"]


def test_focus_brings_a_description_but_never_for_private_things(home, h):
    (h / "Documents" / "passwords.txt").write_text("hunter2\n")
    with running(make_brain(home)):
        f = ask("focus", ref=str(h / "notes.md"))
        assert f["thing"]["title"] == "notes.md"
        assert f["description"] in ({"text": None, "stale": False, "pending": True},
                                    {"text": DESCRIPTION, "stale": False, "pending": False})
        until(lambda: (ask("describe", ref=str(h / "notes.md")) or {}).get("text") == DESCRIPTION)
        assert ask("focus", ref=str(h / "notes.md"))["description"]["text"] == DESCRIPTION
        secret = ask("focus", ref=str(h / "Documents" / "passwords.txt"))
        assert secret["thing"]["private"] is True and secret["description"] is None
        assert ask("describe", ref=str(h / "Documents" / "passwords.txt")) is None


# -- the logs --

def test_logs_are_read_at_start(home, h):
    made = h / "setup-wg.sh"
    made.write_text("#!/bin/sh\n")
    t = time.time() - 60
    row = {"n": 41, "t": t, "started": t - 5, "prompt": "install the VPN", "unit": TURN_UNIT,
           "provider": "claude", "ok": True, "summary": "Installed WireGuard.",
           "files": {"wrote": [str(made)], "read": []}}
    paths.state_dir().mkdir(parents=True)
    paths.turns_log().write_text(json.dumps(row) + "\n")
    (home / "pacman.log").write_text(
        "[2026-09-27T10:00:00+0000] [ALPM] installed wireguard-tools (1.0.20210914-3)\n")
    (h / "memory.md").write_text("- Daniel likes short answers\n")
    with running(make_brain(home)):
        assert ask("thing", ref="turn:41")["title"] == "install the VPN"
        assert ask("why", ref=str(made)).startswith("Made by the machine in turn 41, “install the VPN”")
        assert ask("thing", ref="package:wireguard-tools")["kind"] == "package"
        found = ask("search", q="short answers")["items"]
        assert [i["title"] for i in found] == ["Daniel likes short answers"]


def test_turn_end_reads_the_new_row(home, h):
    paths.state_dir().mkdir(parents=True)
    with running(make_brain(home, walk=False)):
        with pytest.raises(client.BrainError):
            ask("thing", ref="turn:2")
        with paths.turns_log().open("a") as f:
            f.write(json.dumps({"n": 2, "t": time.time(), "prompt": "tidy my downloads", "files": {}}) + "\n")
        assert ask("note", kind="turn_end", n=2) == {"noted": "turn_end"}
        assert ask("thing", ref="turn:2")["title"] == "tidy my downloads"


def test_without_a_watcher_the_logs_are_looked_at_once_a_minute(home, h, monkeypatch):
    monkeypatch.setattr(service, "LOOK_S", 0.2)
    with running(make_brain(home, walk=False)):
        (home / "pacman.log").write_text("[2026-09-27T10:00:00+0000] [ALPM] installed ffmpeg (2:7.1-1)\n")
        until(lambda: ask("thing", ref="package:ffmpeg"))
        (h / "memory.md").write_text("- Daniel reads the Arch Wiki\n")
        until(lambda: ask("search", q="Arch Wiki")["items"])


# -- the watcher --

def test_watcher_batches_wake_the_witnesses_and_rename_apps(home, h):
    watcher = FakeWatcher(home / "watch.sock")
    app = h / "Apps" / "notes"
    app.mkdir(parents=True)
    (app / "app.toml").write_text('title = "Notes"\n')
    paths.state_dir().mkdir(parents=True)
    try:
        with running(make_brain(home, watch_path=watcher.path)):
            until(lambda: ask("status")["watching"])
            assert ask("thing", ref="app:notes")["title"] == "Notes"
            (app / "app.toml").write_text('title = "Field notes"\n')
            with paths.turns_log().open("a") as f:
                row = {"n": 5, "t": time.time(), "prompt": "rename my notes app", "files": {}}
                f.write(json.dumps(row) + "\n")
            watcher.send(ev("write", app / "app.toml"), ev("write", paths.turns_log(), TURN_CG, SH))
            until(lambda: ask("thing", ref="app:notes")["title"] == "Field notes")
            until(lambda: ask("thing", ref="turn:5")["title"] == "rename my notes app")
    finally:
        watcher.close()


def test_pacman_lines_belong_to_the_turn_that_wrote_them(home, h):
    watcher = FakeWatcher(home / "watch.sock")
    (home / "pacman.log").write_text("")
    try:
        with running(make_brain(home, watch_path=watcher.path, walk=False)):
            until(lambda: ask("status")["watching"])
            ask("note", kind="turn_start", n=9, unit=TURN_UNIT + ".scope", prompt="install qemu",
                t=time.time())
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime())
            (home / "pacman.log").write_text(f"[{stamp}] [ALPM] installed qemu-full (9.1.0-1)\n")
            chain = [[20, "pacman", "pacman -S qemu-full"]] + SH
            watcher.send(ev("write", home / "pacman.log", TURN_CG, chain))
            focus = until(lambda: ask("focus", ref="package:qemu-full"))
            assert focus["thing"]["made"].startswith("Installed by the machine in turn 9")
    finally:
        watcher.close()


def test_overflow_and_caught_up_walk_to_catch_up(home, h):
    watcher = FakeWatcher(home / "watch.sock")
    try:
        with running(make_brain(home, watch_path=watcher.path)):
            until(lambda: ask("status")["watching"])
            gone = ask("thing", ref=str(h / "notes.md"))
            (h / "notes.md").unlink()   # nobody saw it go
            (h / "Documents" / "new.txt").write_text("x")
            watcher.send({"op": "overflow", "t": time.time()})
            until(lambda: ask("thing", ref=gone["id"])["deleted"])
            until(lambda: ask("thing", ref=str(h / "Documents" / "new.txt")))
    finally:
        watcher.close()


def test_a_watcher_that_cannot_watch_here(home, h):
    with running(make_brain(home)):
        pass   # indexed once
    watcher = FakeWatcher(home / "watch.sock", watching=False)
    try:
        with running(make_brain(home, watch_path=watcher.path), settle=False):
            until(lambda: ask("status")["text"].endswith("; saves are not watched on this system."))
            # Nobody sees saves, so it walked at start to catch up.
            until(lambda: meta("reconciled"))
    finally:
        watcher.close()


def test_the_watcher_coming_and_going(home, h, monkeypatch):
    monkeypatch.setattr(service, "RECONNECT_S", 0.5)
    watcher = FakeWatcher(home / "watch.sock")
    try:
        with running(make_brain(home, watch_path=watcher.path, walk=False)):
            until(lambda: ask("status")["watching"])
            watcher.drop()
            until(lambda: not ask("status")["watching"])
            until(lambda: ask("status")["watching"])   # it came back by itself
            letter = h / "Documents" / "back.txt"
            letter.write_text("hi")
            watcher.send(ev("create", letter))
            until(lambda: ask("why", ref=str(letter)).startswith("You made it in the terminal"))
    finally:
        watcher.close()


def test_changed_pushes_are_throttled(home, h):
    watcher = FakeWatcher(home / "watch.sock")
    try:
        with running(make_brain(home, watch_path=watcher.path, walk=False)):
            until(lambda: ask("status")["watching"])
            with client.Connection() as conn:
                conn.request("subscribe")
                t0 = time.monotonic()
                ids = set()
                for i in range(40):
                    f = h / f"f{i}.txt"
                    f.write_text("x")
                    watcher.send(ev("create", f))
                    time.sleep(0.02)
                got = pushes(conn, "changed", timeout=1.5)
                elapsed = time.monotonic() - t0
                for p in got:
                    ids.update(p["things"])
                assert len(ids) >= 40
                assert len(got) <= elapsed * 4 + 1
    finally:
        watcher.close()


# -- starting over, starting twice, stopping --

def test_rebuild_starts_over(home, h):
    paths.state_dir().mkdir(parents=True)
    row = {"n": 3, "t": time.time(), "prompt": "hello", "files": {}}
    paths.turns_log().write_text(json.dumps(row) + "\n")
    with running(make_brain(home)):
        before = ask("thing", ref=str(h / "notes.md"))["id"]
        with client.Connection() as conn:
            conn.request("subscribe")
            assert ask("rebuild") == {"text": "Starting over: getting to know your files again."}
            wait_push(conn, "status")
        until(lambda: (lambda s: s["walking"] is None and s["indexed"])(ask("status")))
        again = ask("thing", ref=str(h / "notes.md"))
        assert again["path"] == str(h / "notes.md")
        assert ask("thing", ref="turn:3")["title"] == "hello"   # turns.jsonl read again from the top
        assert before is not None


def test_a_database_that_is_damaged_inside_is_replaced(home, h):
    for i in range(400):
        (h / "Documents" / f"file-{i:03}.txt").write_text("x" * i)
    with running(make_brain(home)):
        pass
    db = paths.brain_db()
    size = db.stat().st_size
    assert size > 4096 * 8
    with open(db, "r+b") as f:   # the first pages (the header and the schema) read fine; the rest does not
        f.seek(4096 * 3)
        f.write(b"\xff" * (size - 4096 * 3))
    with running(make_brain(home)):
        assert ask("status")["things"] >= 400
        assert ask("thing", ref=str(h / "notes.md"))["path"] == str(h / "notes.md")
        assert ask("search", q="file-01")["items"]
    assert Path(f"{db}.broken").stat().st_size == size


def test_one_brain_per_database(home, h):
    with running(make_brain(home, walk=False)):
        with pytest.raises(service.AlreadyRunning):
            asyncio.run(make_brain(home, walk=False, socket_path=home / "other.sock").serve())
        assert ask("status")


def test_a_database_it_cannot_read_is_set_aside(home, h):
    db = paths.brain_db()
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not sqlite" * 100)
    with running(make_brain(home)):
        assert ask("status")["things"] >= 3
    assert Path(f"{db}.broken").read_bytes().startswith(b"this is not sqlite")


def test_a_stop_applies_the_saves_it_had_read(home, h, monkeypatch):
    monkeypatch.setattr(service, "BATCH_S", 30.0)   # the batch is still waiting when the stop comes
    watcher = FakeWatcher(home / "watch.sock")
    letter = h / "Documents" / "letter.txt"
    try:
        brain = make_brain(home, watch_path=watcher.path)
        with running(brain):
            until(lambda: ask("status")["watching"])
            letter.write_text("hi")
            watcher.send(ev("create", letter))
            until(lambda: brain._batch)   # read from the watcher, which will not send it again
        assert meta("brain.run") == "running"   # stopped right after a save: the next start walks too
        db = sqlite3.connect(paths.brain_db())
        try:
            assert db.execute("SELECT COUNT(*) FROM things WHERE path = ?", (str(letter),)).fetchone()[0] == 1
        finally:
            db.close()
    finally:
        watcher.close()


def test_a_run_that_was_cut_off_walks_again_after_the_replay(home, h, monkeypatch):
    monkeypatch.setattr(service, "QUIET_S", 0.5)
    watcher = FakeWatcher(home / "watch.sock")
    late, lost = h / "Documents" / "late.txt", h / "Documents" / "lost.txt"
    try:
        with running(make_brain(home, watch_path=watcher.path)):
            until(lambda: ask("status")["watching"])
            assert meta("brain.run") == "running"
        assert meta("brain.run") == "stopped"
        late.write_text("x")
        lost.write_text("x")
        db = sqlite3.connect(paths.brain_db())   # what a kill leaves behind
        db.execute("UPDATE meta SET value = 'running' WHERE key = 'brain.run'")
        db.commit()
        db.close()
        with running(make_brain(home, watch_path=watcher.path), settle=False):
            until(lambda: ask("status")["watching"])
            watcher.send(ev("create", late), ev("write", late))   # the spool, replayed after hello
            # Nobody ever saw `lost`; the walk finds it, after the replay so `late` keeps its maker.
            until(lambda: ask("thing", ref=str(lost)))
            assert ask("why", ref=str(late)).startswith("You made it in the terminal")
    finally:
        watcher.close()


def test_a_database_that_cannot_be_opened_is_one_line_and_exit_1(home, monkeypatch, capsys):
    (home / "state" / "brain.db").mkdir(parents=True)   # a folder where the file should be
    monkeypatch.setenv("BOMBADIL_WATCH_SOCKET", str(home / "none.sock"))
    assert service.main([]) == 1
    err = capsys.readouterr().err
    assert "cannot start:" in err and "Traceback" not in err


def test_sigterm_closes_cleanly(home, h):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "BOMBADIL_WATCH_SOCKET": str(home / "none.sock")}
    proc = subprocess.Popen([sys.executable, str(ROOT / "bin" / "bombadil-brain")], env=env, cwd=str(home),
                            stderr=subprocess.PIPE, text=True)
    try:
        until(lambda: client.request("status"), timeout=20)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not paths.brain_socket().exists()
    assert "answering on" in proc.stderr.read()


# -- the ISO --

def test_the_iso_starts_the_brain_and_puts_both_programs_on_path():
    etc = ROOT / "iso" / "airootfs" / "etc" / "systemd" / "user"
    unit = (etc / "bombadil-brain.service").read_text()
    assert "\nExecStart=/usr/local/bin/bombadil-brain\n" in unit and "\nWantedBy=default.target\n" in unit
    link = etc / "default.target.wants" / "bombadil-brain.service"
    assert link.is_symlink() and os.readlink(link) == "../bombadil-brain.service"
    assert link.resolve() == (etc / "bombadil-brain.service").resolve()
    loop = re.search(r"^for b in ([^;]+); do$", (ROOT / "scripts" / "build-iso.sh").read_text(), re.M)
    assert {"bombadil-brain", "bombadil-brain-watch"} <= set(loop.group(1).split())
    for name in ("bombadil-brain", "bombadil-brain-watch"):
        assert os.access(ROOT / "bin" / name, os.X_OK)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_smoke_script_parses_and_looks_for_both_programs():
    smoke = ROOT / "iso" / "airootfs" / "usr" / "local" / "bin" / "bombadil-smoke"
    assert subprocess.run(["bash", "-n", str(smoke)], capture_output=True, check=False).returncode == 0
    assert "bombadil-brain bombadil-brain-watch; do command -v" in smoke.read_text()


# -- bombadil brain --

def cli():
    loader = importlib.machinery.SourceFileLoader("bombadil_cli", str(ROOT / "bin" / "bombadil"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("bombadil_cli", loader))
    loader.exec_module(mod)
    return mod


def run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["bombadil", "brain", *args])
    code = cli().main()
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_speaks_in_lines_and_json(home, h, monkeypatch, capsys):
    with running(make_brain(home)):
        code, out, _ = run_cli(monkeypatch, capsys)
        assert code == 0 and out.startswith("Knows ") and out.endswith("; not watching saves right now.\n")
        code, out, _ = run_cli(monkeypatch, capsys, "why", str(h / "notes.md"))
        assert code == 0 and out.startswith("It was here before the brain started.")
        code, out, _ = run_cli(monkeypatch, capsys, "find", "notes")
        assert code == 0 and out.startswith("notes.md · Home · ")
        code, _, err = run_cli(monkeypatch, capsys, "find", "zzzzz")
        assert code == 1 and err == "Nothing matches “zzzzz” yet.\n"
        monkeypatch.chdir(h)
        code, out, _ = run_cli(monkeypatch, capsys, "focus", "notes.md", "--json")
        assert code == 0 and json.loads(out)["thing"]["path"] == str(h / "notes.md")
        code, out, _ = run_cli(monkeypatch, capsys, "focus", ".")
        assert code == 0 and out.splitlines()[0] == "Home" and "In it (2)" in out and "  Documents/ · " in out
        code, out, _ = run_cli(monkeypatch, capsys, "status", "--json")
        assert json.loads(out)["watching"] is False
        code, _, err = run_cli(monkeypatch, capsys, "focus", "turn:404")
        assert code == 1 and err == "The brain does not know this yet.\n"
        code, out, _ = run_cli(monkeypatch, capsys, "rebuild")
        assert code == 0 and out == "Starting over: getting to know your files again.\n"
        code, out, _ = run_cli(monkeypatch, capsys, "nonsense")
        assert code == 2
    code, _, err = run_cli(monkeypatch, capsys, "status")
    assert code == 1 and err.splitlines()[-1] == "The brain is not running yet."


def test_cli_focus_lines():
    f = {"thing": {"title": "snapshots.py", "path": "/home/user/Projects/bombadil/snapshots.py",
                   "made": "You made it in the terminal on 3 Sep.",
                   "last": "Last changed by the machine in turn 44, today at 10:02."},
         "description": {"text": "Takes and prunes snapper snapshots.", "stale": True, "pending": True},
         "slots": {"came_from": {"items": [{"title": "the machine, turn 12",
                                            "why": "“drop the old cleanup”, 13:02", "when": "13:02"}],
                                 "more": 2},
                   "read_with": {"items": [{"title": "Snapper - ArchWiki",
                                            "why": "open while you changed this", "when": "Tue 14:02"}],
                                 "more": 0},
                   "used_with": {"items": [], "more": 0},
                   "changes": {"count_week": 7,
                               "items": [{"when": "10:02", "why": "changed by the machine in turn 44"}],
                               "more": 6}},
         "children": None}
    assert cli().focus_lines(f) == [
        "snapshots.py", "/home/user/Projects/bombadil/snapshots.py",
        "You made it in the terminal on 3 Sep.", "Last changed by the machine in turn 44, today at 10:02.",
        "“Takes and prunes snapper snapshots.” (out of date)",
        "", "Came from", "  the machine, turn 12 · “drop the old cleanup”, 13:02", "  and 2 more",
        "", "Read with", "  Snapper - ArchWiki · open while you changed this · Tue 14:02",
        "", "Changes (7 this week)", "  10:02 · changed by the machine in turn 44", "  and 6 more"]


def test_cli_refs(tmp_path, monkeypatch):
    mod = cli()
    monkeypatch.chdir(tmp_path)
    assert mod.brain_ref("turn:41") == "turn:41"
    assert mod.brain_ref("https://example.org/a") == "https://example.org/a"
    assert mod.brain_ref("12") == "12" and mod.brain_ref("system") == "system"
    assert mod.brain_ref("notes.md") == str(tmp_path / "notes.md")
    (tmp_path / "12").write_text("a file named 12")
    assert mod.brain_ref("12") == str(tmp_path / "12")


# -- everything together --

def test_a_batch_line_from_the_watcher_reads_like_its_events(home, h):
    """Consecutive events by one writer come as one line that names the writer once."""
    watcher = FakeWatcher(home / "watch.sock")
    try:
        with running(make_brain(home, watch_path=watcher.path)):
            until(lambda: ask("status")["watching"])
            assert client.notify("note", kind="turn_start", n=7, unit=TURN_UNIT, prompt="install the VPN",
                                 t=time.time())
            until(lambda: ask("thing", ref="turn:7"))
            files = [h / f"batch-{i}.sh" for i in range(3)]
            for f in files:
                f.write_text("#!/bin/sh\n")
            events = [ev(op, f, TURN_CG, SH) for f in files for op in ("create", "write")]
            writer = {k: events[0][k] for k in ("pid", "uid", "comm", "cgroup", "chain", "gone")}
            watcher.send({"op": "batch", **writer,
                          "events": [{k: v for k, v in e.items() if k not in writer} for e in events]},
                         {"op": "batch", "events": "a broken line is skipped, the next one still counts"})
            for f in files:
                until(lambda f=f: ask("thing", ref=str(f)))
                assert ask("why", ref=str(f)).startswith("Made by the machine in turn 7, “install the VPN”, today at")
    finally:
        watcher.close()


def test_end_to_end_from_the_watcher_to_the_answers(home, h):
    for mod in ("witnesses", "index", "focus", "describe"):
        pytest.importorskip(f"bombadil.brain.{mod}")
    watcher = FakeWatcher(home / "watch.sock")
    try:
        with running(make_brain(home, watch_path=watcher.path)):
            status = until(lambda: (lambda s: s["watching"] and s)(ask("status")))
            assert status["text"] == f"Knows {status['things']:,} things; watching every save."
            # agentd pokes the brain as a turn starts: that scope's writes are turn 7's.
            assert client.notify("note", kind="turn_start", n=7, unit=TURN_UNIT, prompt="install the VPN",
                                 t=time.time())
            until(lambda: ask("thing", ref="turn:7"))
            with client.Connection() as conn:
                conn.request("subscribe")
                wg = h / "setup-wg.sh"
                wg.write_text("#!/bin/sh\nwg-quick up wg0\n")
                letter = h / "Documents" / "letter.txt"
                letter.write_text("Dear landlord,\n")
                watcher.send(ev("create", wg, TURN_CG, SH), ev("write", wg, TURN_CG, SH),
                             ev("create", letter), ev("write", letter))
                wg_id = until(lambda: ask("thing", ref=str(wg)))["id"]
                letter_id = until(lambda: ask("thing", ref=str(letter)))["id"]
                wait_push(conn, "changed", lambda p: wg_id in p["things"])

                assert ask("why", ref=str(wg)).startswith(
                    "Made by the machine in turn 7, “install the VPN”, today at")
                moved = h / "Documents" / "letter-final.txt"
                letter.rename(moved)
                watcher.send(ev("rename", moved, old=str(letter)))
                f = until(lambda: ask("focus", ref=str(moved)))
                assert f["thing"]["id"] == letter_id and f["thing"]["title"] == "letter-final.txt"
                assert "move" in [c["kind"] for c in f["slots"]["changes"]["items"]]
                assert ask("why", ref=str(moved)).startswith("You made it in the terminal today at")

                found = ask("search", q="setup")["items"]
                assert [(i["title"], i["where"], i["kind"]) for i in found] == [
                    ("setup-wg.sh", "Home", "file")]
                turn = ask("focus", ref="turn:7")
                assert turn["thing"]["title"] == "install the VPN"
                assert "setup-wg.sh" in [c["name"] for c in turn["children"]["items"]]

                # Looking starts a description; when it is written, subscribers hear of it.
                while conn.push(0.5) is not None:
                    pass
                ask("focus", ref=str(wg))
                wait_push(conn, "changed", lambda p: wg_id in p["things"])
                until(lambda: ask("focus", ref=str(wg))["description"]["text"] == DESCRIPTION)
            assert ask("status")["things"] > status["things"]
    finally:
        watcher.close()
