"""What "this" is: the thing in front of you when you tap Super.

"brain" opens the Brain on it and "why is this here?" asks about it, so it has to be what
you are looking at, read from the machine at that moment and never guessed: Hyprland names
the active window, and /proc says what its programs have open.

  one of your apps          the app ("app:passwords")
  the browser               the page in its active tab, from Chromium's debugging port
  a terminal                the file the program running in it has open or was started on
                            (an editor's file), else the folder it is in
  anything else             the files its processes have open under your home, newest first

Files an app keeps for itself (its settings, caches, dot-files) are never "this", nor is
anything the brain leaves out. The /proc root and the window are arguments of the helpers,
so tests build a fake tree.
"""

import json
import os
import re
import stat
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..hypr import Hyprland
from . import rules

PROC = Path("/proc")
DEVTOOLS = "http://127.0.0.1:9222/json"   # hypr.PANELS starts the browser with this port
DEVTOOLS_TIMEOUT = 0.5
BROWSERS = {"bombadil-browser", "chromium", "chromium-browser"}
TERMINALS = {"foot", "footclient", "bombadil-terminal", "kitty", "alacritty"}
APP_CLASS = re.compile(r"^bombadil-app-([a-z0-9][a-z0-9-]{0,40})$")
BRAIN_APP = "brain"   # asking the Brain about itself means nothing
# A browser or an IDE can run hundreds of processes with thousands of files open; looking
# further than this would make the pill wait.
MAX_PROCS = 256
MAX_LINKS = 20000


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    pgrp: int
    tpgid: int    # the foreground process group of its terminal, -1 without one
    start: int    # clock ticks since boot: bigger is younger

    @property
    def foreground(self) -> bool:
        """Is it what its terminal is running right now (not the waiting shell, not a job
        sent to the background)?"""
        return self.tpgid > 0 and self.pgrp == self.tpgid


def resolve(hypr=None) -> str | None:
    """The ref of what is in front (a path, a URL or "app:<name>"), or None. Never raises:
    no Hyprland, no window or an odd answer all mean nothing is in front."""
    try:
        h = hypr if hypr is not None else Hyprland()
        if not h.available:
            return None
        win = json.loads(h.request("j/activewindow") or "null")
        return ref_for(win)
    except Exception:  # noqa: BLE001 - "this" is a nicety; the pill works without it
        return None


def ref_for(win, proc: Path = PROC, home: str | None = None, pages=None) -> str | None:
    """The ref for one window as Hyprland describes it ({"class", "pid", "title", ...}).
    `pages()` lists the browser's tabs (default: its debugging port)."""
    if not isinstance(win, dict) or not win:
        return None
    cls = str(win.get("class") or win.get("initialClass") or "").lower()
    m = APP_CLASS.match(cls)
    if m:
        return None if m.group(1) == BRAIN_APP else f"app:{m.group(1)}"
    if cls in BROWSERS:
        return tab_url((pages or devtools_pages)(), str(win.get("title") or ""))
    pid = win.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    home = (home if home is not None else str(paths.home())).rstrip("/")
    if not home:
        return None
    if cls in TERMINALS:
        return terminal_ref(pid, proc, home)
    found = open_files(tree(pid, procs(proc)), proc, home)
    return found[0] if found else None


# -- the browser --

def devtools_pages(url: str = DEVTOOLS, timeout: float = DEVTOOLS_TIMEOUT) -> list:
    """Chromium's open tabs and workers, most recently used first; [] when it does not answer."""
    # Never through a proxy: the port is on this machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as r:
            data = json.loads(r.read(4 << 20))
    except Exception:  # noqa: BLE001 - not running, no debugging port, slow, or not Chromium
        return []
    return data if isinstance(data, list) else []


def tab_url(pages: list, title: str = "") -> str | None:
    """The active tab's URL: the page whose title the window shows ("Lease renewal 2026 -
    Chromium"), else the first page. None when it is not a web page (a new tab, settings)."""
    tabs = [p for p in pages if isinstance(p, dict) and p.get("type") == "page"]
    if not tabs:
        return None
    pick, best = tabs[0], 0
    for p in tabs:
        # The longest title that fits: "Foo - Bar - Chromium" is the tab "Foo - Bar", not "Foo".
        t = str(p.get("title") or "")
        if len(t) > best and (title == t or title.startswith(t + " - ")):
            pick, best = p, len(t)
    url = str(pick.get("url") or "")
    return url if url.lower().startswith(("http://", "https://")) else None


# -- processes --

def _stat(proc: Path, pid: int) -> Proc | None:
    try:
        raw = (proc / str(pid) / "stat").read_bytes().decode(errors="replace")
        # comm is in parentheses and may itself contain spaces and parentheses.
        f = raw[raw.rindex(")") + 2:].split()
        return Proc(pid, int(f[1]), int(f[2]), int(f[5]), int(f[19]))
    except (OSError, ValueError, IndexError):
        return None


def procs(proc: Path = PROC) -> dict[int, Proc]:
    out = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for name in names:
        if name.isdigit():
            p = _stat(proc, int(name))
            if p is not None:
                out[p.pid] = p
    return out


def tree(root: int, table: dict[int, Proc]) -> list[int]:
    """`root` and its descendants, nearest first, at most MAX_PROCS of them."""
    children: dict[int, list[int]] = {}
    for p in table.values():
        children.setdefault(p.ppid, []).append(p.pid)
    out, todo, seen = [], [root], {root}
    while todo and len(out) < MAX_PROCS:
        pid = todo.pop(0)
        out.append(pid)
        for c in sorted(children.get(pid, [])):
            if c not in seen:
                seen.add(c)
                todo.append(c)
    return out


# -- files --

def _yours(path: str, home: str, real_home: str, dots: bool = False) -> str | None:
    """`path` as the brain names it, when it is one of your things; else None. With `dots`,
    the dot-files the brain keeps (~/.bashrc, ~/.config/hypr) count too: a terminal program
    has them open because you asked it to."""
    if not path.startswith("/"):
        return None   # socket:[123], pipe:[45], anon_inode:…
    try:
        path.encode()
    except UnicodeEncodeError:
        return None   # a name that is not UTF-8 cannot reach the brain unchanged
    path = os.path.normpath(path)
    if real_home != home and path.startswith(real_home + "/"):
        path = home + path[len(real_home):]   # /var/home/you -> /home/you
    if not path.startswith(home + "/"):
        return None
    # Dot-folders are where apps keep their settings, caches and state (~/.config, ~/.cache,
    # ~/.local): an app holding its own files open is not you looking at them.
    if not dots and any(part.startswith(".") for part in path[len(home) + 1:].split("/")):
        return None
    if rules.classify(path, home).kind != "thing":
        return None
    return path


def open_files(pids: list[int], proc: Path, home: str, dots: bool = False) -> list[str]:
    """Regular files under home that these processes have open, newest change first."""
    real_home = os.path.realpath(home)
    found: dict[str, float] = {}
    budget = MAX_LINKS
    for pid in pids:
        fd_dir = proc / str(pid) / "fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue   # gone, or not ours to look at
        for fd in fds[:budget]:
            budget -= 1
            try:
                target = os.readlink(fd_dir / fd)
            except OSError:
                continue
            path = _yours(target, home, real_home, dots)
            if path is None or path in found:
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue   # deleted since it was opened: "/path (deleted)"
            if stat.S_ISREG(st.st_mode):
                found[path] = st.st_mtime
        if budget <= 0:
            break
    return sorted(found, key=lambda p: (-found[p], p))


def _cwd(pid: int, proc: Path) -> str | None:
    try:
        return os.readlink(proc / str(pid) / "cwd")
    except OSError:
        return None


def argument_file(pid: int, proc: Path, home: str) -> str | None:
    """The file a program was started on (`nvim notes.md`): most editors read the file and
    close it, so it is not among their open files."""
    try:
        argv = (proc / str(pid) / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
    except OSError:
        return None
    cwd = _cwd(pid, proc)
    real_home = os.path.realpath(home)
    for raw in argv[1:]:
        arg = raw.decode(errors="surrogateescape")
        if not arg or arg.startswith(("-", "+")):
            continue   # flags, and vim's +42
        if not arg.startswith("/"):
            if cwd is None or not cwd.startswith("/"):
                continue
            arg = os.path.join(cwd, arg)
        path = _yours(arg, home, real_home, dots=True)
        if path is not None and os.path.isfile(path):
            return path
    return None


def folder_ref(path: str | None, home: str) -> str | None:
    """A working folder as a thing: the folder itself, or the nearest one above it the brain
    keeps (a shell in node_modules is in the project). None outside home."""
    if not path or not path.startswith("/"):
        return None
    try:
        path.encode()
    except UnicodeEncodeError:
        return None
    path = os.path.normpath(path)
    real_home = os.path.realpath(home)
    if real_home != home and (path == real_home or path.startswith(real_home + "/")):
        path = home + path[len(real_home):]
    while path == home or path.startswith(home + "/"):
        if rules.classify(path, home).kind == "thing" and os.path.isdir(path):
            return path
        path = os.path.dirname(path)
    return None


def terminal_ref(pid: int, proc: Path, home: str) -> str | None:
    """What a terminal window shows: the file its running program has open or was started
    on, else the folder it is in. The program is the youngest process in the foreground of
    its terminal (the editor, not the shell waiting for it, nor a job left in the background)."""
    table = procs(proc)
    inside = [p for p in tree(pid, table) if p != pid and p in table]
    if not inside:
        return folder_ref(_cwd(pid, proc), home)
    inside.sort(key=lambda p: (table[p].start, p), reverse=True)
    front = [p for p in inside if table[p].foreground] or inside
    for p in front:
        found = open_files([p], proc, home, dots=True)
        if found:
            return found[0]
    for p in front:
        found = argument_file(p, proc, home)
        if found:
            return found
    # The folder of what runs in front, else of the shell behind it (a program run with
    # sudo keeps its folder to itself).
    for p in front + [p for p in inside if p not in front]:
        found = folder_ref(_cwd(p, proc), home)
        if found:
            return found
    return None
