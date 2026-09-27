"""The brain's core: brain.db, what gets in, who did it, and how events become things and links."""

import os
import time

import pytest

from bombadil.brain import actors, rules, words
from bombadil.brain.ingest import Ingest, Who, fingerprint_url
from bombadil.brain.store import Store

TURN_CG = "/user.slice/user-1000.slice/user@1000.service/app.slice/bombadil-turn-99-3-1727429990.scope"
YOU_CG = "/user.slice/user-1000.slice/session-1.scope"
FOOT = [[5, "nvim", "nvim notes.md"], [4, "bash", "bash"], [3, "foot", "foot"], [2, "Hyprland", "Hyprland"]]
CHROME = [[7, "chromium", "chromium --class=bombadil-browser"], [2, "Hyprland", "Hyprland"]]


@pytest.fixture
def brain(tmp_path):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    store = Store()
    return store, Ingest(store, str(home), xattrs=False), str(home)


def ev(op, path, t, cgroup=YOU_CG, chain=FOOT, **extra):
    return {"op": op, "path": path, "t": t, "uid": 1000, "comm": chain[0][1] if chain else "", "cgroup": cgroup,
            "chain": chain, **extra}


# -- store --

def test_store_keys_paths_and_search():
    s = Store()
    a = s.add("file", "", path="/h/lease-2026.pdf", touched=10)
    b = s.upsert_key("page", "url:https://x.org/", "Lease renewal 2026", url="https://x.org/")
    assert s.upsert_key("page", "url:https://x.org/") == b
    assert s.by_path("/h/lease-2026.pdf")["title"] == "lease-2026.pdf"
    assert {t["id"] for t in s.search("lease")} == {a, b}
    assert {t["id"] for t in s.search("le")} == {a, b}   # under three letters: the start of a name
    assert s.search("re") == []
    assert s.search("") == []


def test_store_live_paths_are_unique_but_history_stays():
    s = Store()
    a = s.add("file", "", path="/h/a")
    s.mark_deleted(a, 5)
    b = s.add("file", "", path="/h/a")
    assert s.by_path("/h/a")["id"] == b
    assert s.by_path("/h/zzz", deleted=True) is None
    s.mark_deleted(b, 6)
    assert s.by_path("/h/a", deleted=True)["id"] == b
    assert not s.revive(a) or s.by_path("/h/a")["id"] == a


def test_store_move_carries_what_is_inside():
    s = Store()
    d = s.add("folder", "", path="/h/Lease")
    f = s.add("file", "", path="/h/Lease/letter.odt", parent=d)
    s.add("file", "", path="/h/Lease2/other")
    s.move(d, "/h/Leases", None, None)
    assert s.get(f)["path"] == "/h/Leases/letter.odt"
    assert s.by_path("/h/Lease2/other") is not None   # a sibling with the same prefix is left alone
    assert s.search("Leases")[0]["id"] in (d, f)


def test_store_changes_fold_together():
    s = Store()
    f = s.add("file", "", path="/h/log.txt")
    s.event(100, "change", f, "you", via="foot")
    s.event(130, "change", f, "you", via="foot")
    s.event(500, "change", f, "you", via="foot")
    s.event(510, "change", f, "turn", actor_thing=9)
    evs = s.events(f)
    assert [(e["t"], e["n"]) for e in evs] == [(510, 1), (500, 1), (100, 2)]


def test_store_merge_things_moves_history():
    s = Store()
    placeholder = s.upsert_key("turn", "unit:bombadil-turn-1", "a turn now running")
    real = s.upsert_key("turn", "turn:4", "install the VPN")
    f = s.add("file", "", path="/h/x", made_by="turn", made_by_thing=placeholder)
    s.event(1, "create", f, "turn", placeholder)
    s.link(f, placeholder, "made_by", 1)
    s.merge_things(real, placeholder)
    assert s.get(placeholder) is None
    assert s.get(f)["made_by_thing"] == real
    assert s.links_from(f)[0]["dst"] == real
    assert s.events(f)[0]["actor_thing"] == real


def test_store_rolls_back_a_failed_transaction():
    s = Store()
    with pytest.raises(RuntimeError), s.tx():
        s.add("file", "", path="/h/a")
        raise RuntimeError
    assert s.by_path("/h/a") is None


# -- rules --

@pytest.mark.parametrize("rel,kind,private", [
    ("notes.md", "thing", False),
    ("Projects/x/src/main.py", "thing", False),
    ("Projects/x/node_modules/a/index.js", "skip", False),
    ("Projects/x/target/debug/x", "skip", False),
    (".cache/thumbnails/a.png", "skip", False),
    (".config/chromium/Default/History", "skip", False),
    (".config/hypr/hyprland.lua", "thing", False),
    (".bashrc", "thing", False),
    (".ssh/id_ed25519", "thing", True),
    ("Documents/passwords.txt", "thing", True),
    ("Downloads/lease.pdf.crdownload", "skip", False),
    ("notes.md.swp", "skip", False),
    (".notes.md.swp", "skip", False),
    ("4913", "skip", False),
    ("Apps/passwords/data/vault.json", "rollup", True),
    ("Apps/passwords/main.qml", "thing", False),
    (".local/share/Trash/files/a", "trash", False),
    (".local/state/bombadil/brain.db", "skip", False),
])
def test_rules_classify(rel, kind, private):
    v = rules.classify(f"/home/u/{rel}", "/home/u")
    assert (v.kind, v.private) == (kind, private)


def test_rules_outside_home_and_etc():
    assert rules.classify("/usr/lib/libc.so", "/home/u").kind == "skip"
    assert rules.classify("/etc/resolv.conf", "/home/u").kind == "thing"
    assert rules.classify("/etc/ld.so.cache", "/home/u").kind == "skip"
    assert rules.classify("/etc/shadow", "/home/u").private


@pytest.mark.parametrize("rel,is_dir,area", [
    ("notes.md", False, ("home", "Home")),
    ("Lease", True, ("folder", "Lease")),
    ("Lease/letter.odt", False, ("folder", "Lease")),
    ("Documents/Taxes 2026/w2.pdf", False, ("folder", "Taxes 2026")),
    ("Documents/Taxes 2026", True, ("folder", "Taxes 2026")),
    ("Documents/cv.pdf", False, ("folder", "Documents")),
    ("Projects/bombadil/src/a.py", False, ("project", "bombadil")),
    ("Apps/passwords/main.qml", False, ("app", "passwords")),
])
def test_rules_areas(rel, is_dir, area):
    a = rules.area_of(f"/home/u/{rel}", "/home/u", is_dir)
    assert (a.kind, a.title) == area
    assert rules.area_of("/etc/hosts", "/home/u").kind == "system"


def test_gitignore_asks_git_in_one_batch(tmp_path):
    repo = tmp_path / "Projects" / "x"
    (repo / ".git").mkdir(parents=True)
    calls = []

    def run(cmd, input, **kw):
        calls.append((cmd, input))

        class R:
            returncode = 0
            stdout = "build/out.o\0"
        return R()
    g = rules.GitIgnore(runner=run)
    got = g.ignored([str(repo / "build/out.o"), str(repo / "src/a.c"), str(tmp_path / "loose.txt")], stop=str(tmp_path))
    assert got == {str(repo / "build/out.o")}
    assert len(calls) == 1 and calls[0][1] == "build/out.o\0src/a.c\0"
    g.ignored([str(repo / "build/out.o")], stop=str(tmp_path))
    assert len(calls) == 1   # remembered


# -- actors --

def test_actors_turn_session_app_you_system():
    assert actors.from_event({"cgroup": TURN_CG, "comm": "bash"}) == actors.Actor(
        "turn", "unit:bombadil-turn-99-3-1727429990", "bash")
    dev = "/user.slice/user-1000.slice/user@1000.service/bombadil.slice/bombadil-dev.slice/" \
          "bombadil-dev-bombadil.slice/bombadil-dev-bombadil-builder.scope"
    assert actors.from_event({"cgroup": dev, "comm": "node"}).key == "session:bombadil"
    esc = "/x/bombadil-dev.slice/bombadil-dev-my\\x2dapp.slice/s.scope"
    assert actors.from_event({"cgroup": esc}).key == "session:my-app"
    app = actors.from_event({"cgroup": YOU_CG, "chain": [[9, "python3", "python3 /usr/bin/bombadil-app run passwords"]]})
    assert app.key == "app:passwords"
    you = actors.from_event({"cgroup": YOU_CG, "uid": 1000, "comm": "nvim", "chain": FOOT})
    assert (you.kind, you.via, you.prog) == ("you", "foot", "nvim")
    assert actors.from_event({"cgroup": "/system.slice/x.service", "uid": 0, "comm": "pacman"}).kind == "system"
    assert actors.from_event({"op": "offline"}).kind == "unknown"


def test_actors_session_names_come_from_the_registry(tmp_path):
    reg = tmp_path / "sessions.json"
    reg.write_text('[{"name": "builder", "unit": "bombadil-dev-bombadil-builder.scope"}]')
    cg = "/a/bombadil-dev.slice/bombadil-dev-bombadil.slice/bombadil-dev-bombadil-builder.scope"
    assert actors.from_event({"cgroup": cg}, actors.Sessions(reg)).key == "session:bombadil/builder"


# -- words --

def test_words_when_and_sentences():
    now = time.mktime((2026, 9, 27, 15, 0, 0, 0, 0, -1))   # a Sunday
    assert words.when(now - 3600, now) == "14:00"
    assert words.when(now - 86400, now) == "yesterday 15:00"
    assert words.when(now - 5 * 86400, now) == "Tue 15:00"
    assert words.when(now - 30 * 86400, now) == "28 Aug"
    turn = words.Who("turn", 41, "install the VPN")
    assert words.made_sentence(turn, now - 6 * 86400, now) == "Made by the machine in turn 41, “install the VPN”, on Monday."
    assert words.made_sentence(words.Who("you", via="foot"), now - 60, now) == "You made it in the terminal today at 14:59."
    assert words.who(words.Who("session", name="builder", project="Bombadil")) == "builder on Bombadil"
    assert words.quoted("x" * 100, 10) == "“xxxxxxxxx…”"


# -- ingest --

def test_ingest_a_turn_makes_a_file_and_its_number_arrives_later(brain):
    store, ing, home = brain
    t0 = 1000.0
    ing.apply([ev("create", f"{home}/setup-wg.sh", t0, TURN_CG, [[1, "bash", "bash -c x"]], gone=True),
               ev("write", f"{home}/setup-wg.sh", t0 + 1, TURN_CG, [[1, "bash", "bash -c x"]], gone=True)])
    f = store.by_path(f"{home}/setup-wg.sh")
    running = store.get(f["made_by_thing"])
    assert running["kind"] == "turn" and running["title"] == "a turn now running"
    ing.turn_row({"t": t0 + 9, "started": t0 - 1, "n": 41, "unit": "bombadil-turn-99-3-1727429990",
                  "prompt": "install the VPN", "files": {"wrote": [f"{home}/setup-wg.sh"], "read": []}}, 41)
    turn = store.by_key("turn:41")
    assert store.get(f["id"])["made_by_thing"] == turn["id"]
    assert turn["title"] == "install the VPN"
    assert store.turn_by_unit("bombadil-turn-99-3-1727429990")["n"] == 41
    # the turn's own record did not add a second change: the watcher already saw it
    assert [e["kind"] for e in store.events(f["id"])] == ["change", "create"]
    # a late event from the same scope lands on the numbered turn
    ing.apply([ev("write", f"{home}/setup-wg.sh", t0 + 120, TURN_CG, [])])
    assert store.events(f["id"])[0]["actor_thing"] == turn["id"]


def test_ingest_save_by_rename_keeps_the_thing(brain):
    store, ing, home = brain
    ing.apply([ev("create", f"{home}/notes.md", 1, chain=FOOT), ev("write", f"{home}/notes.md", 2, chain=FOOT)])
    first = store.by_path(f"{home}/notes.md")["id"]
    # an editor writes .notes.md.tmp-ish name that is not a temp pattern, then renames it over
    ing.apply([ev("create", f"{home}/notes.md.new1", 100), ev("write", f"{home}/notes.md.new1", 101),
               ev("rename", f"{home}/notes.md", 102, old=f"{home}/notes.md.new1")])
    assert store.by_path(f"{home}/notes.md")["id"] == first
    assert store.by_path(f"{home}/notes.md.new1") is None
    assert store.by_path(f"{home}/notes.md.new1", deleted=True) is None   # scaffolding is purged


def test_ingest_vim_backup_rename_then_write_keeps_the_thing(brain):
    store, ing, home = brain
    ing.apply([ev("create", f"{home}/a.txt", 1)])
    first = store.by_path(f"{home}/a.txt")["id"]
    ing.apply([ev("rename", f"{home}/a.txt~", 50, old=f"{home}/a.txt"),
               ev("create", f"{home}/a.txt", 50.1), ev("write", f"{home}/a.txt", 50.2),
               ev("delete", f"{home}/a.txt~", 50.3)])
    assert store.by_path(f"{home}/a.txt")["id"] == first


def test_ingest_move_rename_trash_and_delete(brain):
    store, ing, home = brain
    ing.apply([ev("create", f"{home}/Lease", 1, dir=True), ev("create", f"{home}/Lease/letter.odt", 2)])
    letter = store.by_path(f"{home}/Lease/letter.odt")["id"]
    ing.apply([ev("rename", f"{home}/Documents/Lease", 100, old=f"{home}/Lease", dir=True)])
    moved = store.get(letter)
    assert moved["path"] == f"{home}/Documents/Lease/letter.odt"
    assert store.get(moved["area"])["title"] == "Lease"
    ing.apply([ev("rename", f"{home}/.local/share/Trash/files/letter.odt", 200, old=moved["path"])])
    assert store.get(letter)["deleted"] == 200
    assert store.events(letter)[0]["kind"] == "trash"


def test_ingest_a_download_comes_from_its_page(brain):
    store, ing, home = brain
    ing.apply([ev("create", f"{home}/Downloads/lease.pdf.crdownload", 1, chain=CHROME),
               ev("rename", f"{home}/Downloads/lease.pdf", 2, chain=CHROME,
                  old=f"{home}/Downloads/lease.pdf.crdownload")])
    f = store.by_path(f"{home}/Downloads/lease.pdf")
    assert (f["made_by"], f["made_via"]) == ("you", "chromium")
    ing.visit("https://rent-portal.example/renewal#top", "Lease renewal 2026", 0.5)
    ing.download(f"{home}/Downloads/lease.pdf", "https://cdn.example/l.pdf", "https://rent-portal.example/renewal", 2.5)
    page = store.by_key("url:https://rent-portal.example/renewal")
    assert page["title"] == "Lease renewal 2026"
    assert store.links_from(f["id"], ("came_from",))[0]["dst"] == page["id"]
    assert store.get(page["area"])["title"] == "rent-portal.example"
    ing.download(f"{home}/Downloads/lease.pdf", "https://cdn.example/l.pdf", "https://rent-portal.example/renewal", 3)
    assert len([e for e in store.events(f["id"]) if e["kind"] == "download"]) == 1


def test_ingest_apps_data_counts_as_the_app(brain):
    store, ing, home = brain
    ing.apply([ev("write", f"{home}/Apps/passwords/data/vault.json", 5,
                  chain=[[9, "python3", "python3 bombadil-app run passwords"]])])
    app = store.by_key("app:passwords")
    assert app is not None and store.by_path(f"{home}/Apps/passwords/data/vault.json") is None
    assert store.events(app["id"])[0]["actor"] == "app"


def test_ingest_turn_rows_link_what_was_read(brain, tmp_path):
    store, ing, home = brain
    src = f"{home}/Downloads/lease.pdf"
    open(src, "w").close()
    ing.apply([ev("create", src, 1)])
    out = f"{home}/letter.odt"
    open(out, "w").close()
    ing.turn_row({"t": 20, "started": 10, "n": 57, "prompt": "draft a reply", "files": {"wrote": [out], "read": [src]}}, 57)
    letter = store.by_path(out)
    assert letter["made_by"] == "turn"
    assert store.links_from(letter["id"], ("came_from",))[0]["dst"] == store.by_path(src)["id"]


def test_ingest_packages_and_facts(brain):
    store, ing, home = brain
    turn = ing.turn(3, prompt="install ffmpeg", started=1, ended=9)
    ing.package("ffmpeg", "2:7.1-1", "install", 5, Who("turn", turn, "pacman"))
    pkg = store.by_key("package:ffmpeg")
    assert store.get(pkg["area"])["key"] == "system"
    assert store.links_from(pkg["id"], ("made_by",))[0]["dst"] == turn
    ing.package("ffmpeg", "2:7.1-1", "remove", 50, Who("system", None, "pacman"))
    assert store.by_key("package:ffmpeg")["deleted"] == 50
    ing.facts(["- Daniel likes dark themes", "# heading", "- uses zsh"], 10, Who("turn", turn), "memory.md")
    ing.facts(["- Daniel likes dark themes"], 20, Who("you"), "memory.md")
    live = store.q("SELECT title FROM things WHERE kind = 'fact' AND deleted IS NULL")
    assert [r["title"] for r in live] == ["Daniel likes dark themes"]


def test_ingest_found_reads_xattrs(brain, tmp_path):
    store, ing, home = brain
    p = f"{home}/made.sh"
    open(p, "w").close()
    try:
        os.setxattr(p, "user.bombadil.made_by", b"turn 41")
    except OSError:
        pytest.skip("no user xattrs here")
    tid = ing.found(p, os.stat(p), False)
    thing = store.get(tid)
    assert thing["made_by"] == "turn" and store.get(thing["made_by_thing"])["key"] == "turn:41"


def test_fingerprint_url():
    assert fingerprint_url("HTTPS://Example.org/a?b=1#frag") == "https://example.org/a?b=1"
    assert fingerprint_url("chrome://settings") is None
    assert fingerprint_url("not a url") is None
