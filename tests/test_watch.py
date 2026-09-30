import json

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
            {"t": 1790000090, "kind": "improve", "prompt": "noticed a repeat", "what": "x"},
            {"t": 1790000120, "prompt": "set up docker", "stopped": True}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = watch.history_lines()
    assert any("install ffmpeg" in x and "Installed ffmpeg." in x and "restore point 4" in x for x in out)
    assert any("passwords: Opened Passwords." in x for x in out)
    assert any("set up docker" in x and "Stopped." in x for x in out)
    assert not any("noticed a repeat" in x for x in out)


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
