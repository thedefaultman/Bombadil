import json
import time

from bombadil import paths, watch


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


def test_history_reads_old_rows_and_ledger_v2_rows_alike(home):
    log = paths.turns_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        # Before v2: no id, no v, nothing else.
        {"t": 1790000000, "prompt": "install ffmpeg", "summary": "Installed ffmpeg.", "snapshot": 4},
        {"t": 1790000060, "kind": "local", "prompt": "passwords", "action": "app", "result": "Opened Passwords."},
        # v2 model and local rows, as agentd writes them.
        {"t": 1790000100, "prompt": "how full is my disk", "result": "212 GB free.", "snapshot": 41, "id": "1790000090000-3",
         "started": 1790000090.0, "seconds": 10.0, "origin": "typed", "tools": {"n": 2, "names": ["Bash"]},
         "model": "claude-sonnet-4-5", "cost": 0.04, "usage": None, "rate_limit": None, "v": 2, "n": 3},
        {"t": 1790000110, "kind": "local", "prompt": "stop", "action": "stop", "target": "", "result": "Stopping.",
         "ok": True, "v": 2, "verb": "stop", "via": "typed", "word": None, "of": "1790000090000-3", "of_snapshot": None},
        {"t": 1790000120, "kind": "local", "prompt": "my passwords", "action": "app", "target": "passwords",
         "result": "Opened Passwords.", "ok": True, "v": 2, "verb": "open", "via": "word", "word": "my passwords",
         "of": None, "of_snapshot": None},
        {"t": 1790000130, "kind": "improve", "id": "w1", "what": "word", "title": "Made “my passwords” open Passwords.",
         "group": "g1", "undo": {"op": "remove_word", "phrase": "my passwords"}, "undone": False, "v": 2},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = watch.history_lines()
    assert any("install ffmpeg" in x and "Installed ffmpeg." in x and "restore point 4" in x for x in out)
    assert any("passwords: Opened Passwords." in x for x in out)
    assert any("how full is my disk" in x and "212 GB free." in x and "restore point 41" in x for x in out)
    assert any(x.endswith("stop: Stopping.") for x in out)
    assert any("my passwords: Opened Passwords." in x for x in out)
    assert any(x.endswith("Made “my passwords” open Passwords.") for x in out)
    assert len([x for x in out if not x.startswith("  ")]) == 1   # one day header for the lot


def time_of(t):
    return time.strftime("%H:%M", time.localtime(t))


def test_the_trail_shows_as_a_dim_line_of_its_own(home):
    log = paths.turns_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"t": 1790000000, "kind": "improve", "title": "New apps no longer open on top of each other."})
                   + "\n" + json.dumps({"t": 1790000001, "kind": "improve"}) + "\n")
    out = watch.history_lines(color=True)
    assert out[1] == f"{watch.DIM}  {time_of(1790000000)}  New apps no longer open on top of each other.{watch.RESET}"
    assert "Changed something about itself." in out[2]   # a row with no title still says something


def test_history_shows_what_it_can_of_a_row_that_is_odd_and_skips_what_it_cannot(home):
    log = paths.turns_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"t": 1790000000, "kind": "from-the-future", "title": "x", "prompt": "never shown"}),
        json.dumps({"t": 1790000010, "kind": "local"}),                                     # a local row with nothing
        json.dumps({"t": 1790000020, "kind": "local", "prompt": "undo", "result": None}),
        json.dumps({"kind": "improve", "title": "no time on this one"}),                   # no t at all
        json.dumps({"t": "yesterday", "prompt": "a time that is a word", "summary": "Done."}),
        json.dumps({"t": 1790000030, "prompt": None, "result": None, "snapshot": 0}),       # a turn row missing its words
        json.dumps({"t": 1790000040, "prompt": "big", "result": "  \n", "stopped": None}),
        json.dumps({"t": 1e30, "prompt": "a time nobody can show"}),
        json.dumps([1, 2, 3]), json.dumps("a string"), json.dumps(7), json.dumps(None), "{not json", "",
        json.dumps({"t": 1790000050, "prompt": "last", "result": "all fine\nsecond line"}),
    ]
    log.write_text("\n".join(lines) + "\n")
    out = watch.history_lines()
    assert not any("never shown" in x for x in out)
    assert any("no time on this one" in x and "--:--" in x for x in out)
    assert any("a time that is a word" in x and "Done." in x and "--:--" in x for x in out)
    assert any(x.endswith("undo") for x in out)
    assert any("last" in x and "all fine" in x and "second line" not in x for x in out)
    assert any("a time nobody can show" in x for x in out)


def test_history_with_only_rows_it_cannot_show_says_nothing_yet(home):
    log = paths.turns_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"t": 1, "kind": "what-next"}) + "\n")
    assert watch.history_lines() == ["Nothing yet."]


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
