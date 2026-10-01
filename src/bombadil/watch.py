"""`bombadil watch` and `bombadil history`: what a turn ran, in a terminal.

Clicking the line above the pill after a turn opens `bombadil watch` in the details drawer:
every step in plain words, the exact command under it, and the command's output. While a
turn runs it follows along live. `bombadil history` lists recent turns and what the pill did
without the model (open, undo), newest last.
"""

import json
import os
import select
import socket
import sys
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from . import narrate, paths, rest

AMBER, RED, DIM, BOLD, RESET = "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
CLAUDE_REJECTED = "The user doesn't want to proceed with this tool use"


def _color(on: bool):
    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if on else text
    return c


def read_log(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


class Renderer:
    """Turns a turn's events into lines for a terminal."""

    def __init__(self, color: bool = False, width: int = 100):
        self.c = _color(color)
        self.width = width
        self.steps: dict[str, str] = {}

    def lines(self, events: Iterable[dict]) -> Iterator[str]:
        for ev in events:
            yield from self.one(ev)

    def one(self, ev: dict) -> Iterator[str]:
        c = self.c
        kind = ev.get("kind")
        if kind == "turn_start":
            when = time.strftime("%a %H:%M:%S", time.localtime(ev.get("t", time.time())))
            yield c(BOLD, f"› {ev.get('prompt', '')}") + c(DIM, f"   {when}")
        elif kind == "snapshot":
            yield c(DIM, f"  restore point {ev.get('number')}")
        elif kind == "tool":
            step = narrate.tool_step(ev.get("name", ""), ev.get("input"))
            if step is None:
                return
            yield from self._step(step, _what_ran(ev), ev)
        elif kind == "file_change":
            yield from self._step(narrate.file_change_step(ev.get("changes") or []), "", ev)
            for ch in ev.get("changes") or []:
                if isinstance(ch, dict):
                    yield c(DIM, f"    {ch.get('kind', 'update')}: {ch.get('path', '')}")
        elif kind == "status" and ev.get("source") == "step" and ev.get("text") in ("Saving a restore point",):
            yield c(DIM, f"  {ev['text']}")
        elif kind == "tool_result":
            out = (ev.get("output") or "").rstrip()
            if out.startswith(CLAUDE_REJECTED):
                # What Claude Code tells the model about a tool call cut short by Stop.
                yield c(RED, "  │ stopped")
                return
            if out:
                shown = out.splitlines()
                for line in shown:
                    yield c(DIM, "  │ ") + line[: self.width * 3]
            if ev.get("error"):
                code = ev.get("exit_code")
                yield c(RED, "  │ failed" + (f" (exit {code})" if code not in (None, "") else ""))
        elif kind == "text":
            for line in (ev.get("text") or "").strip().splitlines():
                yield f"  {line}"
        elif kind == "error":
            yield c(RED, f"  ! {ev.get('text', '')}")
        elif kind == "card":
            # A picture shown during or after the turn, in the words it has for screen readers and copying.
            card = ev.get("card") if isinstance(ev.get("card"), dict) else {}
            if card.get("text") and not card.get("partial"):
                yield c(DIM, "  picture" + (" (what changed)" if card.get("receipt") else ""))
                for line in str(card["text"]).splitlines():
                    yield c(DIM, "  ┆ ") + line
        elif kind == "rest":
            # The account said no. Built from the fields, like every line about resting (rest.words):
            # the provider's own words stay in the log.
            found = rest.Rest(str(ev.get("provider") or ""), str(ev.get("why") or "limit"), ev.get("window"),
                              _number(ev.get("until")))
            yield c(DIM, "  stopped " + rest.words(found, found.provider, _number(ev.get("t")))["row"])
        elif kind == "turn_end":
            end = ev.get("line") or ev.get("summary") or "Done."
            yield c(BOLD, f"  {end}") + c(DIM, f"   {ev.get('seconds', 0)} s")
            if ev.get("irreversible"):
                yield c(RED, "  One step here cannot be undone.")
            yield from self._read(ev.get("read"))

    def _read(self, reads) -> Iterator[str]:
        """Everything the turn read, told apart: what is yours and what came from outside."""
        c = self.c
        reads = [r for r in reads if isinstance(r, dict)] if isinstance(reads, list) else []
        if not reads:
            return
        yield c(DIM, "  read")
        for outside in (False, True):
            names = [str(r.get("label", "")) + (f" (from {r['origin']})" if r.get("origin") else "")
                     for r in reads if bool(r.get("outside")) == outside]
            if names:
                yield "    " + ("outside  " if outside else "yours    ") + c(AMBER if outside else DIM, ", ".join(names))

    def _step(self, step: narrate.Step, ran: str = "", ev: dict | None = None) -> Iterator[str]:
        """The step in words, then exactly what ran: the plain words can be wrong, this is not.
        Marked steps show it in their colour, the rest dimmed."""
        c = self.c
        mark = {"system": c(AMBER, "  [system]"), "irreversible": c(RED, "  [cannot be undone]")}.get(step.risk or "", "")
        yield f"▸ {step.text}{mark}"
        exact = ran or step.command or ""
        colour = AMBER if step.risk == "system" else RED if step.risk else DIM
        for i, line in enumerate(exact.splitlines()):
            yield c(colour, ("  $ " if i == 0 else "    ") + line[: self.width * 3])
        # Why, in the agent's own words from just before it acted, and what it followed.
        because = str((ev or {}).get("because") or "")
        if because:
            yield c(DIM, f"  why: {because}")
        after = (ev or {}).get("after")
        if isinstance(after, dict) and after.get("text"):
            yield c(AMBER, f"  {after['text']}")


def _number(value) -> float | None:
    return value if isinstance(value, int | float) else None


def _what_ran(ev: dict) -> str:
    """A tool call's command (Bash, the inner script of `bash -lc`) or the file it touched."""
    a = ev.get("input") if isinstance(ev.get("input"), dict) else {}
    name = str(ev.get("name") or "")
    if name == "Bash" and a.get("command"):
        return narrate._unwrap(str(a["command"])).strip()
    path = a.get("file_path") or a.get("notebook_path") or a.get("path")
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read") and path:
        return f"{name.lower().removeprefix('multi').removeprefix('notebook')} {path}"
    return ""


def _terminal() -> tuple[bool, int]:
    tty = sys.stdout.isatty()
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 100
    return tty, width


@contextmanager
def _keys():
    """The terminal's keys one at a time, without Enter or echo (None without a terminal), so
    Esc closes the drawer while it still follows a turn."""
    if not sys.stdin.isatty():
        yield None
        return
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def follow(turn_file: Path, out=sys.stdout, keys: int | None = None) -> bool:
    """Print new events of a running turn as agentd sends them, until it ends. Returns True
    when a key on `keys` (a file descriptor) closed it first."""
    tty, width = _terminal()
    r = Renderer(color=tty, width=width)
    seen = len(read_log(turn_file))
    for line in r.lines(read_log(turn_file)):
        print(line, file=out)
    turn = None
    try:
        turn = int(turn_file.stem.split("-", 1)[1])
    except (IndexError, ValueError):
        pass
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        try:
            s.connect(str(paths.socket_path()))
        except OSError:
            return False
        # Anything written between reading the file and connecting is in the file now.
        for line in r.lines(read_log(turn_file)[seen:]):
            print(line, file=out, flush=True)
        buf = b""
        while True:
            ready, _, _ = select.select([s] + ([keys] if keys is not None else []), [], [])
            if keys is not None and keys in ready:
                os.read(keys, 64)
                return True
            chunk = s.recv(65536)
            if not chunk:
                return False
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "event" or ev.get("turn") != turn or ev.get("kind") in ("status", "turn_start"):
                    continue
                for line in r.one(ev):
                    print(line, file=out, flush=True)
                if ev.get("kind") == "turn_end":
                    return False


def show(turn_file: Path | None, live: bool = False, out=sys.stdout, wait: bool = False) -> int:
    """A turn's details; with wait, the drawer then stays until a key closes it. Esc (any key)
    also closes it while it still follows a running turn."""
    if turn_file is None or not turn_file.exists():
        print("No turns yet.", file=out)
        if wait:
            wait_for_key()
        return 1
    if live:
        with _keys() as fd:
            if follow(turn_file, out, keys=fd if wait else None):
                return 0
    else:
        tty, width = _terminal()
        for line in Renderer(color=tty, width=width).lines(read_log(turn_file)):
            print(line, file=out)
    if wait:
        wait_for_key()
    return 0


def history_lines(limit: int = 40, color: bool = False) -> list[str]:
    c = _color(color)
    rows = read_log(paths.turns_log())[-limit:]
    out = []
    day = None
    for e in rows:
        if not (paths.is_turn_row(e) or (isinstance(e, dict) and e.get("kind") == "local")):
            continue   # another kind of row (the self-improvement loop's) is not history of yours
        t = time.localtime(e.get("t", 0))
        d = time.strftime("%A %d %B", t)
        if d != day:
            out.append(c(BOLD, d))
            day = d
        when = time.strftime("%H:%M", t)
        if e.get("kind") == "local":
            out.append(c(DIM, f"  {when}  {e.get('prompt', '')}: {e.get('result', '')}"))
            continue
        if e.get("kind") == "dev":
            out.append(c(DIM, f"  {when}  {e.get('result', '')}"))
            continue
        end = e.get("summary") or ("Stopped." if e.get("stopped") else "")
        if not end:
            end = (e.get("result") or "").strip().splitlines()[0][:80] if (e.get("result") or "").strip() else ""
        snap = f"  (restore point {e['snapshot']})" if e.get("snapshot") else ""
        out.append(f"  {when}  {e.get('prompt', '')}" + (c(DIM, f"  {end}") if end else "") + c(DIM, snap))
    return out or ["Nothing yet."]


def wait_for_key(prompt: str = "Press Esc to close.") -> None:
    """Keep the drawer open until a key is pressed (only when there is a terminal)."""
    if not sys.stdin.isatty():
        return
    import termios
    import tty
    print(f"\n\033[2m{prompt}\033[0m", end="", flush=True)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def last_turn_file() -> Path | None:
    d = paths.state_dir() / "turns"
    logs = sorted(d.glob("*.jsonl"), key=lambda p: int(p.stem.split("-")[0])) if d.exists() else []
    return logs[-1] if logs else None
