"""What "this" is: the window Hyprland says is active, and what its programs have open.

A fake /proc is built in tmp: each process a stat line (ppid, process group, the terminal's
foreground group, start time), its open files as fd symlinks, its cwd as a symlink and its
cmdline, the way the kernel shows them.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bombadil.brain import this


class FakeProc:
    def __init__(self, root):
        self.root = root
        root.mkdir()

    def add(self, pid, ppid, start, cwd=None, fds=(), argv=("prog",), pgrp=None, tpgid=-1, comm="prog"):
        d = self.root / str(pid)
        (d / "fd").mkdir(parents=True)
        pgrp = pid if pgrp is None else pgrp
        # state ppid pgrp session tty_nr tpgid, then 13 fields, then starttime (field 22).
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pgrp} {pgrp} 34816 {tpgid} " + "0 " * 13
                                + f"{start} 0 0\n")
        (d / "cmdline").write_bytes(b"\0".join(os.fsencode(a) for a in argv) + b"\0")
        if cwd is not None:
            os.symlink(cwd, d / "cwd")
        for i, target in enumerate(fds):
            os.symlink(target, d / "fd" / str(i + 3))
        return d


def _file(path, text="x", mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return str(path)


@pytest.fixture
def proc(home):
    return FakeProc(home / ".fakeproc")


def _ref(win, proc, home, **k):
    return this.ref_for(win, proc=proc.root, home=str(home), **k)


def test_an_app_window_is_the_app_but_the_brain_is_not_about_itself(home, proc):
    assert _ref({"class": "bombadil-app-passwords", "pid": 5}, proc, home) == "app:passwords"
    assert _ref({"class": "bombadil-app-brain", "pid": 5}, proc, home) is None
    assert _ref({"class": "bombadil-app-", "pid": 5}, proc, home) is None
    assert _ref({}, proc, home) is None
    assert _ref(None, proc, home) is None
    assert _ref({"class": "org.pwmt.zathura", "pid": 0}, proc, home) is None
    assert _ref({"class": "org.pwmt.zathura", "pid": "12"}, proc, home) is None


PAGES = [
    {"type": "service_worker", "url": "https://rent.example/sw.js", "title": "sw"},
    {"type": "page", "url": "https://rent.example/lease#terms", "title": "Lease renewal 2026"},
    {"type": "page", "url": "https://wiki.archlinux.org/title/Snapper", "title": "Snapper - ArchWiki"},
    {"type": "page", "url": "chrome://newtab/", "title": "New Tab"},
]


def test_the_browser_is_the_page_in_its_active_tab(home, proc):
    win = {"class": "bombadil-browser", "pid": 7, "title": "Lease renewal 2026 - Chromium"}
    assert _ref(win, proc, home, pages=lambda: PAGES) == "https://rent.example/lease#terms"
    # The window's title names the tab when the first page is not the one in front.
    win["title"] = "Snapper - ArchWiki - Chromium"
    assert _ref(win, proc, home, pages=lambda: PAGES) == "https://wiki.archlinux.org/title/Snapper"
    both = [{"type": "page", "url": "https://snapper.example/", "title": "Snapper"}, *PAGES]
    assert _ref(win, proc, home, pages=lambda: both) == "https://wiki.archlinux.org/title/Snapper"
    assert _ref({"class": "chromium", "title": "whatever"}, proc, home, pages=lambda: PAGES) \
        == "https://rent.example/lease#terms"
    # A new tab or settings is not a page the brain knows; no debugging port, nothing.
    win["title"] = "New Tab - Chromium"
    assert _ref(win, proc, home, pages=lambda: PAGES) is None
    assert _ref(win, proc, home, pages=lambda: []) is None
    assert this.tab_url([{"type": "page"}, "junk", None]) is None


def test_the_debugging_port_is_asked_directly_and_briefly(home, monkeypatch):
    class Tabs(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow":
                time.sleep(2)
            body = json.dumps(PAGES).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Tabs)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # A proxy in the environment must not get in the way of a port on this machine.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert this.devtools_pages(base + "/json") == PAGES
        t0 = time.monotonic()
        assert this.devtools_pages(base + "/slow", timeout=0.3) == []
        assert time.monotonic() - t0 < 1.5
    finally:
        server.shutdown()
        server.server_close()
    assert this.devtools_pages(base + "/json") == []   # nothing listening


def test_a_terminal_is_the_folder_its_shell_is_in(home, proc):
    project = home / "Projects" / "bombadil"
    project.mkdir(parents=True)
    proc.add(100, 1, 10, cwd=home, argv=["foot"])
    proc.add(101, 100, 20, cwd=project, argv=["-bash"], tpgid=101,
             fds=["/dev/pts/3", "/dev/pts/3", "/dev/pts/3"])
    assert _ref({"class": "foot", "pid": 100}, proc, home) == str(project)
    # Hyprland may give the class in another case; kitty's windows are terminals too.
    assert _ref({"class": "Kitty", "pid": 100}, proc, home) == str(project)


def test_a_terminal_running_an_editor_is_the_editors_file(home, proc):
    project = home / "Projects" / "bombadil"
    notes = _file(project / "notes.md")
    _file(project / ".notes.md.swp")
    proc.add(100, 1, 10, cwd=home, argv=["foot"])
    # The shell waits for nvim, which the terminal runs in the foreground (group 102).
    proc.add(101, 100, 20, cwd=project, argv=["bash"], tpgid=102)
    proc.add(102, 101, 30, cwd=project, argv=["nvim", "+12", "notes.md"], tpgid=102,
             fds=[str(project / ".notes.md.swp")])
    # nvim's language server is younger and in the same group, with no file of its own.
    proc.add(103, 102, 40, cwd=project, argv=["pyright-langserver", "--stdio"], pgrp=102, tpgid=102)
    # A job sent to the background is younger still, and not what is in front.
    proc.add(104, 101, 50, cwd=home, argv=["less", str(home / "other.txt")], tpgid=102,
             fds=[_file(home / "other.txt")])
    assert _ref({"class": "foot", "pid": 100}, proc, home) == notes


def test_a_terminal_program_with_a_file_open_is_that_file(home, proc):
    lease = _file(home / "Documents" / "lease 2026 ✓.pdf")
    proc.add(100, 1, 10, cwd=home, argv=["bombadil-terminal"])
    proc.add(101, 100, 20, cwd=home, argv=["bash"], tpgid=102)
    proc.add(102, 101, 30, cwd=home / "Documents", argv=["less"], tpgid=102, fds=[lease])
    assert _ref({"class": "bombadil-terminal", "pid": 100}, proc, home) == lease


def test_a_terminal_editing_a_dot_file_the_brain_keeps_is_that_file(home, proc):
    hypr_conf = _file(home / ".config" / "hypr" / "hyprland.lua")
    _file(home / ".local" / "state" / "nvim" / "log")
    proc.add(100, 1, 10, cwd=home, argv=["foot"])
    proc.add(101, 100, 20, cwd=home, argv=["bash"], tpgid=102)
    proc.add(102, 101, 30, cwd=home, argv=["nvim", "--clean", ".config/hypr/hyprland.lua"], tpgid=102,
             fds=[str(home / ".local" / "state" / "nvim" / "log")])
    assert _ref({"class": "foot", "pid": 100}, proc, home) == hypr_conf
    bashrc = _file(home / ".bashrc")
    proc.add(200, 1, 10, cwd=home, argv=["foot"])
    proc.add(201, 200, 20, cwd=home, argv=["bash"], tpgid=202)
    proc.add(202, 201, 30, cwd=home, argv=["less"], tpgid=202, fds=[bashrc])
    assert _ref({"class": "foot", "pid": 200}, proc, home) == bashrc


def test_a_terminal_program_whose_folder_is_hidden_falls_back_to_the_shells(home, proc):
    proc.add(100, 1, 10, cwd=home, argv=["foot"])
    proc.add(101, 100, 20, cwd=home / "Documents", argv=["bash"], tpgid=102)
    (home / "Documents").mkdir()
    d = proc.add(102, 101, 30, argv=["sudo", "nvim", "/etc/hosts"], tpgid=102)   # root's: no cwd to read
    assert not (d / "cwd").exists()
    assert _ref({"class": "foot", "pid": 100}, proc, home) == str(home / "Documents")


def test_a_terminal_in_a_folder_the_brain_leaves_out_is_in_the_folder_above_it(home, proc):
    deep = home / "Projects" / "web" / "node_modules" / "left-pad"
    deep.mkdir(parents=True)
    proc.add(100, 1, 10, argv=["alacritty"])
    proc.add(101, 100, 20, cwd=deep, argv=["zsh"], tpgid=101)
    assert _ref({"class": "Alacritty", "pid": 100}, proc, home) == str(home / "Projects" / "web")


def test_a_terminal_outside_home_or_without_a_shell(home, proc):
    proc.add(100, 1, 10, cwd="/usr", argv=["foot"])
    proc.add(101, 100, 20, cwd="/usr/share", argv=["bash"], tpgid=101)
    assert _ref({"class": "foot", "pid": 100}, proc, home) is None
    proc.add(200, 1, 10, cwd=home / "Music", argv=["foot"])     # a cwd that no longer exists
    assert _ref({"class": "foot", "pid": 200}, proc, home) == str(home)
    proc.add(300, 1, 10, cwd=home, argv=["foot"])
    assert _ref({"class": "foot", "pid": 300}, proc, home) == str(home)
    assert _ref({"class": "foot", "pid": 999}, proc, home) is None


def test_any_other_window_is_its_newest_open_file_under_home(home, proc):
    old = _file(home / "Documents" / "lease.pdf", mtime=1_700_000_000)
    new = _file(home / "Downloads" / "letter.pdf", mtime=1_700_000_500)
    newest = _file(home / "Documents" / "notes.txt", mtime=1_700_000_900)
    skipped = [
        _file(home / ".config" / "zathura" / "zathurarc", mtime=1_800_000_000),
        _file(home / ".local" / "share" / "zathura" / "history.db", mtime=1_800_000_000),
        _file(home / ".cache" / "thumb.png", mtime=1_800_000_000),
        _file(home / ".hidden.pdf", mtime=1_800_000_000),
        _file(home / "Projects" / "web" / "node_modules" / "x.js", mtime=1_800_000_000),
        _file(home / "Documents" / "draft.odt~", mtime=1_800_000_000),
        str(home / "Documents"),                  # a folder, not a file
        "socket:[4242]", "pipe:[17]", "anon_inode:[eventfd]",
        "/usr/lib/libc.so.6",                     # not yours
        str(home / "Documents" / "gone.pdf"),     # deleted since it was opened
    ]
    proc.add(200, 1, 10, fds=[old, *skipped, new])
    proc.add(201, 200, 20, fds=[newest, old])     # a helper process of the same app
    proc.add(300, 1, 30, fds=[_file(home / "Documents" / "someone-else.txt", mtime=1_900_000_000)])
    win = {"class": "org.pwmt.zathura", "pid": 200, "title": "lease.pdf"}
    assert this.open_files(this.tree(200, this.procs(proc.root)), proc.root, str(home)) == [newest, new, old]
    assert _ref(win, proc, home) == newest
    assert _ref({"class": "org.pwmt.zathura", "pid": 999}, proc, home) is None


def test_names_that_cannot_reach_the_brain_unchanged_are_left_out(home, proc):
    odd = os.fsencode(str(home / "Documents")) + b"/caf\xe9.txt"
    os.makedirs(home / "Documents", exist_ok=True)
    with open(odd, "w") as f:
        f.write("x")
    fd = proc.root / "400" / "fd"
    proc.add(400, 1, 10)
    os.symlink(odd, os.fsencode(str(fd)) + b"/9")
    assert _ref({"class": "gedit", "pid": 400}, proc, home) is None


def test_a_home_reached_through_a_symlink_is_named_as_home(home, tmp_path_factory, proc):
    real = tmp_path_factory.mktemp("var") / "home"
    lease = _file(real / "Documents" / "lease.pdf")
    link = tmp_path_factory.mktemp("h") / "me"
    os.symlink(real, link)
    proc.add(500, 1, 10, fds=[lease])
    assert this.ref_for({"class": "evince", "pid": 500}, proc=proc.root, home=str(link)) \
        == str(link / "Documents" / "lease.pdf")


def test_a_missing_proc_is_nothing_in_front(home, tmp_path):
    assert this.ref_for({"class": "foot", "pid": 100}, proc=tmp_path / "nope", home=str(home)) is None
    assert this.ref_for({"class": "gedit", "pid": 100}, proc=tmp_path / "nope", home=str(home)) is None


def test_a_torn_stat_line_is_skipped(home, proc):
    proc.add(100, 1, 10, argv=["foot"], cwd=home)
    d = proc.add(101, 100, 20, cwd=home / "Projects", argv=["bash"])
    (d / "stat").write_bytes(b"101 (ba\xffsh) S 100")     # cut short, and not UTF-8
    assert _ref({"class": "foot", "pid": 100}, proc, home) == str(home)


class Hypr:
    def __init__(self, answer, available=True):
        self.answer = answer
        self.available = available
        self.asked = []

    def request(self, cmd):
        self.asked.append(cmd)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_resolve_asks_hyprland_for_the_active_window(home):
    h = Hypr(json.dumps({"class": "bombadil-app-tracker", "pid": 1, "title": "Tracker"}))
    assert this.resolve(h) == "app:tracker" and h.asked == ["j/activewindow"]
    assert this.resolve(Hypr("{}")) is None
    assert this.resolve(Hypr("not json")) is None
    assert this.resolve(Hypr(RuntimeError("Hyprland is not running"))) is None
    assert this.resolve(Hypr("{}", available=False)) is None
    assert this.resolve() is None     # no Hyprland here at all
