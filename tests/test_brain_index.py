import os
import platform
import subprocess
import threading
import time

import pytest

from bombadil.brain import index
from bombadil.brain.index import Walker


@pytest.fixture(autouse=True)
def _full_priority(monkeypatch):
    # Idle I/O on the test runner's own thread would slow every test after this one.
    monkeypatch.setattr(index, "lower_priority", lambda: None)


def _db(home):
    return home / ".local" / "state" / "bombadil" / "brain.db"


def _tree(home):
    """A home with what a real one has: documents, a project with git and its ignores,
    dot-folders, a private one, and a folder of generated data."""
    files = {
        "Documents/Lease/lease-2026.pdf": "%PDF",
        "Documents/Lease/notes/call.txt": "called the landlord",
        "Projects/latchkey/.gitignore": "build/\n*.log\n",
        "Projects/latchkey/src/main.py": "print('hi')\n",
        "Projects/latchkey/debug.log": "noise",
        "Projects/latchkey/build/out/app.o": "obj",
        "Projects/latchkey/node_modules/left-pad/index.js": "x",
        ".cache/thumbs/a.png": "png",
        ".config/hypr/hyprland.conf": "monitor=,preferred,auto,1",
        ".config/other-app/settings.json": "{}",
        ".ssh/id_ed25519": "PRIVATE KEY",
        "café ☕/menu.txt": "tea",
        "setup-wg.sh": "#!/bin/sh\n",
    }
    for rel, text in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q", str(home / "Projects" / "latchkey")], check=True)
    data = home / "data"
    data.mkdir()
    for i in range(25):
        (data / f"sample-{i}.csv").write_text("1,2\n")
    return files


def _paths(w):
    rows = w.store.q("SELECT path FROM things WHERE path IS NOT NULL AND deleted IS NULL")
    return {r["path"] for r in rows}


def test_first_index_finds_what_a_person_would_call_a_thing(home, monkeypatch):
    monkeypatch.setattr(index, "MAX_ENTRIES", 20)
    _tree(home)
    os.mkfifo(home / "a-pipe")
    (home / "link-to-lease").symlink_to(home / "Documents" / "Lease")
    try:
        (home / os.fsdecode(b"bad-\xff-name.txt")).write_text("x")
    except (OSError, UnicodeEncodeError):
        pass
    seen = []
    w = Walker(_db(home), str(home))
    result = w.first_index(progress=lambda done, total: seen.append((done, total)))
    assert result["stopped"] is False and result["found"] > 0
    got = _paths(w)
    h = str(home)
    for rel in ("Documents/Lease/lease-2026.pdf", "Documents/Lease/notes/call.txt", "Projects/latchkey",
                "Projects/latchkey/src/main.py", ".config/hypr/hyprland.conf",
                ".ssh/id_ed25519", "café ☕/menu.txt", "setup-wg.sh", "data"):
        assert f"{h}/{rel}" in got, rel
    for rel in ("Projects/latchkey/debug.log", "Projects/latchkey/build", "Projects/latchkey/build/out/app.o",
                "Projects/latchkey/node_modules/left-pad/index.js", ".cache/thumbs/a.png",
                ".config/other-app/settings.json", "a-pipe", "link-to-lease", "data/sample-0.csv",
                ".local/state/bombadil/brain.db"):
        assert f"{h}/{rel}" not in got, rel
    assert not any("bad-" in p for p in got)
    assert w.store.by_path(f"{h}/Projects/latchkey")["kind"] == "project"
    assert w.store.by_path(f"{h}/.ssh/id_ed25519")["private"] == 1
    assert w.store.by_path(f"{h}/data")["meta"] == {"files": 25}
    lease = w.store.by_path(f"{h}/Documents/Lease/lease-2026.pdf")
    assert lease["made_by"] == "before" and lease["size"] == 4 and lease["ino"]
    assert w.store.by_path(f"{h}/Documents/Lease")["ino"] == os.stat(f"{h}/Documents/Lease").st_ino
    assert w.store.get_meta("indexed") is not None
    # progress only goes up and ends at 100%
    assert seen[-1][0] == seen[-1][1]
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert len({t for _, t in seen}) == 1
    # the first index can run again at no cost: nothing is found twice
    before = w.store.counts()
    w.first_index()
    assert w.store.counts() == before
    w.close()


def test_a_folder_that_shrinks_below_the_cap_is_walked_again(home, monkeypatch):
    monkeypatch.setattr(index, "MAX_ENTRIES", 20)
    _tree(home)
    w = Walker(_db(home), str(home))
    w.first_index()
    for i in range(5):
        (home / "data" / f"sample-{i}.csv").unlink()
    w.reconcile()
    assert w.store.by_path(f"{home}/data")["meta"] == {}
    assert w.store.by_path(f"{home}/data/sample-24.csv") is not None
    w.close()


def test_made_by_xattr_names_the_turn(home):
    f = home / "setup-wg.sh"
    f.write_text("#!/bin/sh\n")
    try:
        os.setxattr(f, "user.bombadil.made_by", b"turn 41")
    except OSError:
        pytest.skip("this filesystem has no user xattrs")
    w = Walker(_db(home), str(home))
    w.first_index()
    thing = w.store.by_path(str(f))
    turn = w.store.by_key("turn:41")
    assert thing["made_by"] == "turn" and thing["made_by_thing"] == turn["id"]
    assert w.store.turn(41)["thing"] == turn["id"]
    assert [ln["dst"] for ln in w.store.links_from(thing["id"], ("made_by",))] == [turn["id"]]
    w.close()


def test_stop_between_batches_leaves_the_index_unfinished(home, monkeypatch):
    monkeypatch.setattr(index, "BATCH", 2)
    _tree(home)
    w = Walker(_db(home), str(home))
    calls = []

    def stop():
        calls.append(1)
        return len(calls) > 3

    assert w.first_index(stop=stop)["stopped"] is True
    assert w.store.get_meta("indexed") is None
    assert w.first_index()["stopped"] is False   # picks up again
    assert w.store.get_meta("indexed") is not None
    w.close()


def test_reconcile_sees_renames_by_inode_deletions_and_new_files(home):
    _tree(home)
    h = str(home)
    now = time.time()
    os.utime(f"{h}/Projects/latchkey/src/main.py", (now - 7200, now - 7200))
    w = Walker(_db(home), str(home))
    w.first_index()
    lease = w.store.by_path(f"{h}/Documents/Lease/lease-2026.pdf")
    notes = w.store.by_path(f"{h}/Documents/Lease/notes")
    call = w.store.by_path(f"{h}/Documents/Lease/notes/call.txt")
    menu = w.store.by_path(f"{h}/café ☕/menu.txt")
    main = w.store.by_path(f"{h}/Projects/latchkey/src/main.py")

    os.rename(f"{h}/Documents/Lease/lease-2026.pdf", f"{h}/Documents/Lease/lease-signed.pdf")
    os.rename(f"{h}/Documents/Lease/notes", f"{h}/Documents/Lease/phone notes")
    os.unlink(f"{h}/café ☕/menu.txt")
    (home / "Documents" / "Lease" / "phone notes" / "second-call.txt").write_text("again")
    (home / "Downloads").mkdir()
    (home / "Downloads" / "new.pdf").write_text("%PDF")
    os.utime(f"{h}/Projects/latchkey/src/main.py", (now - 3600, now - 3600))  # saved while nobody watched

    counts = w.reconcile()
    assert counts == {"found": 3, "moved": 2, "gone": 1, "changed": 1, "stopped": False}
    moved = w.store.get(lease["id"])
    assert moved["path"] == f"{h}/Documents/Lease/lease-signed.pdf" and moved["deleted"] is None
    assert moved["title"] == "lease-signed.pdf"
    ev = w.store.events(lease["id"], kinds=("move",))[0]
    assert ev["actor"] == "unknown" and ev["detail"]["from"] == f"{h}/Documents/Lease/lease-2026.pdf"
    # a folder that moved takes what is inside it along
    assert w.store.get(notes["id"])["path"] == f"{h}/Documents/Lease/phone notes"
    assert w.store.get(call["id"])["path"] == f"{h}/Documents/Lease/phone notes/call.txt"
    assert w.store.get(call["id"])["deleted"] is None
    new = w.store.by_path(f"{h}/Documents/Lease/phone notes/second-call.txt")
    assert new is not None and new["parent"] == notes["id"]
    assert new["made_by"] == "unknown"   # made after the first index, while nobody watched
    gone = w.store.get(menu["id"])
    assert gone["deleted"] is not None
    assert w.store.events(menu["id"], kinds=("delete",))[0]["actor"] == "unknown"
    assert w.store.by_path(f"{h}/Downloads/new.pdf") is not None
    change = w.store.events(main["id"], kinds=("change",))[0]
    assert change["actor"] == "unknown" and change["t"] == pytest.approx(now - 3600)
    assert w.store.get_meta("reconciled") is not None
    # a second reconcile has nothing left to say
    assert w.reconcile() == {"found": 0, "moved": 0, "gone": 0, "changed": 0, "stopped": False}
    w.close()


def test_reconcile_does_not_take_a_reused_inode_for_a_move(home):
    a, b = home / "a.txt", home / "b.txt"
    a.write_text("a")
    b.write_text("b")
    w = Walker(_db(home), str(home))
    w.first_index()
    thing = w.store.by_path(str(a))
    # an inode number that turns up again as a folder is not the file moving
    a.unlink()
    d = home / "some folder"
    d.mkdir()
    w.store.x("UPDATE things SET ino = ? WHERE id = ?", (os.stat(d).st_ino, thing["id"]))
    counts = w.reconcile()
    assert counts["moved"] == 0 and counts["gone"] == 1
    assert w.store.get(thing["id"])["deleted"] is not None
    assert w.store.by_path(str(d))["kind"] == "folder"
    w.close()


def test_reconcile_does_not_take_a_new_file_on_an_old_inode_for_a_rename(home):
    a = home / "a.txt"
    a.write_text("the old file")
    w = Walker(_db(home), str(home))
    w.first_index()
    thing = w.store.by_path(str(a))
    a.unlink()
    b = home / "b.txt"
    b.write_text("something new")   # ext4 hands out a's inode again at once
    w.store.x("UPDATE things SET ino = ? WHERE id = ?", (os.stat(b).st_ino, thing["id"]))
    counts = w.reconcile()
    assert (counts["moved"], counts["gone"], counts["found"]) == (0, 1, 1)
    assert w.store.by_path(str(b))["id"] != thing["id"]
    w.close()


def test_reconcile_does_not_take_a_different_file_with_the_same_name_for_a_move(home):
    (home / "Docs").mkdir()
    (home / "Other").mkdir()
    old = home / "Docs" / "report.odt"
    old.write_text("the Q3 report, version one")
    os.utime(old, (time.time() - 86400, time.time() - 86400))
    w = Walker(_db(home), str(home))
    w.first_index()
    thing = w.store.by_path(str(old))
    old.unlink()
    new = home / "Other" / "report.odt"
    new.write_text("a different letter, written later and longer than the report")
    w.store.x("UPDATE things SET ino = ? WHERE id = ?", (os.stat(new).st_ino, thing["id"]))   # ext4 does this
    counts = w.reconcile()
    assert (counts["moved"], counts["gone"], counts["found"]) == (0, 1, 1)
    assert w.store.get(thing["id"])["deleted"] is not None
    assert w.store.by_path(str(new))["id"] != thing["id"]
    # the same file moved (even resized: same name, one candidate, unedited) is still the same thing
    moved = home / "Docs" / "kept.odt"
    moved.write_text("kept")
    os.utime(moved, (time.time() - 86400, time.time() - 86400))
    w.reconcile()
    kept = w.store.by_path(str(moved))
    os.rename(moved, home / "Other" / "kept.odt")
    assert w.reconcile()["moved"] == 1
    assert w.store.get(kept["id"])["path"] == str(home / "Other" / "kept.odt")
    w.close()


def test_moves_to_where_the_brain_does_not_look_are_deletions(home):
    _tree(home)
    w = Walker(_db(home), str(home))
    w.first_index()
    call = w.store.by_path(f"{home}/Documents/Lease/notes/call.txt")
    os.rename(f"{home}/Documents/Lease/notes/call.txt", f"{home}/Projects/latchkey/build/call.txt")
    assert w.reconcile()["gone"] == 1
    assert w.store.get(call["id"])["deleted"] is not None
    w.close()


def test_a_home_that_is_not_there_is_an_error_not_a_finished_index(home):
    w = Walker(_db(home), str(home / "nobody"))
    with pytest.raises(OSError):
        w.first_index()
    assert w.store.get_meta("indexed") is None   # it would never be indexed if this said it was
    with pytest.raises(OSError):
        w.reconcile()
    w.close()


def test_a_home_that_cannot_be_listed_is_an_error_but_a_folder_in_it_is_just_empty(home, monkeypatch):
    (home / "locked" / "inner").mkdir(parents=True)
    w = Walker(_db(home), str(home))
    real = os.scandir

    def scandir(path):
        if str(path) == f"{home}/locked":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(index.os, "scandir", scandir)
    assert w.first_index()["stopped"] is False
    assert w.store.by_path(f"{home}/locked") is not None and w.store.by_path(f"{home}/locked/inner") is None

    def home_scandir(path):
        if str(path) == str(home):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(index.os, "scandir", home_scandir)
    w.store.x("DELETE FROM meta WHERE key = 'indexed'")
    with pytest.raises(PermissionError):
        w.first_index()
    assert w.store.get_meta("indexed") is None
    w.close()


def test_reconcile_of_a_home_that_looks_empty_deletes_nothing(home):
    """An encrypted home that is not mounted yet is an empty folder; that is not everything gone."""
    (home / "Documents").mkdir()
    for i in range(5):
        (home / "Documents" / f"f{i}.txt").write_text("x")
    w = Walker(_db(home), str(home))
    w.first_index()
    live = w.store.counts()["live"]
    assert live >= 7
    away = home.parent / (home.name + "-away")
    (home / "Documents").rename(away)
    with pytest.raises(OSError, match="looks empty"):
        w.reconcile()
    assert w.store.counts()["live"] == live
    away.rename(home / "Documents")
    assert w.reconcile()["gone"] == 0
    w.close()


def test_a_home_with_more_entries_than_the_cap_is_still_indexed(home, monkeypatch):
    monkeypatch.setattr(index, "MAX_ENTRIES", 20)
    for i in range(25):
        (home / f"note-{i}.txt").write_text("x")
    w = Walker(_db(home), str(home))
    w.first_index()
    assert w.store.by_path(f"{home}/note-24.txt") is not None
    assert w.store.by_path(str(home))["meta"] == {}   # only other folders are "a folder of N files"
    w.close()


def test_symlinks_are_never_followed_out_of_home_and_a_loop_ends(home):
    outside = home / "outside"
    outside.mkdir()
    (outside / "elsewhere.txt").write_text("not mine")
    h = home / "h"
    (h / "Docs").mkdir(parents=True)
    (h / "Docs" / "a.txt").write_text("a")
    (h / "Docs" / "up").symlink_to("..")                 # a loop
    (h / "Docs" / "self").symlink_to(".")
    (h / "out").symlink_to(outside)                      # out of home
    (h / "out-file").symlink_to(outside / "elsewhere.txt")
    (h / "dangling").symlink_to(home / "gone")
    w = Walker(_db(home), str(h))
    assert w.first_index()["stopped"] is False
    assert _paths(w) == {str(h), f"{h}/Docs", f"{h}/Docs/a.txt"}
    w.reconcile()
    assert _paths(w) == {str(h), f"{h}/Docs", f"{h}/Docs/a.txt"}
    w.close()


def test_lower_priority_is_per_thread(monkeypatch):
    monkeypatch.undo()   # the real one, in a thread of its own
    main_nice = os.nice(0)
    out = {}

    def run():
        index.lower_priority()
        index.lower_priority()   # once per thread, not 20
        out["nice"] = os.nice(0)
        if platform.machine() == "x86_64":
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            out["ioprio"] = libc.syscall(252, 1, 0)   # ioprio_get(IOPRIO_WHO_PROCESS, this thread)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert out["nice"] == min(main_nice + 10, 19)
    assert os.nice(0) == main_nice
    if "ioprio" in out:
        assert out["ioprio"] >> 13 == 3


def test_a_file_moved_out_of_a_folder_that_was_then_deleted_keeps_its_identity(home):
    (home / "Old" / "keep").mkdir(parents=True)
    (home / "Old" / "keep" / "report.odt").write_text("q3")
    (home / "Old" / "junk.txt").write_text("junk")
    w = Walker(_db(home), str(home))
    w.first_index()
    report = w.store.by_path(f"{home}/Old/keep/report.odt")
    os.rename(home / "Old" / "keep" / "report.odt", home / "report.odt")
    subprocess.run(["rm", "-r", str(home / "Old")], check=True)
    counts = w.reconcile()
    assert counts["moved"] == 1 and counts["gone"] >= 1
    assert w.store.get(report["id"])["path"] == f"{home}/report.odt"
    assert w.store.get(report["id"])["deleted"] is None
    assert w.store.by_path(f"{home}/Old", deleted=True)["deleted"] is not None
    w.close()


def test_a_file_a_turn_wrote_is_not_an_unseen_save(home):
    from bombadil.brain.ingest import Who
    w = Walker(_db(home), str(home))
    w.first_index()
    now = time.time()
    f = home / "setup-wg.sh"
    f.write_text("#!/bin/sh\n")
    os.utime(f, (now - 100, now - 100))   # written 20 s into a turn that ran from -120 to -60
    turn = w.ingest.turn(41, prompt="install the VPN", started=now - 120, ended=now - 60)
    w.ingest.saw(str(f), "create", now - 120, Who("turn", turn))
    assert w.reconcile()["changed"] == 0
    os.utime(f, (now - 30, now - 30))     # then someone saved it while nobody watched
    assert w.reconcile()["changed"] == 1
    w.close()


def test_a_folder_that_cannot_be_read_is_not_gone(home, monkeypatch):
    (home / "Taxes").mkdir()
    (home / "Taxes" / "2026.pdf").write_text("%PDF")
    w = Walker(_db(home), str(home))
    w.first_index()
    real = os.lstat

    def lstat(path, *a, **kw):
        if str(path).startswith(f"{home}/Taxes/"):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(index.os, "lstat", lstat)
    assert w.reconcile()["gone"] == 0
    assert w.store.by_path(f"{home}/Taxes/2026.pdf") is not None
    w.close()
