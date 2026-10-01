"""The viewer in the details drawer: `bombadil view`.

A click on a box in a picture opens what it names (a service's status, a package, a folder, a
text file) in the drawer. This shows that text: all of it if it fits, a window on it that scrolls
if it does not. Esc (or q) closes it, like the history drawer, which any key closes; the arrows,
the wheel, PageUp and PageDown and the space bar scroll. It is a few lines of Python instead of
`less` because less cannot leave on Esc (Esc starts its own key sequences) and was not on the
image at all.

Plain text only: a service's status or a listing has no colour worth keeping when the viewer
has to know how wide a line is. The keys come from the terminal (the drawer's own), not stdin,
so the text may be piped in.
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

MAX_BYTES = 2_000_000   # a bigger file shows its start and says so
RUN_TIMEOUT = 15

_SEQUENCES = {
    "[A": "up", "OA": "up", "[B": "down", "OB": "down",
    "[5~": "pgup", "[6~": "pgdn", "[H": "home", "OH": "home", "[1~": "home", "[F": "end", "OF": "end", "[4~": "end",
}
_PLAIN = {" ": "pgdn", "f": "pgdn", "b": "pgup", "j": "down", "\r": "down", "\n": "down", "k": "up",
          "g": "home", "G": "end", "q": "quit", "Q": "quit", "\x03": "quit", "\x04": "quit"}


def layout(text: str, cols: int) -> list[str]:
    """The text as screen rows: tabs spread, anything that is not a printable character blanked, long lines wrapped."""
    cols = max(8, cols)
    rows: list[str] = []
    for raw in (text or "").splitlines() or [""]:
        line = "".join(ch if ch.isprintable() else " " for ch in raw.expandtabs(8)).rstrip()
        if not line:
            rows.append("")
            continue
        while len(line) > cols:
            cut = line.rfind(" ", cols // 2, cols + 1)   # break at a space when there is one near the edge
            cut = cut + 1 if cut > 0 else cols
            rows.append(line[:cut].rstrip())
            line = line[cut:]
        rows.append(line)
    return rows


def move(top: int, key: str, total: int, room: int) -> int:
    """Where the window starts after a key. The last window shows the last rows."""
    last = max(0, total - room)
    step = {"up": -1, "down": 1, "pgup": -max(1, room - 1), "pgdn": max(1, room - 1)}.get(key)
    if step is not None:
        return min(max(0, top + step), last)
    return {"home": 0, "end": last}.get(key, top)


def frame(rows: list[str], top: int, room: int, cols: int) -> str:
    """One screen: the window of rows, then a footer saying where it is and how to leave."""
    shown = rows[top:top + room]
    at = f"{top + 1}-{top + len(shown)} of {len(rows)}"
    foot = f" {at}  ·  Esc closes  ·  arrows, wheel or space scroll "[:cols]
    body = "\r\n".join(shown + [""] * (room - len(shown)))
    return f"\033[H\033[2J{body}\r\n\033[7m{foot.ljust(cols)}\033[0m"


def page(text: str, keys: Iterator[str], size: Callable[[], tuple[int, int]], write: Callable[[str], None]) -> None:
    """Show text until a key says quit (or the keys run out). `size` is (columns, rows)."""
    cols, lines = size()
    rows = layout(text, cols)
    room = max(1, lines - 1)
    if len(rows) + 3 <= lines:       # the text, a blank line and the prompt fit: no scrolling
        write("\r\n".join(rows) + "\r\n\r\n\033[2mPress Esc to close.\033[0m")
        for _ in keys:       # any key closes a short text
            break
        return
    top = 0
    write("\033[?1049h\033[?25l")   # the drawer's own screen, no cursor
    try:
        write(frame(rows, top, room, cols))
        for key in keys:
            if key == "quit":
                break
            if size() != (cols, lines):                    # the drawer was resized: lay the text out again
                cols, lines = size()
                rows, room = layout(text, cols), max(1, lines - 1)
                top = min(top, max(0, len(rows) - room))
            top = move(top, key, len(rows), room)
            write(frame(rows, top, room, cols))
    finally:
        write("\033[?25h\033[?1049l")


def read_keys(fd: int) -> Iterator[str]:
    """Key names from a terminal: up, down, pgup, pgdn, home, end, quit; Esc alone is quit."""
    while True:
        try:
            b = os.read(fd, 1)
        except OSError:
            return
        if not b:
            return
        if b == b"\x1b":
            seq = ""
            while select.select([fd], [], [], 0.05)[0]:
                c = os.read(fd, 1).decode("latin-1")
                if not c:
                    return
                seq += c
                if len(seq) >= 2 and (c.isalpha() or c == "~"):
                    break
            if not seq:
                yield "quit"
            elif seq[0] in "[O":
                yield _SEQUENCES.get(seq, "")
            continue
        yield _PLAIN.get(b.decode("latin-1"), "")


def text_of(args: list[str]) -> str:
    """What to show: `--file PATH`, or `-- COMMAND ARG...` run here with its errors mixed in."""
    if args[:1] == ["--file"] and len(args) == 2:
        try:
            with open(args[1], "rb") as f:
                data = f.read(MAX_BYTES + 1)
        except OSError as e:
            return f"Could not read {Path(args[1]).name}: {e.strerror or e}."
        text = data[:MAX_BYTES].decode("utf-8", errors="replace")
        return text + ("\n\n(The rest of the file is not shown.)" if len(data) > MAX_BYTES else "")
    if args[:1] == ["--"] and len(args) > 1:
        try:
            r = subprocess.run(args[1:], capture_output=True, text=True, errors="replace", timeout=RUN_TIMEOUT,
                               stdin=subprocess.DEVNULL, check=False)
        except FileNotFoundError:
            return f"{Path(args[1]).name} is not installed."
        except subprocess.TimeoutExpired:
            return f"{Path(args[1]).name} did not answer in time."
        except OSError as e:
            return f"Could not run {Path(args[1]).name}: {e.strerror or e}."
        return (r.stdout + r.stderr).strip("\n") or "Nothing to show."
    return "Nothing to show."


def view(args: list[str]) -> int:
    """`bombadil view --file PATH` or `bombadil view -- COMMAND...`. Without a terminal it prints."""
    text = text_of(args)
    try:
        fd = os.open("/dev/tty", os.O_RDONLY) if not sys.stdin.isatty() else sys.stdin.fileno()
        import termios
        import tty
        old = termios.tcgetattr(fd)
    except (OSError, ImportError, ValueError):
        print(text)
        return 0

    def size() -> tuple[int, int]:
        try:
            t = os.get_terminal_size(sys.stdout.fileno())
            return t.columns, t.lines
        except OSError:
            return 80, 24

    def write(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        page(text, read_keys(fd), size, write)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return 0
