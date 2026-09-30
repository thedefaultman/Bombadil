"""Focus and "why is this here?": fed the way the service feeds the brain (watcher events,
turns.jsonl rows, Chromium's visits and downloads), then asked what a person would ask."""

import json
import os
import subprocess
import time

import pytest

from bombadil.brain import actors, describe, focus, rules
from bombadil.brain.ingest import Ingest
from bombadil.brain.store import Store


KINDS = {"you", "turn", "session", "app", "system", "unknown"}


def local(y, mo, d, h=12, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def lines_of(f):
    """Every line the Brain window draws a dot for."""
    slots = f["slots"]
    out = [i for k in ("came_from", "read_with", "used_with") for i in slots[k]["items"]]
    return out + slots["changes"]["items"] + ((f["children"] or {}).get("items") or [])


NOW = local(2026, 10, 1, 12, 0)          # Thursday
MON, TUE = local(2026, 9, 28, 10, 0), local(2026, 9, 29, 14, 2)
DEV = "/user.slice/user-1000.slice/user@1000.service/bombadil.slice/bombadil-dev.slice"
FOOT = [[5, "nvim", "nvim"], [4, "bash", "bash"], [3, "foot", "foot"], [2, "Hyprland", "Hyprland"]]
FILES = [[6, "nautilus", "nautilus"], [2, "Hyprland", "Hyprland"]]
CHROME = [[7, "chromium", "chromium --class=bombadil-browser"], [2, "Hyprland", "Hyprland"]]


def unit(n):
    return f"bombadil-turn-{n}-1-1727429990"


class Brain:
    def __init__(self, home):
        self.home = home
        self.store = Store(home / "state" / "brain.db")
        (home / "sessions.json").write_text(json.dumps([{"unit": "dev-builder", "name": "builder"},
                                                        {"unit": "dev-flaky", "name": "flaky-snapshot"},
                                                        {"unit": "dev-kit", "name": "kit"}]))
        git = rules.GitIgnore(runner=lambda *a, **k: None)
        self.ingest = Ingest(self.store, str(home), actors.Sessions(home / "sessions.json"), git=git, xattrs=False)

    def file(self, rel, content, t, opened=False):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
        self.stamp(p, t, opened)
        return p

    def stamp(self, p, t, opened=False):
        os.utime(p, (t + 60 if opened else t, t))

    def ev(self, op, path, t, cgroup, chain, **extra):
        return {"op": op, "path": str(path), "t": t, "uid": 1000, "pid": chain[0][0], "comm": chain[0][1],
                "cgroup": cgroup, "chain": chain, "dir": False, **extra}

    def you(self, op, path, t, chain=FOOT, **extra):
        self.ingest.apply([self.ev(op, path, t, "/user.slice/user-1000.slice/session-1.scope", chain, **extra)])

    def session(self, op, path, t, name, project="bombadil"):
        cg = f"{DEV}/bombadil-dev-{project}.slice/dev-{name}.scope"
        self.ingest.apply([self.ev(op, path, t, cg, [[9, "python3", "python3"], [8, "claude", "claude"]])])

    def turn(self, n, prompt, t, wrote=(), made=(), read=()):
        """A turn as it happens: its writes seen by the watcher while it runs, then its row."""
        cg = f"/user.slice/user-1000.slice/user@1000.service/app.slice/{unit(n)}.scope"
        chain = [[100 + n, "bash", "bash -c"], [99, "claude", "claude -p"]]
        evs = []
        for p in made:
            evs += [self.ev("create", p, t, cg, chain), self.ev("write", p, t + 1, cg, chain)]
        evs += [self.ev("write", p, t + 2, cg, chain) for p in wrote]
        self.ingest.apply(evs)
        row = {"n": n, "unit": unit(n), "t": t + 30, "started": t - 5,
               "files": {"wrote": [str(p) for p in [*made, *wrote]], "read": [str(p) for p in read]}}
        if prompt:
            row["prompt"] = prompt
        return self.ingest.turn_row(row, n)

    def found(self, p):
        return self.ingest.found(str(p), os.stat(p), p.is_dir())

    def focus(self, ref, **kw):
        f = focus.focus(self.store, str(ref) if not isinstance(ref, int) else ref, str(self.home), now=NOW, **kw)
        json.dumps(f)   # it goes over brain.sock as it is
        for line in lines_of(f or {"slots": {k: {"items": []} for k in ("came_from", "read_with", "used_with",
                                                                        "changes")}, "children": None}):
            assert line["actor"] in KINDS, line   # the window colours a dot by it
        return f

    def why(self, ref):
        return focus.why(self.store, str(ref), str(self.home), now=NOW)


@pytest.fixture
def brain(home, monkeypatch):
    mounts = home / "mounts"
    mounts.write_text("/dev/vda / ext4 rw,relatime 0 0\n")
    monkeypatch.setattr(focus, "MOUNTS", mounts)
    monkeypatch.setattr(focus, "_mounts", (0.0, []))
    b = Brain(home)
    yield b
    b.store.close()


def titles(slot):
    return [(i["title"], i["why"]) for i in slot["items"]]


# -- why is this here? --

def test_a_file_the_machine_made_says_which_turn_and_that_nothing_opened_it(brain):
    wg = brain.home / "setup-wg.sh"
    wg.write_text("#!/bin/sh\nwg-quick up wg0\n")
    brain.turn(41, "install the VPN", MON, made=[wg])
    brain.stamp(wg, MON)
    assert brain.why(wg) == "Made by the machine in turn 41, “install the VPN”, on Monday. Nothing has opened it since."

    f = brain.focus(wg)
    t = f["thing"]
    assert (t["kind"], t["ref"], t["title"], t["exists"], t["opened"], t["private"]) == \
        ("file", str(wg), "setup-wg.sh", True, False, False)
    assert t["made"] == "Made by the machine in turn 41, “install the VPN”, on Monday." and t["last"] == ""
    assert t["size"] == len("#!/bin/sh\nwg-quick up wg0\n")    # from the disk: the watcher's events carried none
    assert t["area"]["title"] == "Home"
    assert f["preview"] == {"type": "text", "path": str(wg), "private": False}
    [line] = f["slots"]["came_from"]["items"]
    assert (line["title"], line["why"], line["kind"], line["ref"]) == \
        ("the machine, turn 41", "“install the VPN”, Mon 10:00", "turn", "turn:41")
    # The create and the close-write that follows it are one change, on Monday.
    ch = f["slots"]["changes"]
    assert ch["count_week"] == 1 and ch["days"] == [0, 0, 0, 1, 0, 0, 0] and ch["more"] == 0
    assert ch["items"][0]["kind"] == "create"
    assert ch["items"][0]["why"] == "made by the machine in turn 41, “install the VPN”"
    assert f["children"] is None and f["description"] is None

    brain.stamp(wg, MON, opened=True)
    assert brain.why(wg) == "Made by the machine in turn 41, “install the VPN”, on Monday."
    assert brain.focus(wg)["thing"]["opened"] is True


def test_nothing_is_said_about_opening_on_a_noatime_mount(brain, monkeypatch):
    (brain.home / "mounts").write_text(f"/dev/vda / ext4 rw,relatime 0 0\n/dev/vdb {brain.home} btrfs rw,noatime 0 0\n")
    wg = brain.home / "setup-wg.sh"
    wg.write_text("x")
    brain.turn(41, "install the VPN", MON, made=[wg])
    brain.stamp(wg, MON)
    assert brain.why(wg) == "Made by the machine in turn 41, “install the VPN”, on Monday."
    assert brain.focus(wg)["thing"]["opened"] is None


def test_a_download_says_the_page_you_were_reading(brain):
    ing = brain.ingest
    ing.visit("https://www.rent-portal.com/lease/renewal?id=7#top", "Lease renewal 2026", TUE - 300)
    part = brain.file("Downloads/lease-2026.pdf.crdownload", b"%PDF-1.7\n", TUE)
    pdf = brain.home / "Downloads/lease-2026.pdf"
    part.rename(pdf)
    brain.you("rename", pdf, TUE, chain=CHROME, old=str(part))
    ing.download(str(pdf), "https://files.rent-portal.com/lease.pdf", "https://www.rent-portal.com/lease/renewal?id=7",
                 TUE)
    brain.stamp(pdf, TUE, opened=True)
    assert brain.why(pdf) == "Downloaded from rent-portal on Tue 14:02 while you read “Lease renewal 2026”."

    f = brain.focus(pdf)
    assert f["thing"]["made"] == "Downloaded from rent-portal on Tue 14:02 while you read “Lease renewal 2026”."
    assert f["preview"]["type"] == "pdf"
    # The page, not "you, in the browser": the browser writing it is the download itself.
    [line] = f["slots"]["came_from"]["items"]
    assert (line["title"], line["kind"], line["ref"]) == \
        ("Lease renewal 2026", "page", "https://www.rent-portal.com/lease/renewal?id=7")
    assert line["why"] == "downloaded from rent-portal while you read it, Tue 14:02"

    page = brain.focus(line["ref"])
    assert page["thing"]["kind"] == "page" and page["preview"]["url"] == line["ref"]
    assert titles(page["slots"]["used_with"]) == [("lease-2026.pdf", "downloaded from this")]
    assert brain.why(line["ref"]) == "You first read it on Tuesday. 1 download came from it."
    site = brain.focus(page["thing"]["area"]["ref"])
    assert [c["name"] for c in site["children"]["items"]] == ["Lease renewal 2026"]


def test_you_made_it_and_the_machine_changed_it_later(brain):
    notes = brain.file("notes.txt", "hello", local(2026, 9, 3, 9, 0))
    brain.you("create", notes, local(2026, 9, 3, 9, 0))
    brain.turn(44, None, local(2026, 10, 1, 10, 2) - 2, wrote=[notes])
    brain.stamp(notes, local(2026, 10, 1, 10, 2))
    assert brain.why(notes) == ("You made it in the terminal on 3 Sep. "
                                "Last changed by the machine in turn 44, today at 10:02.")
    f = brain.focus(notes)
    assert f["thing"]["last"] == "Last changed by the machine in turn 44, today at 10:02."
    assert titles(f["slots"]["came_from"]) == [("the machine, turn 44", "changed this, 10:02"),
                                               ("you, in the terminal", "made this, 3 Sep")]
    assert f["slots"]["came_from"]["items"][1]["id"] is None


def test_a_turn_that_read_one_file_and_wrote_another(brain):
    pdf = brain.file("Documents/Lease/lease-2026.pdf", b"%PDF-1.7\n", TUE)
    brain.found(pdf)
    letter = brain.home / "Documents/Lease/letter-to-landlord.odt"
    letter.write_bytes(b"PK\x03\x04")
    brain.turn(57, "draft a reply saying we'll renew", local(2026, 9, 30, 11, 0), made=[letter], read=[pdf])
    f = brain.focus(letter)
    assert titles(f["slots"]["came_from"])[0] == ("lease-2026.pdf", "read by turn 57 before writing this, yesterday 11:00")
    assert ("the machine, turn 57", "“draft a reply saying we'll renew”, yesterday 11:00") in titles(f["slots"]["came_from"])
    assert titles(brain.focus(pdf)["slots"]["used_with"]) == [("letter-to-landlord.odt", "made from this in turn 57")]
    assert brain.focus(pdf)["thing"]["area"]["title"] == "Lease"


# -- Focus around snapshots.py --

@pytest.fixture
def snapshots(brain):
    proj = brain.home / "Projects/Bombadil"
    snap = brain.file("Projects/Bombadil/src/bombadil/snapshots.py", "def prune(): ...\n", local(2026, 9, 1))
    brain.found(proj)
    brain.found(snap)
    return snap


def test_two_sessions_changed_it_while_a_wiki_page_was_open(brain, snapshots):
    ing = brain.ingest
    brain.session("write", snapshots, TUE - 3600 * 4, "flaky")
    ing.visit("https://wiki.archlinux.org/title/Snapper", "Snapper - ArchWiki", local(2026, 10, 1, 12, 50) - 3600)
    ing.visit("https://example.org/closed", "Closed long before", local(2026, 10, 1, 11, 30), duration=30)
    ing.visit("https://example.org/later", "Opened after", local(2026, 10, 1, 13, 30) - 3600)
    brain.session("write", snapshots, local(2026, 10, 1, 13, 2) - 3600, "builder")
    f = brain.focus(snapshots)
    assert titles(f["slots"]["came_from"]) == [("builder on Bombadil", "changed this, 12:02"),
                                               ("flaky-snapshot on Bombadil", "changed this, Tue 10:02")]
    assert f["slots"]["came_from"]["items"][0]["ref"] == "session:bombadil/builder"
    assert titles(f["slots"]["read_with"]) == [("Snapper - ArchWiki", "open while builder edited this")]
    assert f["thing"]["made"] == "It was here before the brain started."
    assert f["thing"]["last"] == "Last changed by builder on Bombadil today at 12:02."
    assert f["thing"]["area"]["title"] == "Bombadil" and f["thing"]["area"]["kind"] == "project"
    # The page, in the middle, shows the other side.
    page = brain.focus("https://wiki.archlinux.org/title/Snapper")
    assert titles(page["slots"]["read_with"]) == [("snapshots.py", "builder edited it while this was open")]
    # And a session in the middle lists what it changed.
    s = brain.focus("session:bombadil/builder")
    assert s["thing"]["title"] == "builder on Bombadil" and s["preview"]["type"] == "session"
    assert [(c["name"], c["who"]) for c in s["children"]["items"]] == [("snapshots.py", "changed")]


def test_used_with_counts_the_turns_they_changed_in_together(brain, snapshots):
    tests = brain.file("Projects/Bombadil/tests/test_snapshots.py", "def test(): ...\n", local(2026, 9, 1))
    once = brain.file("Projects/Bombadil/README.md", "# Bombadil\n", local(2026, 9, 1))
    for p in (tests, once):
        brain.found(p)
    for i, n in enumerate(range(50, 55)):
        wrote = [snapshots, tests] + ([once] if i == 0 else [])
        brain.turn(n, f"fix the race, part {i + 1}", MON + i * 3600, wrote=wrote)
    f = brain.focus(snapshots)
    assert titles(f["slots"]["used_with"]) == [("test_snapshots.py", "changed in the same 5 turns")]
    came = f["slots"]["came_from"]
    assert len(came["items"]) == 5 and came["more"] == 0
    assert came["items"][0]["title"] == "the machine, turn 54"
    f = brain.focus(snapshots, limit=2)
    assert len(f["slots"]["came_from"]["items"]) == 2 and f["slots"]["came_from"]["more"] == 3
    assert f["slots"]["changes"]["count_week"] == 5 and f["slots"]["changes"]["more"] == 3


def test_something_used_with_it_being_changed_now_by_another_session_is_amber(brain, snapshots):
    launcher = brain.file("Projects/Bombadil/src/bombadil/launcher.py", "x\n", local(2026, 9, 1))
    brain.found(launcher)
    for t in (MON, TUE):
        brain.session("write", snapshots, t, "builder")
        brain.session("write", launcher, t + 300, "builder")
    brain.session("write", launcher, NOW - 120, "kit")
    f = brain.focus(snapshots)
    [line] = f["slots"]["used_with"]["items"]
    assert (line["title"], line["why"], line["tone"], line["when"]) == \
        ("launcher.py", "also being changed by kit", "amber", "11:58")
    kit = brain.store.by_key("session:bombadil/kit")["id"]
    [line] = brain.focus(snapshots, looking=kit)["slots"]["used_with"]["items"]
    assert (line["why"], line["tone"]) == ("changed with this 2 times", "normal")


# -- folders, gone things, private things --

def test_a_folder_lists_what_is_on_disk_with_who_and_when(brain):
    lease = brain.home / "Documents/Lease"
    old = brain.file("Documents/Lease/old.txt", "old", local(2026, 8, 1))
    brain.found(lease)
    brain.found(old)
    letter = lease / "letter.odt"
    letter.write_bytes(b"PK")
    brain.turn(57, "draft a reply", local(2026, 9, 30, 11, 0), made=[letter])
    brain.stamp(letter, local(2026, 9, 30, 11, 0))
    notes = brain.file("Documents/Lease/notes.txt", "n", local(2026, 10, 1, 9, 0))
    brain.you("create", notes, local(2026, 10, 1, 9, 0))
    stale = brain.file("Documents/Lease/changed-behind.txt", "a", local(2026, 9, 2))
    brain.you("create", stale, local(2026, 9, 2))
    brain.stamp(stale, local(2026, 9, 29, 8, 0))   # changed later while the brain was not looking
    brain.file("Documents/Lease/untracked.txt", "u", local(2026, 9, 20))
    (lease / "scans").mkdir()
    brain.you("create", lease / "scans", local(2026, 9, 5), chain=FILES, dir=True)
    os.utime(lease / "scans", (local(2026, 9, 5), local(2026, 9, 5)))
    for skip in (".hidden", "letter.odt.swp", "node_modules"):
        (lease / skip).mkdir() if skip == "node_modules" else (lease / skip).write_text("x")

    kids = brain.focus(lease)["children"]
    rows = [(c["name"], c["kind"], c["who"], c["when"], c["id"] is None) for c in kids["items"]]
    assert rows == [("scans", "folder", "you", "5 Sep", False),
                    ("notes.txt", "file", "you", "09:00", False),
                    ("letter.odt", "file", "the machine, turn 57", "yesterday 11:00", False),
                    ("changed-behind.txt", "file", "", "Tue 08:00", False),
                    ("untracked.txt", "file", "", "20 Sep", True),
                    ("old.txt", "file", "", "1 Aug", False)]
    assert kids["total"] == 6 and kids["items"][0]["ref"] == str(lease / "scans")
    page = focus.children(brain.store, str(lease), str(brain.home), offset=1, limit=2, now=NOW)
    assert [c["name"] for c in page["items"]] == ["notes.txt", "letter.odt"] and page["total"] == 6
    assert brain.focus(lease)["preview"] == {"type": "folder", "path": str(lease), "private": False}


def test_a_deleted_thing_still_focuses_on_its_history(brain):
    notes = brain.file("notes.txt", "hello", local(2026, 9, 3, 9, 0))
    brain.you("create", notes, local(2026, 9, 3, 9, 0))
    tid = brain.store.by_path(str(notes))["id"]
    notes.unlink()
    brain.you("delete", notes, local(2026, 10, 1, 10, 2))
    f = brain.focus(notes)
    t = f["thing"]
    assert (t["id"], t["exists"], t["ref"], t["opened"]) == (tid, False, str(tid), None)
    assert t["deleted"] == local(2026, 10, 1, 10, 2)
    assert f["preview"] == {"type": "none", "private": False}
    assert f["slots"]["changes"]["items"][0]["why"] == "deleted by you in the terminal"
    assert brain.why(notes) == "You made it in the terminal on 3 Sep. You deleted it in the terminal today at 10:02."
    # Something new at the same path is another thing; the old one is still there by its id.
    brain.file("notes.txt", "new", NOW - 60)
    brain.you("create", notes, NOW - 60)
    assert brain.focus(notes)["thing"]["id"] != tid
    assert brain.focus(tid)["thing"]["deleted"] is not None


def test_trash_and_a_folder_that_went(brain):
    lease = brain.home / "Documents/Lease"
    a = brain.file("Documents/Lease/a.txt", "a", local(2026, 9, 1))
    brain.found(lease)
    brain.found(a)
    trash = brain.home / ".local/share/Trash/files/Lease"
    brain.you("rename", trash, local(2026, 10, 1, 10, 0), chain=FILES, old=str(lease), dir=True)
    assert brain.why(lease) == ("It was here before the brain started. "
                                "You moved it to the Trash in Files today at 10:00.")
    kids = brain.focus(lease)["children"]
    assert [(c["name"], c["ref"].isdigit()) for c in kids["items"]] == [("a.txt", True)] and kids["total"] == 1


def test_private_things_show_their_names_never_their_contents(brain):
    pw = brain.file("Documents/passwords.txt", "hunter2", local(2026, 9, 3))
    brain.you("create", pw, local(2026, 9, 3))
    f = brain.focus(pw)
    assert f["thing"]["private"] is True and f["preview"] == {"type": "none", "private": True}
    assert f["thing"]["made"] == "You made it in the terminal on 3 Sep."
    ssh = brain.home / ".ssh"
    key = brain.file(".ssh/id_ed25519", "secret", local(2026, 9, 3))
    brain.found(ssh)
    brain.found(key)
    kids = brain.focus(ssh)["children"]
    assert kids["items"] == [{"id": brain.store.by_path(str(key))["id"], "ref": str(key), "name": "id_ed25519",
                              "kind": "file", "t": None, "when": "", "who": "", "actor": "unknown",
                              "private": True}]
    assert brain.focus(ssh)["preview"] == {"type": "none", "private": True}


# -- refs, turns, the rest --

def test_refs_resolve_every_way_the_app_passes_them(brain):
    wg = brain.home / "setup-wg.sh"
    wg.write_text("x")
    brain.turn(41, "install the VPN", MON, made=[wg])
    brain.ingest.visit("https://Wiki.ArchLinux.org/title/Snapper#Configuration", "Snapper", MON)
    s = brain.store
    tid = s.by_path(str(wg))["id"]
    assert focus.resolve(s, tid)["id"] == tid
    assert focus.resolve(s, str(tid))["id"] == tid
    assert focus.resolve(s, str(wg) + "/")["id"] == tid
    assert focus.resolve(s, "https://wiki.archlinux.org/title/Snapper")["title"] == "Snapper"
    assert focus.resolve(s, "turn:41")["kind"] == "turn"
    assert focus.resolve(s, "system") is None
    for bad in (None, True, "", "  ", "/nowhere", "ftp://x", "turn:999", 10 ** 9):
        assert focus.resolve(s, bad) is None
    assert brain.focus("/nowhere") is None
    assert brain.why("/nowhere") == "The brain does not know this yet."


def test_a_turn_in_the_middle(brain):
    wg = brain.home / "setup-wg.sh"
    wg.write_text("x")
    conf = brain.file("wg0.conf", "[Interface]\n", local(2026, 9, 1))
    brain.found(conf)
    brain.turn(41, "install the VPN", MON, made=[wg], wrote=[conf])
    f = brain.focus("turn:41")
    assert f["thing"]["title"] == "install the VPN" and f["preview"]["type"] == "turn"
    assert {(c["name"], c["who"]) for c in f["children"]["items"]} == {("setup-wg.sh", "made"),
                                                                       ("wg0.conf", "changed")}
    assert brain.why("turn:41") == "You asked the machine “install the VPN” on Monday. It made 1 thing and changed 1 other."


def test_packages_say_who_installed_them(brain):
    t = brain.turn(12, "install ffmpeg", MON)
    brain.ingest.package("ffmpeg", "2:7.1-1", "install", MON + 10, brain.ingest.who(actors.Actor("turn", f"unit:{unit(12)}")))
    from bombadil.brain.ingest import Who
    brain.ingest.package("ffmpeg", "2:7.1-2", "upgrade", NOW - 3600, Who("system", None, "pacman"))
    assert t
    assert brain.why("package:ffmpeg") == ("Installed by the machine in turn 12, “install ffmpeg”, on Monday. "
                                           "Last upgraded by the system (pacman) today at 11:00.")
    f = brain.focus("package:ffmpeg")
    assert f["preview"] == {"type": "package", "text": "version 2:7.1-2", "private": False}
    assert f["thing"]["area"]["ref"] == "system"
    assert [c["name"] for c in brain.focus("system")["children"]["items"]] == ["ffmpeg"]


def test_site_names_are_what_people_say():
    assert focus.site_name("https://www.rent-portal.com/x") == "rent-portal"
    assert focus.site_name("https://wiki.archlinux.org/title/Snapper") == "archlinux"
    assert focus.site_name("https://www.bbc.co.uk/news") == "bbc"
    assert focus.site_name("http://192.168.1.1/admin") == "192.168.1.1"
    assert focus.site_name("http://localhost:8080/") == "localhost"
    assert focus.site_name("") == ""


def test_odd_names_and_an_unreadable_folder_do_not_break_focus(brain):
    odd = brain.file("Documents/Ünïcode – “quotes” 😀.txt", "x", local(2026, 9, 3))
    brain.you("create", odd, local(2026, 9, 3))
    f = brain.focus(odd)
    assert f["thing"]["title"] == "Ünïcode – “quotes” 😀.txt"
    json.dumps(f)
    docs = brain.home / "Documents"
    names = [c["name"] for c in brain.focus(docs)["children"]["items"]]
    assert names == ["Ünïcode – “quotes” 😀.txt"]
    docs_id = brain.store.by_path(str(docs))["id"]
    os.rename(docs, brain.home / "Elsewhere")   # the brain has not heard yet
    kids = brain.focus(docs_id)["children"]
    assert [c["name"] for c in kids["items"]] == ["Ünïcode – “quotes” 😀.txt"]


def test_a_fact_says_who_taught_it_and_where_it_lives(brain):
    from bombadil.brain.ingest import Who
    tid = brain.turn(41, "remember that I like dark mode", MON)
    memory = brain.home / ".bombadil/memory.md"
    brain.ingest.facts(["# About Daniel", "- Daniel likes dark mode"], MON + 20, Who("turn", tid), str(memory))
    [fact] = brain.store.q("SELECT * FROM things WHERE kind = 'fact'")
    assert brain.why(fact["key"]) == ("Added by the machine in turn 41, “remember that I like dark mode”, on Monday. "
                                      "It is a line in memory.md.")
    f = brain.focus(fact["key"])
    assert f["preview"] == {"type": "fact", "text": "Daniel likes dark mode", "private": False}
    assert f["slots"]["changes"]["items"][0]["why"].startswith("added by the machine in turn 41")


def test_an_empty_brain_and_a_folder_that_became_a_file(brain):
    assert brain.focus(str(brain.home)) is None
    assert brain.why(str(brain.home)) == "The brain does not know this yet."
    d = brain.home / "Lease"
    (d / "a.txt").parent.mkdir()
    brain.file("Lease/a.txt", "a", local(2026, 9, 1))
    brain.found(d)
    brain.found(d / "a.txt")
    (d / "a.txt").unlink()
    d.rmdir()
    d.write_text("not a folder any more")
    f = brain.focus(d)
    assert f["preview"] == {"type": "none", "private": False}
    assert [c["name"] for c in f["children"]["items"]] == ["a.txt"]   # what the brain remembers


def test_mount_points_with_spaces_are_read(brain):
    odd = brain.home / "My Drive"
    odd.mkdir()
    (brain.home / "mounts").write_text(f"/dev/vda / ext4 rw,relatime 0 0\n"
                                       f"/dev/vdb {str(odd).replace(' ', chr(92) + '040')} vfat rw,noatime 0 0\n")
    f = brain.file("My Drive/x.txt", "x", MON)
    assert focus.opened_since_change(str(f)) is None
    g = brain.file("y.txt", "y", MON)
    assert focus.opened_since_change(str(g)) is False


# -- who every line is about --

def test_each_line_says_who_it_is_about(brain, snapshots):
    ing = brain.ingest
    # A session and a turn both changed it; you made a note beside it; a page was open.
    brain.session("write", snapshots, TUE - 3600 * 4, "flaky")
    ing.visit("https://wiki.archlinux.org/title/Snapper", "Snapper - ArchWiki", local(2026, 10, 1, 12, 50) - 3600)
    brain.session("write", snapshots, local(2026, 10, 1, 13, 2) - 3600, "builder")
    brain.turn(50, "fix the race", MON, wrote=[snapshots])
    f = brain.focus(snapshots)
    by_title = {i["title"]: i["actor"] for i in f["slots"]["came_from"]["items"]}
    assert by_title == {"builder on Bombadil": "session", "flaky-snapshot on Bombadil": "session",
                        "the machine, turn 50": "turn"}
    # read_with: whoever was editing while the page was open.
    assert [(i["title"], i["actor"]) for i in f["slots"]["read_with"]["items"]] == [("Snapper - ArchWiki", "session")]
    page = brain.focus("https://wiki.archlinux.org/title/Snapper")
    assert [(i["title"], i["actor"]) for i in page["slots"]["read_with"]["items"]] == [("snapshots.py", "session")]
    assert {i["actor"] for i in f["slots"]["changes"]["items"]} == {"session", "turn"}
    # What a session changed is listed as its own.
    s = brain.focus("session:bombadil/builder")
    assert {i["actor"] for i in s["children"]["items"]} == {"session"}
    assert brain.focus("turn:50")["children"]["items"][0]["actor"] == "turn"


def test_used_with_says_who_changed_both_and_amber_says_who_is_at_work(brain, snapshots):
    tests = brain.file("Projects/Bombadil/tests/test_snapshots.py", "def test(): ...\n", local(2026, 9, 1))
    launcher = brain.file("Projects/Bombadil/src/bombadil/launcher.py", "x\n", local(2026, 9, 1))
    for p in (tests, launcher):
        brain.found(p)
    for i, n in enumerate((50, 51)):
        brain.turn(n, f"fix the race, part {i + 1}", MON + i * 3600, wrote=[snapshots, tests])
    for t in (MON, TUE):
        brain.session("write", snapshots, t, "builder")
        brain.session("write", launcher, t + 300, "builder")
    brain.session("write", launcher, NOW - 120, "kit")
    [amber, normal] = brain.focus(snapshots)["slots"]["used_with"]["items"]
    assert (amber["title"], amber["tone"], amber["actor"]) == ("launcher.py", "amber", "session")
    assert (normal["title"], normal["tone"], normal["actor"]) == ("test_snapshots.py", "normal", "turn")


def test_the_page_a_download_came_from_and_the_file_a_turn_read_are_about_who_linked_them(brain):
    ing = brain.ingest
    ing.visit("https://www.rent-portal.com/lease/renewal", "Lease renewal 2026", TUE - 300)
    part = brain.file("Downloads/lease-2026.pdf.crdownload", b"%PDF-1.7\n", TUE)
    pdf = brain.home / "Downloads/lease-2026.pdf"
    part.rename(pdf)
    brain.you("rename", pdf, TUE, chain=CHROME, old=str(part))
    ing.download(str(pdf), "https://files.rent-portal.com/lease.pdf", "https://www.rent-portal.com/lease/renewal", TUE)
    letter = brain.home / "Downloads/letter.odt"
    letter.write_bytes(b"PK\x03\x04")
    brain.turn(57, "draft a reply", local(2026, 9, 30, 11, 0), made=[letter], read=[pdf])
    [line] = brain.focus(pdf)["slots"]["came_from"]["items"]
    assert (line["kind"], line["actor"]) == ("page", "you")
    by_kind = {i["kind"]: i["actor"] for i in brain.focus(letter)["slots"]["came_from"]["items"]}
    assert by_kind == {"file": "turn", "turn": "turn"}
    used = brain.focus(pdf)["slots"]["used_with"]["items"]
    assert [(i["title"], i["actor"]) for i in used] == [("letter.odt", "turn")]
    page = brain.focus("https://www.rent-portal.com/lease/renewal")
    assert [(i["title"], i["actor"]) for i in page["slots"]["used_with"]["items"]] == [("lease-2026.pdf", "you")]


def test_unknown_apps_and_the_system_are_told_apart(brain):
    old = brain.file("old.txt", "a", local(2026, 8, 1))
    brain.found(old)                                    # there before the brain
    brain.ingest.apply([{"op": "offline", "path": str(old), "t": local(2026, 9, 2), "uid": 1000, "pid": 0,
                         "comm": "", "cgroup": "", "chain": [], "dir": False}])
    f = brain.focus(old)
    assert [i["actor"] for i in f["slots"]["changes"]["items"]] == ["unknown"]
    assert f["slots"]["changes"]["items"][0]["why"] == "changed while the brain was not watching"
    assert f["slots"]["came_from"]["items"] == []
    brain.turn(12, "install ffmpeg", MON)
    from bombadil.brain.ingest import Who
    tid = brain.store.by_key("turn:12")["id"]
    brain.ingest.package("ffmpeg", "2:7.1-1", "install", MON + 10, Who("turn", tid))
    brain.ingest.package("ffmpeg", "2:7.1-2", "upgrade", NOW - 3600, Who("system", None, "pacman"))
    pkg = brain.focus("package:ffmpeg")
    assert [(i["kind"], i["actor"]) for i in pkg["slots"]["changes"]["items"]] == [("upgrade", "system"),
                                                                                  ("install", "turn")]
    assert {(i["title"], i["actor"]) for i in pkg["slots"]["came_from"]["items"]} == {
        ("the machine, turn 12", "turn"), ("the system (pacman)", "system")}
    assert [(i["name"], i["who"], i["actor"]) for i in brain.focus("system")["children"]["items"]] == [
        ("ffmpeg", "the system", "system")]
    app = brain.home / "Apps/tracker"
    (app / "data").mkdir(parents=True)
    (app / "main.qml").write_text("x")
    brain.ingest.apply([brain.ev("write", app / "data/runs.db", NOW - 60,
                                 "/user.slice/user-1000.slice/user@1000.service/app.slice/bombadil-app-tracker.scope",
                                 [[9, "python3", "python3"]])])
    changes = brain.focus("app:tracker")["slots"]["changes"]["items"]
    assert [(i["actor"], i["why"]) for i in changes] == [("app", "changed by Tracker")]


def test_a_folders_entries_say_who_changed_them_last(brain):
    lease = brain.home / "Documents/Lease"
    lease.mkdir(parents=True)
    brain.found(lease)
    letter = lease / "letter.odt"
    letter.write_bytes(b"PK")
    brain.turn(57, "draft a reply", local(2026, 9, 30, 11, 0), made=[letter])
    brain.stamp(letter, local(2026, 9, 30, 11, 0))
    notes = brain.file("Documents/Lease/notes.txt", "n", local(2026, 10, 1, 9, 0))
    brain.you("create", notes, local(2026, 10, 1, 9, 0))
    brain.file("Documents/Lease/untracked.txt", "u", local(2026, 9, 20))
    kids = brain.focus(lease)["children"]["items"]
    assert {c["name"]: c["actor"] for c in kids} == {"notes.txt": "you", "letter.odt": "turn",
                                                     "untracked.txt": "unknown"}


def test_the_last_writers_query_reads_events_by_thing_not_by_kind(brain):
    """For a folder of hundreds of entries SQLite, left to itself, reads every create or change
    event of the index (340 ms on 340,000 events; by thing it is 14 ms), so the query names its
    index. (A folder of one entry plans well either way; the statement is what is checked.)"""
    lease = brain.home / "Documents/Lease"
    lease.mkdir(parents=True)
    brain.found(lease)
    notes = brain.file("Documents/Lease/notes.txt", "n", local(2026, 10, 1, 9, 0))
    brain.you("create", notes, local(2026, 10, 1, 9, 0))
    seen = []
    real = brain.store.q

    def spy(sql, params=()):
        seen.append((sql, params))
        return real(sql, params)
    brain.store.q = spy
    try:
        assert [c["name"] for c in brain.focus(lease)["children"]["items"]] == ["notes.txt"]
    finally:
        brain.store.q = real
    [(sql, params)] = [s for s in seen if "FROM events" in s[0] and "GROUP BY thing" in s[0]]
    assert "INDEXED BY events_thing_t" in sql
    plan = " ".join(str(r[3]) for r in brain.store.db.execute("EXPLAIN QUERY PLAN " + sql, params))
    assert "events_thing_t" in plan and "events_kind_t" not in plan


# -- what the lines say --

def test_a_line_says_when_it_was_made_not_when_it_was_last_saved(brain):
    notes = brain.file("notes.txt", "hello", local(2026, 9, 3, 9, 0))
    brain.you("create", notes, local(2026, 9, 3, 9, 0))
    brain.you("write", notes, local(2026, 9, 30, 15, 0))
    brain.stamp(notes, local(2026, 9, 30, 15, 0))
    [line] = brain.focus(notes)["slots"]["came_from"]["items"]
    assert (line["why"], line["t"], line["when"]) == ("made this, 3 Sep", local(2026, 9, 3, 9, 0), "3 Sep")


def test_a_turn_line_reads_as_the_brief_writes_it(brain):
    wg = brain.home / "setup-wg.sh"
    wg.write_text("x")
    brain.turn(12, "drop the old cleanup", NOW - 3600 * 0 - 600, made=[wg])
    [line] = brain.focus(wg)["slots"]["came_from"]["items"]
    assert f"{line['title']}, {line['why']}" == "the machine, turn 12, “drop the old cleanup”, 11:50"


def test_a_turn_without_words_is_still_named(brain):
    from bombadil.brain.ingest import Who
    tid = brain.turn(58, None, MON)
    assert brain.why("turn:58") == "That was the machine's turn 58 on Monday."
    assert brain.focus("turn:58")["thing"]["made"] == "That was the machine's turn 58 on Monday."
    brain.ingest.package("nano", "8.0-1", "install", MON + 10, Who("turn", tid))
    assert brain.why("package:nano") == "Installed by the machine in turn 58, on Monday."


def test_a_download_says_the_day_as_the_brief_writes_it(brain):
    ing = brain.ingest

    def why_at(t):
        pdf = brain.file(f"Downloads/{int(t)}.pdf", b"%PDF-1.7\n", t)
        ing.visit("https://www.rent-portal.com/lease", "Lease renewal 2026", t - 60)
        brain.you("create", pdf, t, chain=CHROME)
        ing.download(str(pdf), "https://files.rent-portal.com/x.pdf", "https://www.rent-portal.com/lease", t)
        brain.stamp(pdf, t, opened=True)
        return brain.why(pdf)

    tail = " while you read “Lease renewal 2026”."
    assert why_at(local(2026, 10, 1, 9, 30)) == "Downloaded from rent-portal today at 09:30" + tail
    assert why_at(local(2026, 9, 30, 9, 30)) == "Downloaded from rent-portal yesterday at 09:30" + tail
    assert why_at(local(2026, 9, 28, 9, 30)) == "Downloaded from rent-portal on Mon 09:30" + tail
    assert why_at(local(2026, 9, 3, 9, 30)) == "Downloaded from rent-portal on 3 Sep" + tail


def test_the_week_in_a_line_counts_seven_changes_as_seven(brain):
    wg = brain.file("setup-wg.sh", "x", local(2026, 9, 1))
    brain.found(wg)
    for i in range(7):
        brain.turn(60 + i, f"tweak {i}", local(2026, 9, 29 + i // 4, 9 + i))
    for i in range(7):
        brain.you("write", wg, local(2026, 9, 29 + i // 4, 9 + i))
    ch = brain.focus(wg)["slots"]["changes"]
    assert ch["count_week"] == 7 and sum(ch["days"]) == 7 and len(ch["days"]) == 7


def test_a_page_read_many_times_still_says_when_it_was_first_read(brain):
    brain.ingest.visit("https://example.org/news", "News", MON)
    page = brain.store.by_key("url:https://example.org/news")["id"]
    with brain.store.tx():
        for i in range(focus.HISTORY_CAP + 50):
            brain.store.event(TUE + i * 60, "visit", page, "you", None, "chromium", coalesce=0)
    assert brain.why("https://example.org/news").startswith("You first read it on Monday. Last read ")


def test_a_busy_thing_counts_its_whole_week_not_the_events_that_were_read(brain):
    wg = brain.file("status.txt", "x", local(2026, 9, 1))
    brain.found(wg)
    tid = brain.store.by_path(str(wg))["id"]
    rows = []
    for day, n in ((local(2026, 9, 29, 0, 5), 1000), (local(2026, 9, 30, 0, 5), 1000), (local(2026, 10, 1, 0, 5), 400)):
        rows += [(day + i * 80, "change", tid, "you", None, "foot") for i in range(n)]
    with brain.store.tx():
        brain.store.db.executemany("INSERT INTO events(t, kind, thing, actor, actor_thing, via) VALUES(?,?,?,?,?,?)",
                                   rows)
    ch = brain.focus(wg)["slots"]["changes"]
    assert ch["days"] == [0, 0, 0, 0, 1000, 1000, 400] and ch["count_week"] == 2400
    assert len(ch["items"]) == 5 and ch["more"] == 2395


def test_a_huge_folder_says_how_many_it_holds(brain, monkeypatch):
    monkeypatch.setattr(focus, "SCAN_LIMIT", 30)
    big = brain.home / "data"
    big.mkdir()
    for i in range(100):
        (big / f"f{i:03}.txt").write_text("x")
    brain.found(big)
    kids = brain.focus(big)["children"]
    assert kids["total"] == 100 and len(kids["items"]) == 30
    assert focus.children(brain.store, str(big), str(brain.home), offset=25, limit=50, now=NOW)["total"] == 100


# -- private things: names and history only --

def test_a_link_to_a_private_file_is_private(brain):
    key = brain.file(".ssh/id_ed25519", "not a key, but its name says it is one", local(2026, 9, 3))
    link = brain.home / "notes.txt"
    link.symlink_to(key)
    brain.you("create", link, local(2026, 9, 3))
    vault = brain.home / "Documents/vault"
    vault.parent.mkdir()
    vault.symlink_to(brain.home / ".ssh")
    cfg = brain.file(".ssh/config", "Host x\n", local(2026, 9, 3))
    brain.you("create", vault / "config", local(2026, 9, 3))
    for thing in (link, vault / "config"):
        f = brain.focus(thing)
        assert f["thing"]["private"] is True and f["preview"] == {"type": "none", "private": True}
    assert cfg.exists()


def test_a_link_is_never_followed_for_a_preview(brain):
    target = brain.file("Documents/plain.txt", "hello", local(2026, 9, 3))
    link = brain.home / "Documents/link.txt"
    link.symlink_to(target)
    brain.you("create", link, local(2026, 9, 3))
    assert brain.focus(link)["preview"] == {"type": "none", "private": False}
    brain.you("create", target, local(2026, 9, 3))
    assert brain.focus(target)["preview"]["type"] == "text"


def test_an_app_named_for_its_secrets_is_private_even_when_its_row_is_not(brain):
    app = brain.home / "Apps/passwords"
    (app / "data").mkdir(parents=True)
    (app / "main.qml").write_text("x")
    (app / "app.toml").write_text('title = "Passwords"\n')
    brain.ingest.folder(str(app))
    brain.found(app / "main.qml")
    thing = brain.store.by_path(str(app))
    assert thing["kind"] == "app" and not thing["private"]
    f = brain.focus(app)
    assert f["thing"]["private"] is True and f["preview"] == {"type": "none", "private": True}
    assert {(c["name"], c["t"], c["who"], c["actor"], c["private"]) for c in f["children"]["items"]} == {
        ("main.qml", None, "", "unknown", True), ("app.toml", None, "", "unknown", True),
        ("data", None, "", "unknown", True)}
    # Even after the folder is gone, what it held is remembered by name only.
    (app / "main.qml").unlink()
    (app / "app.toml").unlink()
    (app / "data").rmdir()
    app.rmdir()
    kids = brain.focus(thing["id"])["children"]["items"]
    assert [(c["name"], c["when"], c["who"], c["private"]) for c in kids] == [("main.qml", "", "", True)]


def test_a_private_things_description_is_never_asked_for(brain):
    pw = brain.file("Documents/passwords.txt", "hunter2", local(2026, 9, 3))
    brain.you("create", pw, local(2026, 9, 3))
    ran = []
    d = describe.Describer(brain.store, str(brain.home), run=ran.append)
    assert d.get(brain.store.by_path(str(pw))) is None
    d.wait()
    assert ran == []


# -- deleted and odd things --

def test_a_removed_package_and_a_forgotten_fact_still_focus(brain):
    from bombadil.brain.ingest import Who
    brain.turn(12, "install nano", MON)
    tid = brain.store.by_key("turn:12")["id"]
    brain.ingest.package("nano", "8.0-1", "install", MON + 10, Who("turn", tid))
    brain.ingest.package("nano", "8.0-1", "remove", NOW - 3600, Who("system", None, "pacman"))
    assert brain.why("package:nano") == ("Installed by the machine in turn 12, “install nano”, on Monday. "
                                         "Removed by the system (pacman) today at 11:00.")
    f = brain.focus("package:nano")
    assert (f["thing"]["exists"], f["thing"]["opened"]) == (False, None)
    assert f["slots"]["changes"]["items"][0]["why"] == "removed by the system (pacman)"
    brain.ingest.facts(["- Daniel likes dark mode"], MON + 20, Who("turn", tid), str(brain.home / "memory.md"))
    brain.ingest.facts(["- Something else"], NOW - 60, Who("you"), str(brain.home / "memory.md"))
    [gone] = brain.store.q("SELECT * FROM things WHERE kind = 'fact' AND deleted IS NOT NULL")
    assert brain.why(gone["key"]) == ("Added by the machine in turn 12, “install nano”, on Monday. "
                                      "You deleted it today at 11:59.")
    assert brain.focus(gone["key"])["preview"] == {"type": "fact", "text": "Daniel likes dark mode", "private": False}


def test_a_file_holds_nothing_but_is_not_unknown(brain):
    wg = brain.file("setup-wg.sh", "x", local(2026, 9, 1))
    brain.found(wg)
    assert focus.children(brain.store, str(wg), str(brain.home), now=NOW) == {"items": [], "total": 0}
    assert brain.focus(wg)["children"] is None
    assert focus.children(brain.store, "/nowhere", str(brain.home), now=NOW) is None


def test_a_forgotten_thing_is_not_listed_and_cannot_be_focused(brain):
    wg = brain.home / "setup-wg.sh"
    wg.write_text("x")
    conf = brain.file("wg0.conf", "[Interface]\n", local(2026, 9, 1))
    brain.found(conf)
    tid = brain.turn(41, "install the VPN", MON, made=[wg], wrote=[conf])
    brain.store.update(brain.store.by_path(str(conf))["id"], forgotten=1)
    f = brain.focus("turn:41")
    assert [c["name"] for c in f["children"]["items"]] == ["setup-wg.sh"] and f["children"]["total"] == 1
    assert brain.focus(conf) is None
    brain.store.update(tid, forgotten=1)
    [line] = brain.focus(wg)["slots"]["came_from"]["items"]
    assert (line["id"], line["ref"], line["title"]) == (None, None, "the machine, turn 41")


def test_refs_that_could_never_be_stored_name_nothing(brain):
    for bad in (10 ** 30, "9" * 30, "9" * 5000, -5, "-5", "/home/a\udcffb", "https://x.org/\udcff", "turn:\udcff",
                "/a\0b"):
        assert focus.resolve(brain.store, bad) is None
        assert focus.focus(brain.store, bad, str(brain.home), now=NOW) is None
        assert focus.children(brain.store, bad, str(brain.home), now=NOW) is None
        assert focus.why(brain.store, bad, str(brain.home), now=NOW) == "The brain does not know this yet."


def test_why_never_calls_a_model(brain, monkeypatch):
    wg = brain.home / "setup-wg.sh"
    wg.write_text("x")
    brain.turn(41, "install the VPN", MON, made=[wg])
    brain.ingest.visit("https://www.rent-portal.com/lease", "Lease renewal 2026", TUE)
    from bombadil.brain.ingest import Who
    brain.ingest.package("ffmpeg", "1", "install", MON, Who("system", None, "pacman"))
    brain.ingest.facts(["- Daniel likes dark mode"], MON, Who("you"), str(brain.home / "memory.md"))
    refs = [wg, "turn:41", "https://www.rent-portal.com/lease", "package:ffmpeg", "system", "/nowhere",
            brain.store.q("SELECT key FROM things WHERE kind = 'fact'")[0]["key"]]

    def boom(*a, **k):
        raise AssertionError("something that could start a model was called")

    for name in ("run", "Popen", "check_output", "check_call", "call"):
        monkeypatch.setattr(subprocess, name, boom)
    monkeypatch.setattr(os, "system", boom)
    monkeypatch.setattr(describe.Describer, "get", boom)
    monkeypatch.setattr(describe.Describer, "run", boom)
    monkeypatch.setattr(describe, "material", boom)
    for ref in refs:
        assert isinstance(brain.why(ref), str) and brain.why(ref)
        brain.focus(ref)   # and nor does the rest of Focus: the service adds the description


# -- time --

@pytest.fixture
def berlin(monkeypatch):
    if not os.path.exists("/usr/share/zoneinfo/Europe/Berlin"):
        pytest.skip("no zoneinfo")
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("now, then", [
    # The clocks went back on Sunday 25 Oct (a 25-hour day) and forward on Sunday 29 Mar (23 hours).
    ((2026, 10, 26, 10, 0), [(2026, 10, 24, 12, 0), (2026, 10, 25, 1, 30), (2026, 10, 26, 0, 10)]),
    ((2026, 3, 30, 10, 0), [(2026, 3, 28, 12, 0), (2026, 3, 29, 1, 30), (2026, 3, 30, 0, 10)]),
])
def test_days_hold_across_a_change_of_clocks(brain, berlin, now, then):
    wg = brain.file("setup-wg.sh", "x", local(2026, 3, 1))
    brain.found(wg)
    for at in then:
        brain.you("write", wg, local(*at))
    f = focus.focus(brain.store, str(wg), str(brain.home), now=local(*now))
    assert f["slots"]["changes"]["days"] == [0, 0, 0, 0, 1, 1, 1] and f["slots"]["changes"]["count_week"] == 3
    assert [i["when"] for i in f["slots"]["changes"]["items"]] == ["00:10", "yesterday 01:30", "Sat 12:00"]
    assert focus.why(brain.store, str(wg), str(brain.home), now=local(*now)).startswith("It was here before")


# -- a brain with a long memory --

def steps(store, fn):
    """How many thousand sqlite VM instructions `fn` cost: a count that does not depend on the machine."""
    n = [0]

    def tick():
        n[0] += 1
        return 0

    store.db.set_progress_handler(tick, 1000)
    try:
        fn()
    finally:
        store.db.set_progress_handler(None, 0)
    return n[0]


def test_a_long_history_does_not_make_focus_scan_all_of_it(brain):
    ing = brain.ingest
    ing.visit("https://example.org/news", "News", NOW - 3600)
    page = brain.store.by_key("url:https://example.org/news")["id"]
    files = [brain.store.add("file", f"f{i}", path=f"{brain.home}/f{i}") for i in range(50)]
    mine = files[0]
    rows = [(NOW - 86400 * 30 + i * 30, "change", files[i % 50], "you", None, "foot") for i in range(40000)]
    # Two hundred visits near the end, and something of yours changed while each was open.
    rows += [(NOW - 7200 + i * 30, "change", mine, "you", None, "foot") for i in range(200)]
    with brain.store.tx():
        brain.store.db.executemany("INSERT INTO events(t, kind, thing, actor, actor_thing, via) VALUES(?,?,?,?,?,?)",
                                   rows)
        for i in range(200):
            brain.store.event(NOW - 7200 + i * 30, "visit", page, "you", None, "chromium", coalesce=0)
    cost_page = steps(brain.store, lambda: brain.focus(page))
    cost_file = steps(brain.store, lambda: brain.focus(mine))
    # Read the whole table for every visit and this is in the hundreds of thousands.
    assert cost_page < 2500 and cost_file < 2500, (cost_page, cost_file)
    assert brain.focus(page)["slots"]["read_with"]["items"][0]["title"] == "f0"
