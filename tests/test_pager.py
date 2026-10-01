"""The details drawer's viewer (`bombadil view`): layout, scrolling, keys, and a real terminal run."""
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

from bombadil import pager

BOMBADIL = Path(__file__).resolve().parents[1] / "bin" / "bombadil"


def test_long_lines_wrap_at_a_space_near_the_edge_and_control_characters_are_blanked():
    rows = pager.layout("alpha beta gamma delta epsilon\tzeta\x1b[31m red", 16)
    assert all(len(r) <= 16 for r in rows)
    assert " ".join(rows).split() == "alpha beta gamma delta epsilon zeta [31m red".split()
    assert "\x1b" not in "".join(rows)
    assert pager.layout("", 40) == [""] and pager.layout("a\n\nb", 40) == ["a", "", "b"]
    assert pager.layout("x" * 50, 20) == ["x" * 20, "x" * 20, "x" * 10]    # no space to break at


def test_scrolling_stops_at_the_ends_and_a_page_keeps_one_row_of_context():
    assert pager.move(0, "up", 100, 20) == 0
    assert pager.move(0, "down", 100, 20) == 1
    assert pager.move(0, "pgdn", 100, 20) == 19
    assert pager.move(90, "pgdn", 100, 20) == 80           # the last window shows the last rows
    assert pager.move(50, "end", 100, 20) == 80 and pager.move(50, "home", 100, 20) == 0
    assert pager.move(5, "pgup", 100, 20) == 0
    assert pager.move(7, "", 100, 20) == 7                 # a key that means nothing


def test_a_frame_says_where_you_are_and_how_to_leave():
    rows = [f"row {i}" for i in range(50)]
    out = pager.frame(rows, 10, 9, 60)
    assert "row 10" in out and "row 18" in out and "row 19" not in out
    assert "11-19 of 50" in out and "Esc closes" in out


def run_page(text, keys, size=(40, 10)):
    out: list[str] = []
    pager.page(text, iter(keys), lambda: size, out.append)
    return "".join(out)


def test_text_that_fits_is_shown_whole_and_any_key_closes_it():
    out = run_page("one\ntwo\nthree", ["down"])
    assert "one\r\ntwo\r\nthree" in out and "Press Esc to close." in out
    assert "\033[?1049h" not in out                        # no screen of its own for a short text


def test_long_text_scrolls_in_the_drawers_own_screen_and_quit_gives_the_screen_back():
    text = "\n".join(f"line {i}" for i in range(40))
    out = run_page(text, ["down", "pgdn", "end", "quit", "down"])
    assert out.startswith("\033[?1049h") and out.rstrip().endswith("\033[?25h\033[?1049l")
    assert "1-9 of 40" in out and "2-10 of 40" in out and "32-40 of 40" in out
    assert out.count("\033[H\033[2J") == 4                 # the first screen and three keys; "down" after quit is not drawn


def test_the_screen_is_given_back_even_when_the_keys_run_out():
    out = run_page("\n".join(f"l{i}" for i in range(40)), [])
    assert out.rstrip().endswith("\033[?1049l")


def test_a_resize_lays_the_text_out_again():
    calls = [0]

    def size():
        calls[0] += 1
        return (40, 10) if calls[0] == 1 else (20, 6)        # the drawer shrank after the first screen

    out: list[str] = []
    pager.page("\n".join(f"line number {i}" for i in range(30)), iter(["down"]), size, out.append)
    shown = "".join(out)
    assert "1-9 of 30" in shown and "2-6 of 30" in shown


def test_keys_from_a_terminal_become_names_and_esc_alone_is_quit():
    r, w = os.pipe()
    os.write(w, b"\x1b[B\x1bOA\x1b[6~\x1b[5~\x1b[H\x1b[F j k q")
    os.close(w)
    keys = list(pager.read_keys(r))
    os.close(r)
    assert keys == ["down", "up", "pgdn", "pgup", "home", "end", "pgdn", "down", "pgdn", "up", "pgdn", "quit"]

    r, w = os.pipe()
    os.write(w, b"\x1b")              # Esc with nothing after it, for longer than a key sequence takes
    keys = []
    it = pager.read_keys(r)
    keys.append(next(it))
    os.close(w)
    os.close(r)
    assert keys == ["quit"]


def test_a_file_a_command_and_what_is_missing_each_give_text(tmp_path):
    f = tmp_path / "todo.txt"
    f.write_text("call mum\n")
    assert pager.text_of(["--file", str(f)]) == "call mum\n"
    assert "Could not read nope.txt" in pager.text_of(["--file", str(tmp_path / "nope.txt")])
    assert pager.text_of(["--", "echo", "hi"]) == "hi"
    assert pager.text_of(["--", "sh", "-c", "echo out; echo err >&2"]) == "out\nerr"
    assert pager.text_of(["--", "no-such-program-bombadil"]) == "no-such-program-bombadil is not installed."
    assert pager.text_of(["--", "true"]) == "Nothing to show."
    assert pager.text_of([]) == "Nothing to show."


def test_a_big_file_shows_its_start_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(pager, "MAX_BYTES", 100)
    f = tmp_path / "big.log"
    f.write_text("x" * 500)
    text = pager.text_of(["--file", str(f)])
    assert text.startswith("x" * 100) and "x" * 101 not in text and "rest of the file is not shown" in text


def test_a_command_that_hangs_is_given_up_on(monkeypatch):
    monkeypatch.setattr(pager, "RUN_TIMEOUT", 0.3)
    assert pager.text_of(["--", "sleep", "5"]) == "sleep did not answer in time."


def test_without_a_terminal_the_text_is_printed(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello\n")
    r = subprocess.run([sys.executable, str(BOMBADIL), "view", "--file", str(f)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, start_new_session=True, timeout=20)
    assert r.returncode == 0 and "hello" in r.stdout


def _read_until(fd, needle: str, seconds=8.0) -> str:
    got = b""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if select.select([fd], [], [], 0.1)[0]:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            got += chunk
            if needle.encode() in got:
                break
    return got.decode(errors="replace")


def test_in_a_real_terminal_the_arrows_scroll_and_esc_alone_closes_it(tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("\n".join(f"line {i}" for i in range(100)) + "\n")
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 50, 0, 0))

    def own_tty():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    p = subprocess.Popen([sys.executable, str(BOMBADIL), "view", "--file", str(f)], stdin=slave, stdout=slave,
                         stderr=slave, preexec_fn=own_tty, close_fds=True)
    os.close(slave)
    try:
        first = _read_until(master, "Esc closes")
        assert "line 0" in first and "1-11 of 100" in first
        os.write(master, b"\x1b[B")
        assert "2-12 of 100" in _read_until(master, "2-12 of 100")
        os.write(master, b" ")
        assert "12-22 of 100" in _read_until(master, "12-22 of 100")
        os.write(master, b"\x1b[F")
        assert "90-100 of 100" in _read_until(master, "90-100 of 100")
        os.write(master, b"\x1b")
        assert p.wait(timeout=5) == 0
    finally:
        if p.poll() is None:
            p.kill()
        os.close(master)
