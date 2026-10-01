import json
import time
from datetime import UTC, datetime

import pytest

from bombadil import paths, watch


@pytest.fixture
def utc(monkeypatch):
    """Clock times read in UTC (the container's own zone data is not to be trusted)."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _at(*args) -> float:
    return datetime(*args, tzinfo=UTC).timestamp()


def test_details_show_each_step_its_command_and_output():
    events = [
        {"kind": "turn_start", "prompt": "install ffmpeg", "t": 0},
        {"kind": "snapshot", "number": 7},
        {"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S --noconfirm ffmpeg"}},
        {"kind": "tool_result", "output": "installing ffmpeg...\ndone", "error": False},
        {"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S docker"}},
        {"kind": "tool_result", "output": "The user doesn't want to proceed with this tool use. The tool use was "
                                          "rejected (eg. if it was a file edit ...", "error": True},
        {"kind": "turn_end", "seconds": 9, "stopped": True, "line": "Stopped while installing docker."},
    ]
    lines = list(watch.Renderer().lines(events))
    assert lines[0].startswith("› install ffmpeg")
    assert "  restore point 7" in lines
    assert "▸ Installing ffmpeg  [system]" in lines and "  $ sudo pacman -S --noconfirm ffmpeg" in lines
    assert "  │ installing ffmpeg..." in lines and "  │ done" in lines
    # Claude Code's message to the model about a stopped command reads as what it was.
    assert "  │ stopped" in lines and not any("doesn't want" in x for x in lines)
    assert lines[-1].startswith("  Stopped while installing docker.")


def test_every_command_shows_under_its_step_marked_or_not():
    events = [
        {"kind": "tool", "name": "Bash", "input": {"command": "ls -la ~/Downloads"}},
        {"kind": "tool_result", "output": "a\nb", "error": False},
        {"kind": "tool", "name": "Bash", "input": {"command": "bash -lc 'cat > x <<EOF\nhello\nEOF'"}},
        {"kind": "tool", "name": "Edit", "input": {"file_path": "/home/d/app.py"}},
    ]
    lines = list(watch.Renderer().lines(events))
    assert lines[:4] == ["▸ Looking through files", "  $ ls -la ~/Downloads", "  │ a", "  │ b"]
    assert lines[4:8] == ["▸ Writing x", "  $ cat > x <<EOF", "    hello", "    EOF"]
    assert lines[8:] == ["▸ Editing app.py", "  $ edit /home/d/app.py"]


def test_history_lists_turns_and_launcher_actions(home):
    log = paths.turns_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"t": 1790000000, "prompt": "install ffmpeg", "summary": "Installed ffmpeg.", "snapshot": 4},
            {"t": 1790000060, "kind": "local", "prompt": "passwords", "result": "Opened Passwords."},
            {"t": 1790000120, "prompt": "set up docker", "stopped": True}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = watch.history_lines()
    assert any("install ffmpeg" in x and "Installed ffmpeg." in x and "restore point 4" in x for x in out)
    assert any("passwords: Opened Passwords." in x for x in out)
    assert any("set up docker" in x and "Stopped." in x for x in out)


def _agentd(home, events):
    """A socket where agentd would be; it sends `events` to whoever connects, then stays open."""
    import socket
    import threading
    path = paths.socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)
    conns = []

    def serve():
        c, _ = srv.accept()
        conns.append(c)
        for ev in events:
            c.sendall((json.dumps(ev) + "\n").encode())

    threading.Thread(target=serve, daemon=True).start()
    return srv, conns


def _turn_file(home, turn=3):
    f = home / "state" / "turns" / f"1000-{turn}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"kind": "turn_start", "turn": turn, "prompt": "set up docker", "t": 0}) + "\n")
    return f


def test_following_a_turn_ends_with_it(home):
    import io
    srv, _ = _agentd(home, [
        {"type": "event", "kind": "tool", "turn": 3, "name": "Bash", "input": {"command": "sudo pacman -S docker"}},
        {"type": "event", "kind": "tool", "turn": 4, "name": "Bash", "input": {"command": "ls"}},
        {"type": "event", "kind": "turn_end", "turn": 3, "seconds": 4, "summary": "Installed docker."},
    ])
    out = io.StringIO()
    assert watch.follow(_turn_file(home), out) is False
    text = out.getvalue()
    assert "set up docker" in text and "sudo pacman -S docker" in text and "Installed docker." in text
    assert "$ ls" not in text   # another turn's events
    srv.close()


def test_a_key_closes_the_drawer_while_it_still_follows(home):
    import io
    import os
    srv, _ = _agentd(home, [])   # the turn never ends
    r, w = os.pipe()
    os.write(w, b"\x1b")               # Esc
    out = io.StringIO()
    assert watch.follow(_turn_file(home), out, keys=r) is True
    assert "set up docker" in out.getvalue()
    os.close(r), os.close(w)
    srv.close()


def test_details_give_each_step_its_reason_and_what_it_followed():
    events = [
        {"kind": "turn_start", "prompt": "set up the tunnel", "t": 0},
        {"kind": "tool", "name": "WebFetch", "input": {"url": "https://wireguard.com/quickstart"},
         "because": "Checking the vendor's steps first."},
        {"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S wireguard-tools"},
         "because": "The tunnel needs its tools.", "after": {"label": "wireguard.com/quickstart", "kind": "web",
                                                              "text": "after reading wireguard.com/quickstart"}},
        {"kind": "turn_end", "seconds": 4, "summary": "Installed wireguard-tools.",
         "read": [{"label": "notes.txt", "kind": "file", "outside": False},
                  {"label": "wireguard.com/quickstart", "kind": "web", "outside": True},
                  {"label": "lease.pdf", "kind": "file", "outside": True, "origin": "rent-portal.example"}]},
    ]
    lines = list(watch.Renderer().lines(events))
    i = lines.index("▸ Installing wireguard-tools  [system]")
    assert lines[i + 1] == "  $ sudo pacman -S wireguard-tools"
    assert lines[i + 2:i + 4] == ["  why: The tunnel needs its tools.", "  after reading wireguard.com/quickstart"]
    assert "  why: Checking the vendor's steps first." in lines
    assert lines[-3:] == ["  read", "    yours    notes.txt",
                          "    outside  wireguard.com/quickstart, lease.pdf (from rent-portal.example)"]


def test_details_list_the_pictures_shown_in_words():
    events = [
        {"kind": "turn_start", "prompt": "start the vpn", "t": 0},
        {"kind": "card", "card": {"id": "card-1", "title": "How you're connected", "partial": True, "text": "draft"}},
        {"kind": "card", "card": {"id": "card-1", "title": "t", "text": "How you're connected: Laptop → Router\nAll answers."}},
        {"kind": "turn_end", "seconds": 4, "summary": "Started the VPN."},
        {"kind": "card", "card": {"id": "card-2", "receipt": True, "text": "Before: nothing\nAfter: VPN tunnel (wg0) [new]"}},
    ]
    lines = list(watch.Renderer().lines(events))
    assert "draft" not in "\n".join(lines)
    assert lines[1:4] == ["  picture", "  ┆ How you're connected: Laptop → Router", "  ┆ All answers."]
    assert lines[-3:] == ["  picture (what changed)", "  ┆ Before: nothing", "  ┆ After: VPN tunnel (wg0) [new]"]


# -- the AI rests (rest.py): a turn the limit stopped --

CUT_OFF = "Claude hit its limit partway, after changing 2 files. It carries on at 15:00."


@pytest.mark.parametrize("fields, said", [
    ({"why": "limit", "window": "five_hour", "until": _at(2026, 10, 1, 15)}, "  stopped at its limit until 15:00"),
    ({"why": "limit", "window": "seven_day", "until": _at(2026, 10, 5, 9)},
     "  stopped at its weekly limit until Monday 09:00"),
    ({"why": "spend", "window": "overage", "until": _at(2026, 10, 2, 8)},
     "  stopped at its spending limit until Friday 08:00"),
    ({"why": "spend", "window": None, "until": None}, "  stopped at its spending limit"),
    ({"why": "limit", "window": None, "until": None}, "  stopped at its limit"),
])
def test_details_say_where_the_limit_stopped_a_turn_in_their_own_words(utc, fields, said):
    t = _at(2026, 10, 1, 12, 30)
    events = [
        {"kind": "turn_start", "prompt": "tidy my notes", "t": t},
        {"kind": "rest", "provider": "claude", "t": t + 5, "text": "You've hit your session limit · resets 3pm", **fields},
        {"kind": "turn_end", "seconds": 7, "summary": "Wrote 2 files.", "changed": True, "requeued": True,
         "line": CUT_OFF},
    ]
    lines = list(watch.Renderer().lines(events))
    assert lines[1] == said
    assert "session limit" not in "\n".join(lines) and "You've hit" not in "\n".join(lines)   # the provider's words
    assert lines[2] == f"  {CUT_OFF}   7 s"           # the cut-off line closes it like any other turn's


def test_the_rest_line_is_dim_and_survives_odd_fields():
    r = watch.Renderer(color=True)
    assert list(r.one({"kind": "rest", "provider": "claude", "why": "limit", "until": None})) == [
        f"{watch.DIM}  stopped at its limit{watch.RESET}"]
    assert list(r.one({"kind": "rest"})) == [f"{watch.DIM}  stopped at its limit{watch.RESET}"]
    assert list(r.one({"kind": "rest", "until": "soon", "t": "then", "window": "seven_day"})) == [
        f"{watch.DIM}  stopped at its weekly limit{watch.RESET}"]


def test_a_requeued_turn_end_prints_its_line_like_any_other():
    lines = list(watch.Renderer().lines([
        {"kind": "turn_end", "seconds": 2, "summary": "", "requeued": True, "changed": False,
         "line": "Claude is at its limit until 15:00. Your apps and files still work."}]))
    assert lines == ["  Claude is at its limit until 15:00. Your apps and files still work.   2 s"]


def test_history_ends_a_requeued_turn_with_its_cut_off_line(home):
    log = paths.turns_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"t": 1790000000, "prompt": "tidy my notes", "summary": CUT_OFF, "requeued": True, "snapshot": 5,
             "result": "", "ok": False},
            {"t": 1790000600, "prompt": "tidy my notes", "summary": "Tidied your notes.", "snapshot": 6}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = watch.history_lines()
    assert any("tidy my notes" in x and CUT_OFF in x and "restore point 5" in x for x in out)
    assert any("tidy my notes" in x and "Tidied your notes." in x and "restore point 6" in x for x in out)


def test_following_a_turn_the_limit_stops_ends_with_its_cut_off_line(home):
    import io
    srv, _ = _agentd(home, [
        {"type": "event", "kind": "tool", "turn": 3, "name": "Bash", "input": {"command": "ls"}},
        {"type": "event", "kind": "rest", "turn": 3, "provider": "claude", "why": "limit", "window": "five_hour",
         "until": time.time() + 3 * 3600, "text": "You've hit your session limit"},
        {"type": "event", "kind": "turn_end", "turn": 3, "seconds": 4, "summary": "", "requeued": True,
         "line": CUT_OFF},
        {"type": "event", "kind": "queued", "turn": 3, "prompt": "set up docker"},   # back at the front: not shown
    ])
    out = io.StringIO()
    assert watch.follow(_turn_file(home), out) is False
    text = out.getvalue()
    assert "  stopped at its limit until " in text and CUT_OFF in text and "You've hit" not in text
    srv.close()
