"""`bombadil loop ...`, `bombadil probe`, `bombadil doctor` and `bin/bombadil-probe`, run as programs
against a temp machine: real counts in a real loop.db, a stand-in agentd and Hyprland on sockets, and
a PATH of three stand-in programs, so nothing here can reach the real machine, a browser or the network."""

import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from bombadil.loop import db, findings, store
from bombadil.loop.offers import Config
from bombadil.loop.probes import Observation, Result

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "loop"))
import golden_corpus as gc

ROOT = Path(__file__).resolve().parents[1]
BOMBADIL = ROOT / "bin" / "bombadil"
PROBER = ROOT / "bin" / "bombadil-probe"
DAY = 86400
ASKED = "show me my passwords"


# -- a machine to run against --

class Server:
    """A stand-in on a unix socket. `reply(msg)` answers each JSON line a client sends with a list of
    messages (None hangs up); every line received is kept in `got`. It greets like agentd does."""

    def __init__(self, path: Path, reply):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.reply, self.got = path, reply, []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        with conn:
            conn.sendall(b'{"type": "status", "busy": false}\n')
            for line in conn.makefile("rb"):
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                self.got.append(msg)
                answers = self.reply(msg)
                if answers is None:
                    return
                for a in answers:
                    conn.sendall((json.dumps(a) + "\n").encode())

    def close(self):
        self.sock.close()


class Hyprland:
    """A stand-in for the compositor's socket: one request, one answer, then it closes."""

    def __init__(self, path: Path, answers: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.answers = answers
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                ask = conn.recv(4096).decode()
                conn.sendall(json.dumps(self.answers.get(ask.removeprefix("j/"), [])).encode())

    def close(self):
        self.sock.close()


class Machine:
    """Where the commands run: a home, a loop directory, a runtime directory for agentd's socket and
    Hyprland's, and a PATH that holds only a `python3`, a `systemctl` and a `bombadil-app` of ours."""

    def __init__(self, root: Path):
        self.root = root
        self.loop = root / "state" / "loop"
        self.state = root / "state"
        self.runtime = root / "run"
        self.xdg = root / "x"
        self.bin = root / "fakebin"
        self.bin.mkdir()
        self.servers: list = []
        self._program("python3", f'exec "{sys.executable}" "$@"')
        self._program("systemctl", 'case "$*" in *is-active*) echo "${FAKE_PROBER:-active}";; esac')
        self._program("bombadil-app", '[ "${FAKE_APP_CHECK:-yes}" = yes ] || exit 1\n'
                                      'case "$*" in *--help*) ;; *) echo \'{"ok": true}\';; esac')
        self.env = {**os.environ, "HOME": str(root), "PATH": str(self.bin), "XDG_RUNTIME_DIR": str(self.xdg),
                    "BOMBADIL_STATE": str(self.state), "BOMBADIL_LOOP": str(self.loop),
                    "BOMBADIL_RUNTIME": str(self.runtime), "BOMBADIL_CONFIG": str(root / "config"),
                    "BOMBADIL_APPS": str(root / "Apps"), "XDG_DATA_HOME": str(root / "share"),
                    "BOMBADIL_SHARE": str(root / "nowhere")}
        self.env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)

    def _program(self, name: str, body: str):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def run(self, *args, program=BOMBADIL, timeout=90, **env):
        done = subprocess.run([sys.executable, str(program), *args], capture_output=True, text=True,
                              timeout=timeout, env={**self.env, **env}, check=False)
        assert "Traceback" not in done.stderr, done.stderr     # a broken thing costs a line, never a traceback
        return done

    def agentd(self, reply=lambda msg: [{"type": "pong"}] if msg.get("type") == "ping" else []):
        server = Server(self.runtime / "agentd.sock", reply)
        self.servers.append(server)
        return server

    def hyprland(self):
        fx = json.loads((ROOT / "tests" / "fixtures" / "loop" / "probes" / "drawer-fixed.json").read_text())
        (self.xdg / "hypr").mkdir(parents=True, exist_ok=True)
        server = Hyprland(self.xdg / "hypr" / "testsig" / ".socket.sock",
                          {"layers": fx["layers"], "configerrors": [], "monitors": fx["monitors"],
                           "clients": fx["clients"]})
        self.servers.append(server)
        return server

    # what there is to look at

    def seed(self, *repeats: tuple, offer: bool = False):
        """Each (text, app, times) is an ask made that many times on as many days, each time opening the
        app, counted into loop.db by the real store. With none given: passwords, four times."""
        now = time.time()
        rows = []
        for g, (text, app, times) in enumerate(repeats or [(ASKED, "passwords", 4)]):
            for i in range(times):
                t = now - (2 * (times - i) + 1) * DAY + g * 3600
                rows.append({"id": f"{int(t * 1000)}-{g}{i}", "text": text, "t": t, "day": 0, "seconds": 8,
                             "events": [["mcp__bombadil-os__open_app", {"name": app}]], "group": "x"})
        turns, _ = gc.write_corpus(self.state, rows)
        s = store.LoopStore(self.loop / "loop.db", app_list=gc.app_list(), config=Config())
        s.ingest(turns, now)
        if offer:
            assert s.ripe_offer(now) is not None
        s.close()
        return turns

    def find(self, rule: str = "crash", observed: str = "agentd crashed (signal 11)",
             probe: str = "coredump"):
        """One finding the prober made, with its evidence."""
        s = findings.FindingsStore(db.connect(self.loop / "loop.db"))
        r = Result(False, probe, "agentd", rule, "no crash", observed, kind="crash",
                   title="A part of Bombadil crashed")
        versions = {"build": "abc1234", "Hyprland": "0.56.2"}
        found = s.record(r, time.time(), obs=Observation(), versions=versions)
        s.close()
        assert found is not None
        return found

    def db_bytes(self) -> str:
        return hashlib.sha1((self.loop / "loop.db").read_bytes()).hexdigest()

    def counts(self) -> tuple:
        s = store.LoopStore(self.loop / "loop.db", app_list=gc.app_list(), config=Config())
        try:
            got = s.status()
            return got["requests"], got["groups"]
        finally:
            s.close()


@pytest.fixture
def machine(home, tmp_path, monkeypatch):
    root = tmp_path / "m"
    root.mkdir()
    m = Machine(root)
    # This process reads the same places (findings write their evidence under the loop directory).
    for key in ("HOME", "BOMBADIL_STATE", "BOMBADIL_LOOP", "BOMBADIL_RUNTIME", "BOMBADIL_CONFIG",
                "BOMBADIL_APPS", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "BOMBADIL_SHARE"):
        monkeypatch.setenv(key, m.env[key])
    yield m
    for server in m.servers:
        server.close()


# -- loop asks --

def test_asks_with_nothing_counted_says_so_and_makes_nothing(machine):
    got = machine.run("loop", "asks")
    assert (got.returncode, got.stdout.strip(), got.stderr) == (0, "Nothing counted yet.", "")
    got = machine.run("loop", "asks", "--json")
    assert got.returncode == 0 and json.loads(got.stdout) == []
    assert not machine.loop.exists()        # looking never creates loop.db


def test_asks_lists_what_is_asked_most_in_his_words(machine):
    machine.seed()
    got = machine.run("loop", "asks")
    assert got.returncode == 0 and got.stderr == ""
    first, second = got.stdout.splitlines()
    assert first.startswith("4 times on 4 days") and "passwords" in first
    assert second == f"    “{ASKED}”"


def test_asks_as_json_and_limited(machine):
    machine.seed((ASKED, "passwords", 4), ("show me my runs", "runs", 3))
    rows = json.loads(machine.run("loop", "asks", "--json").stdout)
    assert [(r["label"], r["n"], r["days"]) for r in rows] == [("passwords", 4, 4), ("runs", 3, 3)]
    assert rows[0]["sentences"] == [ASKED] and {"id", "first", "last", "state", "weight"} <= set(rows[0])
    assert len(json.loads(machine.run("loop", "asks", "--json", "-n", "1").stdout)) == 1
    assert len(machine.run("loop", "asks", "-n", "1").stdout.splitlines()) == 2     # a line and its sentence


def test_asking_never_changes_loop_db(machine):
    machine.seed(offer=True)
    before = machine.db_bytes()
    for cmd in (("asks",), ("asks", "--json"), ("status",), ("forget",), ("report",)):
        assert machine.run("loop", *cmd).returncode == 0
    assert machine.db_bytes() == before and machine.counts() == (4, 1)


def test_asks_refuses_a_count_that_is_not_a_number(machine):
    for bad in ("x", "0", "-2"):
        got = machine.run("loop", "asks", "-n", bad)
        assert got.returncode == 2 and "whole number" in got.stderr


def test_a_loop_db_that_is_not_a_database_is_nothing_counted_yet(machine):
    machine.loop.mkdir(parents=True)
    (machine.loop / "loop.db").write_text("this is not a database at all")
    for cmd in (("asks",), ("asks", "--json"), ("forget",), ("report",)):
        got = machine.run("loop", *cmd)
        assert got.returncode == 0, (cmd, got.stderr)
        assert len(got.stderr.strip().splitlines()) == 1 and "loop.db cannot be read" in got.stderr
    assert machine.run("loop", "asks").stdout.strip() == "Nothing counted yet."


def test_a_loop_db_with_no_tables_yet_is_nothing_counted_yet_without_complaint(machine):
    machine.loop.mkdir(parents=True)
    (machine.loop / "loop.db").write_bytes(b"")
    got = machine.run("loop", "asks")
    assert (got.returncode, got.stdout.strip(), got.stderr) == (0, "Nothing counted yet.", "")


# -- loop status --

def test_status_with_nothing_there_answers_in_plain_words(machine):
    got = machine.run("loop", "status")
    assert got.returncode == 0 and got.stderr == ""
    lines = got.stdout.splitlines()
    assert lines[:3] == ["Counted: nothing yet.", "Offers: none waiting.", "Found: nothing waiting."]
    assert "Prober: running." in lines
    assert "Doctor: has not looked yet." in lines
    assert any(ln.startswith("agentd: does not answer") for ln in lines)
    assert "Bar: has not reported yet." in lines
    assert any(ln.startswith("Build: ") for ln in lines) and any(ln.startswith("Versions: ") for ln in lines)
    assert not machine.loop.exists()


def test_status_says_what_is_counted_found_and_alive(machine):
    machine.seed(offer=True)
    found = machine.find()
    machine.loop.joinpath("doctor.json").write_text(json.dumps({"t": time.time() - 3 * 3600, "checks": [
        {"name": "agentd-ping", "ok": True, "detail": "answered in 4 ms"},
        {"name": "units", "ok": True, "detail": "skipped: no user manager", "skipped": True}]}))
    machine.loop.joinpath("bar.json").write_text(json.dumps({"pid": 7, "alive_at": time.time() - 1}))
    machine.agentd()
    got = machine.run("loop", "status")
    lines = got.stdout.splitlines()
    assert got.returncode == 0
    assert lines[0] == "Counted: 4 asks read, 4 of them count, in 1 group."
    assert lines[1] == "Offers: 1 waiting."
    assert lines[2] == "Found: 1 to look at."
    assert lines[3] == f"    A part of Bombadil crashed: 1 time on 1 day  ({found.fp})"
    assert "Prober: running." in lines
    assert "Doctor: looked 3 h ago, all 2 fine." in lines
    assert any(ln.startswith("agentd: answers in ") for ln in lines)
    assert re.search(r"^Bar: alive, last heard \d s ago\.$", got.stdout, re.MULTILINE)


def test_status_names_what_is_not_well(machine):
    machine.seed()
    machine.loop.joinpath("doctor.json").write_text(json.dumps({"t": time.time() - 90, "checks": [
        {"name": "os-mcp", "ok": False, "detail": "os-mcp lists no tools"},
        {"name": "units", "ok": True, "detail": "no failed unit"}]}))
    machine.loop.joinpath("bar.json").write_text(json.dumps({"pid": 7, "alive_at": time.time() - 600}))
    got = machine.run("loop", "status", FAKE_PROBER="failed")
    lines = got.stdout.splitlines()
    assert "Prober: not running (failed)." in lines
    assert "Doctor: looked 2 min ago, 1 of 2 failed (os-mcp)." in lines
    assert "Bar: quiet, last heard 10 min ago." in lines


def test_status_on_a_loop_db_that_cannot_be_read_still_answers(machine):
    machine.loop.mkdir(parents=True)
    (machine.loop / "loop.db").write_bytes(b"\x00" * 600)
    got = machine.run("loop", "status")
    assert got.returncode == 0 and got.stdout.startswith("Counted: nothing yet.")
    assert len(got.stderr.strip().splitlines()) == 1


# -- loop replay --

def test_replay_shows_what_would_have_been_offered_and_pairs_to_label(machine):
    turns = machine.seed()
    got = machine.run("loop", "replay", str(turns))
    out = got.stdout
    assert got.returncode == 0
    assert out.splitlines()[0].startswith("Replayed 4 rows of ") and "4 counted" in out.splitlines()[0]
    assert "Asked more than once: 1 group; 0 asked once." in out
    assert "Would have offered, if you never answered:" in out and "passwords (3 times)" in out
    assert "Pairs to label" in out and f"a  “{ASKED}”" in out and f"b  “{ASKED}”" in out
    assert "the loop said same" in out


def test_replay_defaults_to_his_own_ledger_and_takes_a_number_of_pairs(machine):
    machine.seed()
    assert len(machine.run("loop", "replay", "--pairs", "2").stdout.split("the loop said")) == 3
    assert len(machine.run("loop", "replay", "--pairs", "1").stdout.split("the loop said")) == 2


def test_replay_as_json_is_what_the_store_gives(machine):
    machine.seed()
    got = json.loads(machine.run("loop", "replay", "--json").stdout)
    assert got["turns"] == 4 and got["counted"] == 4
    assert [(g["label"], g["n"]) for g in got["groups"]] == [("passwords", 4)]
    assert got["offers"] and {"a", "b", "same", "score"} <= set(got["pairs"][0])


def test_replay_touches_no_file_and_needs_no_loop_db(machine):
    turns = machine.seed()
    (machine.loop / "loop.db").unlink()
    for side in machine.loop.glob("loop.db-*"):
        side.unlink()
    assert machine.run("loop", "replay", str(turns)).returncode == 0
    assert not (machine.loop / "loop.db").exists()        # the count runs in memory, on a copy


def test_replay_with_the_turns_logs_somewhere_else_and_his_apps_named(machine):
    turns = machine.seed()
    moved = machine.root / "copied"
    (machine.state / "turns").rename(moved)
    lost = machine.run("loop", "replay", str(turns), "--json")
    assert json.loads(lost.stdout)["groups"][0]["routes"] == []     # the rows point at logs that moved
    got = machine.run("loop", "replay", str(turns), "--logs", str(moved), "--app", "Passwords", "--json")
    assert got.returncode == 0 and json.loads(got.stdout)["groups"][0]["routes"]


def test_replay_of_nothing(machine):
    got = machine.run("loop", "replay")
    assert got.returncode == 0 and "Nothing to replay" in got.stdout        # their own ledger, not there yet
    got = machine.run("loop", "replay", str(machine.root / "missing.jsonl"))
    assert got.returncode == 1 and "Nothing to replay" in got.stderr and got.stdout == ""


def test_replay_of_a_ledger_with_garbage_in_it_costs_the_rows_that_are_garbage(machine):
    machine.state.mkdir(parents=True)
    (machine.state / "turns.jsonl").write_bytes(b'not json\n{"t": "x"}\n\x00\x01\n[1, 2]\n')
    got = machine.run("loop", "replay")
    assert got.returncode == 0 and got.stdout.startswith("Replayed ")


# -- loop report --

def test_report_prints_the_one_thing_that_waits(machine):
    machine.seed()
    found = machine.find()
    before = machine.db_bytes()
    got = machine.run("loop", "report")
    assert got.returncode == 0
    assert got.stdout.startswith("Title: A part of Bombadil crashed")
    assert f"[fp {found.fp.rsplit(':', 1)[1]}]" in got.stdout
    assert "Expected: no crash" in got.stdout and "Observed: agentd crashed" in got.stdout
    assert "Not held and not sent" in got.stderr
    assert ASKED not in got.stdout                                  # nothing of their words
    assert not (machine.loop / "reports").exists() and machine.db_bytes() == before


def test_report_finds_a_finding_by_its_fingerprint_its_hash_or_the_start_of_it(machine):
    machine.seed()
    found = machine.find()
    want = machine.run("loop", "report").stdout
    for name in (found.fp, found.fp.rsplit(":", 1)[1], "agentd:crash"):
        assert machine.run("loop", "report", name).stdout == want, name
    got = machine.run("loop", "report", "nosuch")
    assert got.returncode == 1 and "Nothing found called nosuch" in got.stderr


def test_report_with_several_asks_which_one(machine):
    machine.seed()
    a = machine.find()
    b = machine.find("probe-raised", "a check broke", "turn-failed")
    got = machine.run("loop", "report")
    assert got.returncode == 2 and got.stdout == ""
    assert a.fp in got.stderr and b.fp in got.stderr
    assert machine.run("loop", "report", b.fp).returncode == 0


def test_report_prints_the_held_report_as_it_was_held(machine):
    machine.seed()
    found = machine.find()
    held = machine.loop / "reports" / f"{findings.evidence_dir(found.fp).name}.md"
    held.parent.mkdir()
    held.write_text("Title: held for sending\nSeen: once\n")
    got = machine.run("loop", "report")
    assert got.returncode == 0 and got.stdout == "Title: held for sending\nSeen: once\n" and got.stderr == ""
    held.write_bytes(b"\xff\xfe not text")          # a held file that cannot be read is built again
    got = machine.run("loop", "report")
    assert got.returncode == 0 and got.stdout.startswith("Title: A part of Bombadil crashed")


def test_report_with_nothing_found(machine):
    assert "Nothing found that waits" in machine.run("loop", "report").stdout
    machine.seed()
    assert machine.run("loop", "report").returncode == 0
    assert machine.run("loop", "report", "anything").returncode == 1


# -- loop forget --

def test_forget_without_yes_says_what_it_would_do_and_does_nothing(machine):
    machine.seed()
    before = machine.db_bytes()
    got = machine.run("loop", "forget")
    assert got.returncode == 0 and "This would forget what you asked: 4 requests, in 1 group." in got.stdout
    assert "--yes" in got.stdout and "would stay" in got.stdout
    assert machine.db_bytes() == before and machine.counts() == (4, 1)


def test_forget_with_yes_and_no_agentd_opens_the_store_itself(machine):
    machine.seed()
    got = machine.run("loop", "forget", "--yes")
    assert got.returncode == 0 and got.stdout.startswith("Forgot 4 requests, in 1 group.")
    assert machine.counts() == (0, 0)
    assert machine.run("loop", "asks").stdout.strip() == "Nothing asked more than once yet."
    assert machine.run("loop", "forget").stdout.startswith("Nothing counted yet. There is nothing to forget.")


def test_forget_with_yes_and_nothing_counted(machine):
    got = machine.run("loop", "forget", "--yes")
    assert got.returncode == 0 and "There is nothing to forget." in got.stdout
    assert not machine.loop.exists()


def test_forget_with_agentd_running_goes_through_agentd_and_leaves_the_file_alone(machine):
    machine.seed()
    before = machine.db_bytes()
    done = {"type": "noticed_result", "op": "forget_asks", "id": "", "ok": True, "text": "Forgot 4 requests."}
    server = machine.agentd(lambda msg: [done] if msg["type"] == "noticed_do" else [])
    got = machine.run("loop", "forget", "--yes")
    assert got.returncode == 0 and got.stdout == "Forgot 4 requests.\n"
    assert server.got == [{"type": "noticed_do", "op": "forget_asks"}]
    assert machine.db_bytes() == before and machine.counts() == (4, 1)


def test_forget_when_agentd_says_no_or_says_nothing(machine):
    machine.seed()
    machine.agentd(lambda msg: [{"type": "noticed_result", "op": "forget_asks", "ok": False,
                                 "text": "The counts are busy."}])
    got = machine.run("loop", "forget", "--yes")
    assert got.returncode == 1 and got.stderr == "The counts are busy.\n" and got.stdout == ""
    machine.servers.pop().close()
    (machine.runtime / "agentd.sock").unlink()
    machine.agentd(lambda msg: None)                # reads the line and hangs up
    got = machine.run("loop", "forget", "--yes")
    assert got.returncode == 1 and "did not answer" in got.stderr
    assert machine.counts() == (4, 1)               # with agentd there, the file is not opened behind it


def test_a_socket_with_nothing_behind_it_is_not_an_agentd(machine):
    machine.seed()
    machine.runtime.mkdir(parents=True, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(machine.runtime / "agentd.sock"))
    stale.close()                                   # the file stays, nobody listens
    got = machine.run("loop", "forget", "--yes")
    assert got.returncode == 0 and got.stdout.startswith("Forgot 4 requests")


def test_forget_on_a_loop_db_that_cannot_be_opened_says_so_in_one_line(machine):
    machine.loop.mkdir(parents=True)
    (machine.loop / "loop.db").write_text("not a database")
    got = machine.run("loop", "forget", "--yes")
    assert got.returncode == 1 and got.stderr.startswith("loop.db cannot be changed")
    assert len(got.stderr.strip().splitlines()) == 1


# -- probe --

def test_probe_with_a_name_it_does_not_know(machine):
    got = machine.run("probe", "nosuch", "drawer-focus")
    assert got.returncode == 2 and "No check called nosuch." in got.stderr and "drawer-focus," in got.stderr


def test_a_check_that_could_not_look_is_not_checked_and_not_red(machine):
    got = machine.run("probe", "monitor-narrow")
    assert got.returncode == 0
    line = got.stdout.strip()
    assert line.startswith("not checked") and "monitor-narrow" in line and "no monitors" in line


def test_a_red_check_says_what_was_expected_and_what_was_seen(machine):
    got = machine.run("probe", "agentd-ping")         # nothing listens on agentd's socket
    assert got.returncode == 1
    lines = got.stdout.splitlines()
    assert lines[0].split()[:2] == ["red", "agentd-ping"]
    assert lines[1].strip() == "expected: agentd answers ping"
    assert lines[2].strip().startswith("observed: agentd did not accept a connection")


def test_a_green_check_says_what_it_checks_and_exits_clean(machine):
    machine.agentd()
    got = machine.run("probe", "agentd-ping")
    assert got.returncode == 0
    assert got.stdout.split()[:2] == ["ok", "agentd-ping"] and "a real connection" in got.stdout


def test_loop_probe_is_the_same_as_probe_and_every_check_is_one_line_or_a_block(machine):
    machine.agentd()
    machine.hyprland()
    short = machine.run("probe", "agentd-ping", "bar-layer", "hypr-config")
    long = machine.run("loop", "probe", "agentd-ping", "bar-layer", "hypr-config")
    assert short.returncode == long.returncode == 0 and short.stdout == long.stdout
    assert [ln.split()[0] for ln in short.stdout.splitlines()] == ["ok", "ok", "ok"]


def test_all_the_checks_run_with_no_compositor_and_no_agentd(machine):
    got = machine.run("probe")
    lines = got.stdout.splitlines()
    from bombadil.loop import probes
    heads = [ln.split() for ln in lines if not ln.startswith(" ")]
    assert [h[2] if h[0] == "not" else h[1] for h in heads] == probes.ids()
    assert got.returncode == 1 and any(ln.startswith("red") and "agentd-ping" in ln for ln in lines)


# -- doctor --

def test_the_idle_doctor_before_it_has_looked(machine):
    got = machine.run("doctor")
    assert got.returncode == 0 and "has not looked yet" in got.stdout and "--live" in got.stdout


def test_the_idle_doctor_shows_its_last_look_and_fails_when_a_check_did(machine):
    machine.loop.mkdir(parents=True)
    checks = [{"name": "agentd-ping", "ok": True, "detail": "answered in 4 ms"},
              {"name": "hypr-config", "ok": False, "detail": "line 3: bad key\nline 9"},
              {"name": "canary-apps", "ok": True, "detail": "skipped: no canary app", "skipped": True}]
    machine.loop.joinpath("doctor.json").write_text(
        json.dumps({"t": time.time() - 5 * 3600, "checks": checks}))
    got = machine.run("doctor")
    assert got.returncode == 1
    lines = got.stdout.splitlines()
    assert lines[0] == "The idle doctor looked 5 h ago."
    assert lines[1:] == ["ok       agentd-ping  answered in 4 ms",
                         "failed   hypr-config  line 3: bad key line 9",
                         "skipped  canary-apps  no canary app"]
    checks[1]["ok"] = True
    machine.loop.joinpath("doctor.json").write_text(json.dumps({"t": time.time(), "checks": checks}))
    assert machine.run("doctor").returncode == 0


def test_an_idle_doctor_file_that_is_garbage_is_no_look_at_all(machine):
    machine.loop.mkdir(parents=True)
    for text in ("garbage", '{"t": "x"}', '{"checks": 3}', "[1]", '{"t": 1, "checks": [1, 2]}'):
        machine.loop.joinpath("doctor.json").write_text(text)
        got = machine.run("doctor")
        assert got.returncode == 0, text


def test_doctor_live_on_a_healthy_machine_passes_every_check(machine):
    machine.agentd()
    machine.hyprland()
    got = machine.run("doctor", "--live")
    assert got.returncode == 0, got.stdout
    names = [ln.split()[1] for ln in got.stdout.splitlines()]
    assert names == ["agentd-ping", "bar-layer", "hypr-config", "units", "os-mcp", "canary-apps"]
    assert all(ln.startswith("ok ") for ln in got.stdout.splitlines()), got.stdout
    assert got.stderr.strip() == "Looking, which can take up to a minute."


def test_doctor_live_fails_with_a_line_for_each_check_that_did(machine):
    got = machine.run("doctor", "--live", FAKE_APP_CHECK="no")      # no agentd, no compositor, no app check
    assert got.returncode == 1
    by = {ln.split()[1]: ln.split()[0] for ln in got.stdout.splitlines()}
    assert by == {"agentd-ping": "failed", "bar-layer": "failed", "hypr-config": "failed", "units": "ok",
                  "os-mcp": "ok", "canary-apps": "skipped"}


# -- the prober's program --

@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT])
def test_the_prober_starts_idles_and_stops_cleanly_when_told_to(machine, stop):
    proc = subprocess.Popen([sys.executable, str(PROBER)], env=machine.env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(2.0)          # no Hyprland here: it finds none, and still runs
        assert proc.poll() is None, proc.communicate()
        proc.send_signal(stop)
        out, err = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0 and "Traceback" not in err, err
    assert out == ""
