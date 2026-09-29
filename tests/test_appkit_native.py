"""The native types behind `import Bombadil`, driven from Python and from small QML snippets.

Native types register once per process and their singletons belong to one engine, so the
whole module shares one offscreen app, one AppContext and one QML engine.
"""

import asyncio
import contextlib
import gc
import json
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QEventLoop, QMetaObject, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlComponent  # noqa: E402

from bombadil.appkit import engine as kit_engine  # noqa: E402
from bombadil.appkit import native  # noqa: E402
from bombadil.appkit.context import AppContext  # noqa: E402


@pytest.fixture(scope="session")
def kit(tmp_path_factory):
    if native._registered is None:
        d = tmp_path_factory.mktemp("kit-app")
        ctx = AppContext("kit-test", "Kit Test", d, d / "main.qml")
    else:  # another test module registered first: share its context
        ctx = native.context()
    app = QGuiApplication.instance() or kit_engine.make_app(ctx)
    engine = kit_engine.make_engine(ctx)
    return SimpleNamespace(ctx=ctx, app=app, engine=engine, n=0)


@pytest.fixture
def checking(kit):
    """Run the test as `bombadil-app check` would: read-only."""
    kit.ctx.check = True
    try:
        yield
    finally:
        kit.ctx.check = False


def spin(ms: int):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout_ms: int = 5000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if cond():
            return True
        spin(20)
    return cond()


def make(kit, source: str):
    """Create a QML snippet (with `import QtQuick` and `import Bombadil`) in the shared engine."""
    kit.n += 1
    comp = QQmlComponent(kit.engine)
    comp.setData(("import QtQuick\nimport Bombadil\n" + source).encode(),
                 QUrl.fromLocalFile(str(kit.ctx.dir / f"snippet{kit.n}.qml")))
    obj = comp.create()
    assert obj is not None, [e.toString() for e in comp.errors()]
    obj._component = comp
    return obj


def destroy(obj):
    obj.deleteLater()
    spin(10)


def js(value):
    """A value read back from QML as plain Python."""
    return native.files.from_js(value)


# -- Store --

STORE = """
Item {
    property var store: st
    Store {
        id: st
        name: "%s"
        property var entries: []
        property string filter: "all"
        property int selected: -1
        property color tint: "#d97757"
        readonly property int answer: 42
    }
}
"""


def test_store_round_trip(kit):
    root = make(kit, STORE % "rt")
    store = root.property("store")
    assert store.property("loaded") is True
    assert js(store.property("_keys")) == ["entries", "filter", "selected", "tint"]
    store.setProperty("entries", [{"title": "a", "n": 1, "ok": True}])
    store.setProperty("filter", "open")
    store.setProperty("tint", "#00ff00")
    path = kit.ctx.data_dir / "rt.json"
    assert wait_until(path.exists, 2000)
    saved = json.loads(path.read_text())
    assert saved == {"entries": [{"title": "a", "n": 1, "ok": True}], "filter": "open", "selected": -1,
                     "tint": "#00ff00"}
    destroy(root)

    again = make(kit, STORE % "rt").property("store")
    assert js(again.property("entries")) == [{"title": "a", "n": 1, "ok": True}]
    assert again.property("filter") == "open"
    assert again.property("tint").name() == "#00ff00"
    QMetaObject.invokeMethod(again, "reset")
    assert again.property("filter") == "all" and js(again.property("entries")) == []
    assert not path.exists()


def test_store_values_are_there_before_on_completed(kit):
    (kit.ctx.data_dir).mkdir(parents=True, exist_ok=True)
    (kit.ctx.data_dir / "early.json").write_text('{"count": 7}')
    root = make(kit, """
Item {
    property int seen: -1
    Store { id: st; name: "early"; property int count: 0 }
    Component.onCompleted: seen = st.count
}""")
    assert root.property("seen") == 7


def test_store_saves_before_reload(kit):
    from bombadil.appkit.native import app as app_mod

    store = make(kit, STORE % "reload").property("store")
    store.setProperty("selected", 3)
    app_mod.instance().aboutToReload.emit()      # sooner than the 300 ms debounce
    assert json.loads((kit.ctx.data_dir / "reload.json").read_text())["selected"] == 3


def test_store_saves_nan_and_infinity_as_null(kit):
    """`NaN` in the file would fail JSON.parse on the next start, and the Store would start over."""
    src = """
Item {
    property var store: st
    Store { id: st; name: "nan"; property real avg: NaN; property var entries: [] }
}"""
    store = make(kit, src).property("store")
    assert store.property("loaded") is True            # the NaN default did not break the snapshot
    store.setProperty("entries", [1.5, float("inf"), {"x": float("nan")}])
    path = kit.ctx.data_dir / "nan.json"
    assert wait_until(path.exists, 2000)
    assert json.loads(path.read_text()) == {"avg": None, "entries": [1.5, None, {"x": None}]}
    destroy(store.parent())
    again = make(kit, src).property("store")
    assert again.property("loaded") is True and js(again.property("entries")) == [1.5, None, {"x": None}]


def test_kitfiles_quarantine_keeps_every_damaged_file(kit):
    from bombadil.appkit.native.files import KitFiles

    kf = KitFiles(kit.ctx)
    bad = kit.ctx.data_dir / "damaged.json"
    for text in ("{oops", "{again"):
        bad.write_text(text)
        moved = kf.quarantine("damaged.json")
        assert moved and Path(moved).read_text() == text and not bad.exists()
    assert sorted(p.name for p in kit.ctx.data_dir.glob("damaged.json*")) == ["damaged.json.bad", "damaged.json.bad.2"]
    assert kf.quarantine("damaged.json") == ""           # nothing there
    obj = make(kit, "QtObject { property real avg: NaN; property var list: [1, Infinity] }")
    assert json.loads(kf.snapshot(obj, ["avg", "list"])) == {"avg": None, "list": [1, None]}


def test_store_moves_a_damaged_file_aside(kit):
    path = kit.ctx.data_dir / "damaged-store.json"
    path.write_text('{"filter": "open", oops')
    store = make(kit, STORE % "damaged-store").property("store")
    assert store.property("loaded") is True and store.property("filter") == "all"
    assert (kit.ctx.data_dir / "damaged-store.json.bad").read_text() == '{"filter": "open", oops'
    store.setProperty("filter", "new")
    assert wait_until(path.exists, 2000)
    assert json.loads(path.read_text())["filter"] == "new"
    assert (kit.ctx.data_dir / "damaged-store.json.bad").read_text() == '{"filter": "open", oops'


def test_store_never_writes_in_check(kit, checking):
    store = make(kit, STORE % "checked").property("store")
    store.setProperty("filter", "changed")
    spin(450)
    QMetaObject.invokeMethod(store, "save")
    assert not (kit.ctx.data_dir / "checked.json").exists()


# -- Vault --

def vault(kit, name: str, extra: str = ""):
    return make(kit, f'Item {{ property var vault: v; Vault {{ id: v; name: "{name}"; {extra} }} }}').property("vault")


@pytest.fixture
def fast_kdf(monkeypatch):
    """New vaults with cheap scrypt parameters: the real ones take half a second per call."""
    from bombadil.appkit.native import vault as vault_mod

    monkeypatch.setattr(vault_mod, "KDF", {"name": "scrypt", "n": 2 ** 10, "r": 8, "p": 1})


def test_vault_create_unlock_and_change_password(kit, fast_kdf):
    v = vault(kit, "secrets")
    assert v.property("exists") is False and v.property("unlocked") is False
    assert v.create("correct horse") is True
    assert v.property("unlocked") is True and js(v.property("data")) == []
    entries = [{"title": "GitHub", "user": "me", "password": "hunter2-Secret", "n": 3, "fav": True}]
    v.setProperty("data", entries)
    raw = (kit.ctx.data_dir / "secrets.vault").read_text()
    assert "hunter2" not in raw and "GitHub" not in raw
    doc = json.loads(raw)
    assert doc["version"] == 1 and doc["kdf"]["name"] == "scrypt" and {"salt", "nonce", "ciphertext"} <= set(doc)
    assert stat.S_IMODE(os.stat(kit.ctx.data_dir / "secrets.vault").st_mode) == 0o600

    v.setProperty("data", entries)             # same value, fresh nonce
    assert json.loads((kit.ctx.data_dir / "secrets.vault").read_text())["nonce"] != doc["nonce"]

    v.lock()
    assert v.property("unlocked") is False and js(v.property("data")) is None
    assert v.unlock("wrong") is False and v.property("error") == "wrong password"
    assert v.unlock("correct horse") is True and js(v.property("data")) == entries
    assert v.property("error") == ""

    assert v.changePassword("nope", "new") is False
    assert v.changePassword("correct horse", "battery staple") is True
    v.lock()
    assert v.unlock("correct horse") is False
    assert v.unlock("battery staple") is True and js(v.property("data")) == entries
    assert v.create("again") is False          # one vault per name


def test_vault_new_files_use_owasp_scrypt_and_old_files_still_open(kit, monkeypatch):
    from bombadil.appkit.native import vault as vault_mod

    fresh = vault(kit, "owasp")
    assert fresh.create("pw") is True
    assert json.loads((kit.ctx.data_dir / "owasp.vault").read_text())["kdf"] == \
        {"name": "scrypt", "n": 2 ** 17, "r": 8, "p": 1}

    old_kdf = {"name": "scrypt", "n": 2 ** 10, "r": 8, "p": 1}
    monkeypatch.setattr(vault_mod, "KDF", old_kdf)       # a vault made by an older kit
    old = vault(kit, "older")
    assert old.create("pw") is True
    monkeypatch.undo()
    old.lock()
    assert old.unlock("pw") is True
    old.setProperty("data", [1])                          # saving keeps the file's parameters
    path = kit.ctx.data_dir / "older.vault"
    assert json.loads(path.read_text())["kdf"] == old_kdf


def test_vault_refuses_key_derivation_that_asks_for_too_much(kit):
    """The parameters are read before the password is checked: a file must not demand gigabytes."""
    v = vault(kit, "greedy")
    path = kit.ctx.data_dir / "greedy.vault"
    for n, r, p in ((2 ** 20, 8, 1), (2 ** 15, 32, 1), (2 ** 15, 8, 16), (1000, 8, 1)):
        path.write_text(json.dumps({"version": 1, "kdf": {"name": "scrypt", "n": n, "r": r, "p": p},
                                    "salt": "AAAA", "nonce": "AAAA", "ciphertext": "AAAA"}))
        started = time.monotonic()
        assert v.unlock("pw") is False
        assert v.property("error").startswith("the vault file is damaged"), v.property("error")
        assert time.monotonic() - started < 0.5


def test_vault_create_does_not_overwrite_what_another_object_made(kit, fast_kdf):
    root = make(kit, '''Item { property var a: a; property var b: b
        Vault { id: a; name: "shared" }
        Vault { id: b; name: "shared" } }''')
    a, b = root.property("a"), root.property("b")
    assert a.create("first") is True
    a.setProperty("data", [{"site": "bank"}])
    assert b.create("second") is False and b.property("error") == "a vault already exists"
    assert b.property("exists") is True
    b.lock()
    assert b.unlock("first") is True and js(b.property("data")) == [{"site": "bank"}]

    late = vault(kit, "appears-later")
    (kit.ctx.data_dir / "appears-later.vault").write_bytes((kit.ctx.data_dir / "shared.vault").read_bytes())
    assert late.property("exists") is False
    assert late.unlock("first") is True and late.property("exists") is True


def test_vault_objects_on_one_file_show_one_state(kit, fast_kdf):
    """A second Vault with the same name (say in a dialog) must not save from what it saw earlier."""
    root = make(kit, '''Item { property var a: a; property var b: b
        Vault { id: a; name: "twins" }
        Vault { id: b; name: "twins" } }''')
    a, b = root.property("a"), root.property("b")
    assert a.create("old") is True
    assert b.property("unlocked") is True and js(b.property("data")) == []
    a.setProperty("data", [{"n": "bank"}])
    b.setProperty("data", js(b.property("data")) + [{"n": "shop"}])
    assert js(a.property("data")) == [{"n": "bank"}, {"n": "shop"}]      # nothing a saved is lost

    assert b.changePassword("old", "new") is True
    a.setProperty("data", [{"n": "mail"}])               # saved under the new key, not the old one
    a.lock()
    assert b.property("unlocked") is False and js(b.property("data")) is None
    assert b.unlock("old") is False
    assert b.unlock("new") is True and js(b.property("data")) == [{"n": "mail"}]
    assert a.property("unlocked") is True


def test_vault_does_not_save_over_a_file_changed_elsewhere(kit, fast_kdf):
    from bombadil.appkit.native import vault as vault_mod

    v = vault(kit, "elsewhere")
    assert v.create("pw")
    v.setProperty("data", [1])
    path = kit.ctx.data_dir / "elsewhere.vault"
    doc = vault_mod.parse(path.read_bytes())             # another process saves [1, 2]
    path.write_bytes(vault_mod.seal(vault_mod.derive("pw", doc["salt"], doc["kdf"]), doc["salt"], doc["kdf"], [1, 2]))
    v.setProperty("data", [1, 3])
    assert v.property("error") == "the vault changed on disk; unlock it again"
    assert v.property("unlocked") is False
    assert v.unlock("pw") is True and js(v.property("data")) == [1, 2]


def test_vault_shows_only_what_it_saved(kit, fast_kdf, monkeypatch):
    """A save that fails must not look saved: the entry would be gone after the next lock."""
    from bombadil.appkit.native import vault as vault_mod

    v = vault(kit, "diskfull")
    assert v.create("pw")
    v.setProperty("data", [1])

    def fail(*_, **__):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(vault_mod, "atomic_write", fail)
        v.setProperty("data", [1, 2])
        assert v.property("error") == "cannot save: disk full" and js(v.property("data")) == [1]
    v.setProperty("data", [1, 3])
    assert v.property("error") == "" and js(v.property("data")) == [1, 3]


def test_vault_change_password_keeps_the_old_key_when_saving_fails(kit, fast_kdf, monkeypatch):
    from bombadil.appkit.native import vault as vault_mod

    v = vault(kit, "readonly")
    assert v.create("old")
    v.setProperty("data", [1])

    def fail(*_, **__):
        raise OSError("disk full")

    monkeypatch.setattr(vault_mod, "atomic_write", fail)
    assert v.changePassword("old", "new") is False and v.property("error") == "cannot save: disk full"
    monkeypatch.undo()
    v.setProperty("data", [2])                            # saved under the key that is on disk
    v.lock()
    assert v.unlock("new") is False
    assert v.unlock("old") is True and js(v.property("data")) == [2]


def test_vault_survives_hot_reload_until_locked(kit, fast_kdf):
    first = vault(kit, "reloaded")
    assert first.create("pw")
    first.setProperty("data", [{"k": "v"}])
    destroy(first.parent() or first)

    second = vault(kit, "reloaded")
    assert second.property("unlocked") is True and js(second.property("data")) == [{"k": "v"}]
    second.lock()
    third = vault(kit, "reloaded")
    assert third.property("unlocked") is False and third.property("exists") is True


def test_vault_auto_lock(kit, fast_kdf):
    v = vault(kit, "shortlived", "autoLock: 1")
    assert v.create("pw")
    assert wait_until(lambda: not v.property("unlocked"), 3000)
    assert vault(kit, "shortlived").property("unlocked") is False


def test_vault_objects_on_one_file_lock_after_the_shortest_auto_lock(kit, fast_kdf):
    """Using a Vault that never locks itself must not keep a stricter one on the same file open."""
    root = make(kit, '''Item { property var a: a; property var b: b
        Vault { id: a; name: "mixed"; autoLock: 1 }
        Vault { id: b; name: "mixed"; autoLock: 0 } }''')
    a, b = root.property("a"), root.property("b")
    assert a.create("pw") is True                        # b follows it, and so touched it last
    assert b.property("unlocked") is True
    assert wait_until(lambda: not a.property("unlocked"), 3000)
    assert b.property("unlocked") is False


def test_vault_auto_lock_counts_time_asleep(kit, fast_kdf, monkeypatch):
    """Qt timers and time.monotonic stop during suspend: a vault left open must lock on resume."""
    from bombadil.appkit.native import vault as vault_mod

    v = vault(kit, "slept", "autoLock: 300")
    assert v.create("pw")
    resumed = vault_mod._now() + 3600
    monkeypatch.setattr(vault_mod, "_now", lambda: resumed)   # an hour asleep, no Qt time passed
    assert wait_until(lambda: not v.property("unlocked"), 3000)
    assert vault(kit, "slept").property("unlocked") is False


def test_vault_in_check_works_in_memory_only(kit, checking, fast_kdf):
    v = vault(kit, "dryrun")
    assert v.create("pw") is True and v.property("unlocked") is True and v.property("exists") is True
    v.setProperty("data", [1, 2])
    assert js(v.property("data")) == [1, 2]
    v.lock()
    assert v.unlock("nope") is False and v.property("error") == "wrong password"
    assert v.unlock("pw") is True and js(v.property("data")) == [1, 2]
    assert v.changePassword("pw", "new") is True
    v.lock()
    assert v.unlock("new") is True and js(v.property("data")) == [1, 2]
    assert v.create("again") is False and v.property("error") == "a vault already exists"
    assert vault(kit, "dryrun").property("exists") is True
    assert not (kit.ctx.data_dir / "dryrun.vault").exists()


def test_app_data_dir_is_absolute_in_check_too(kit, checking):
    root = make(kit, "QtObject { property string d: App.dataDir }")
    assert root.property("d") == str(kit.ctx.data_dir) and Path(root.property("d")).is_absolute()


def test_vault_passwords():
    from bombadil.appkit.native.vault import SYMBOLS, generate_password, strength

    for length, symbols in ((20, True), (8, False), (4, True)):
        pw = generate_password(length, symbols)
        assert len(pw) == length
        assert any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
        assert any(c in SYMBOLS for c in pw) == symbols
    assert len({generate_password() for _ in range(20)}) == 20
    assert strength("") == 0 and strength("password") == 0
    assert strength("Tr0ub4dor&3") >= 2
    assert strength(generate_password()) == 4


def test_vault_strength_is_the_password_fields_score(kit):
    """A badge from Vault.strength and PasswordField's meter must never disagree."""
    from bombadil.appkit.native.vault import generate_password, strength

    pinned = {"password123": 0, "Summer2024!": 0, "sunshine2019": 0, "letmein!": 0, "qwerty123": 0,
              "aaaaaaaaaaaa": 1, "abcd1234": 1}
    assert {pw: strength(pw) for pw in pinned} == pinned
    field = make(kit, "import QtQuick.Controls\nPasswordField {}")
    samples = list(pinned) + [
        "", "abc", "hunter2", "Tr0ub4dor&3", "correct horse battery staple", "aaa111bbb222", "zxcvbnm,./",
        "P@ssw0rd!", "1990-05-17", "qwertyuiop", "Ünïcödé-pässwörd", "🙂🙂🙂🙂🙂🙂", "a🙂b🙂c🙂d", "x\ny\nz",
        "ROOT", "MyDog2019", "9f8e7d6c", "!!!!!!!!", "Summer", "ab12CD34ef",
    ] + [generate_password(n, sym) for n in (6, 8, 12, 20) for sym in (True, False)]
    for pw in samples:
        field.setProperty("text", pw)
        assert strength(pw) == field.property("strength"), pw


# -- Command --

def test_command_output_json_and_exit_code(kit):
    root = make(kit, """
Item {
    property var ip: ip
    property var bad: bad
    property var ls: ls
    property var got: []
    Command { id: ip; command: "echo '[{\\"a\\": 1}, null]'"; running: true }
    Command { id: bad; command: "echo out; echo oops >&2; exit 3"; onFinished: (code, out) => got = [code, out] }
    Command { id: ls; program: "echo"; args: ["~", "x"] }
}""")
    ip, bad, ls = root.property("ip"), root.property("bad"), root.property("ls")
    assert ip.property("running") is True
    assert wait_until(lambda: not ip.property("running"))
    assert ip.property("exitCode") == 0 and js(ip.property("json")) == [{"a": 1}, None]
    bad.run()
    assert wait_until(lambda: js(root.property("got")))
    assert js(root.property("got")) == [3, "out\n"]
    assert bad.property("stderr") == "oops\n" and js(bad.property("json")) is None
    ls.run(["extra arg"])
    assert wait_until(lambda: ls.property("exitCode") == 0)
    assert ls.property("stdout") == f"{Path.home()} x extra arg\n"
    assert js(ls.property("lines")) == [f"{Path.home()} x extra arg"]


def test_command_interval_and_kill(kit):
    root = make(kit, """
Item {
    property var poll: poll
    property var slow: slow
    Command { id: poll; property int runs: 0; command: "true"; interval: 100; onFinished: runs++ }
    Command { id: slow; command: "sleep 37.5 | cat" }
}""")
    poll, slow = root.property("poll"), root.property("slow")
    assert wait_until(lambda: poll.property("runs") >= 3, 3000)
    slow.run()
    assert slow.property("running") is True
    assert wait_until(lambda: _running("sleep 37.5"))
    slow.kill()                               # the shell and the pipeline it started
    assert slow.property("running") is False
    assert wait_until(lambda: not _running("sleep 37.5"), 2000)
    missing = make(kit, 'Item { property var c: c; Command { id: c; program: "no-such-program" } }').property("c")
    missing.run()
    assert wait_until(lambda: missing.property("exitCode") == 127)
    assert missing.property("stderr") == "no-such-program: command not found\n"


def test_command_poller_never_shows_half_an_answer(kit):
    root = make(kit, """
Item {
    property var seen: []
    property var live: []
    property var once: once
    Command { id: poll; command: "echo a; sleep 0.3; echo b"; interval: 5000
              onLinesChanged: seen = seen.concat([lines.join(",")]) }
    Command { id: once; command: "echo a; sleep 0.6; echo b"
              onStdoutChanged: live = live.concat([stdout]) }
}""")
    root.property("once").run()
    assert wait_until(lambda: js(root.property("seen")) and len(js(root.property("live"))) >= 2, 3000)
    assert set(js(root.property("seen"))) == {"a,b"}
    assert js(root.property("live"))[:2] == ["a\n", "a\nb\n"]     # not a poller: streams


def test_command_stdin_is_written_then_closed(kit):
    root = make(kit, """
Item {
    property var sorter: sorter
    property var counter: counter
    property var tr: tr
    property var repl: repl
    Command { id: sorter; command: "sort"; stdin: "b\\na\\nc\\n" }
    Command { id: counter; property int runs: 0; command: "wc -l"; interval: 100; onFinished: runs++ }
    Command { id: tr; program: "tr"; args: ["a-z", "A-Z"]; stdin: "hello" }
    Command { id: repl; command: "cat"; interactive: true }
}""")
    sorter, counter, tr, repl = (root.property(n) for n in ("sorter", "counter", "tr", "repl"))
    sorter.run()
    tr.run()
    repl.run()
    assert wait_until(lambda: sorter.property("stdout") == "a\nb\nc\n" and tr.property("stdout") == "HELLO")
    assert wait_until(lambda: counter.property("runs") >= 2, 3000)      # the poller keeps going
    assert counter.property("stdout").strip() == "0"
    repl.write("line 1\n")
    assert wait_until(lambda: repl.property("stdout") == "line 1\n")
    assert repl.property("running") is True                            # still open for more
    repl.kill()
    sorter.write("ignored")                                             # not interactive: stdin is closed


def test_command_interval_has_a_floor(kit):
    root = make(kit, """
Item {
    property var c: c
    property var p: p
    Command { id: c; command: "true"; interval: 1 }
    Processes { id: p; interval: 5 }
}""")
    c, p = root.property("c"), root.property("p")
    assert c.property("interval") == 100 and p.property("interval") == 100
    c.setProperty("interval", 0)
    p.setProperty("interval", 0)
    assert c.property("interval") == 0 and p.property("interval") == 0


@pytest.mark.parametrize("command", [
    "printf '[%s]'", "printf '[%s]'\n", "printf '[%s]';", "printf '[%s]' # comment",
    "printf '[%s]' ;  # c; d\n\n", "echo a; printf '[%s]'\n# a comment line\n",
])
def test_command_extra_args_after_any_ending(kit, command):
    c = make(kit, 'Item { property var c: c; Command { id: c } }').property("c")
    c.setProperty("command", command)
    c.run(["x y", "$HOME", "'q'"])
    assert wait_until(lambda: not c.property("running"))
    assert c.property("stdout").removeprefix("a\n") == "[x y][$HOME]['q']", c.property("stderr")


def test_command_extra_args_keep_quoted_hashes_and_escaped_semicolons():
    from bombadil.appkit.native.command import _with_args

    assert _with_args("printf '[%s]' '#x'") == "printf '[%s]' '#x' \"$@\""
    assert _with_args("find . -exec echo {} \\;") == "find . -exec echo {} \\; \"$@\""
    assert _with_args("echo ${#x}  ") == "echo ${#x} \"$@\""


def test_command_keeps_only_the_last_mib_of_output(kit):
    from bombadil.appkit.native.command import LIMIT, _Output

    c = make(kit, 'Item { property var c: c; Command { id: c; command: "seq 1 400000" } }').property("c")
    c.run()
    assert wait_until(lambda: c.property("exitCode") == 0)
    out, lines = c.property("stdout"), js(c.property("lines"))
    assert len(out) <= LIMIT and lines[-1] == "400000"
    assert int(lines[0]) == 400000 - len(lines) + 1                   # starts at a whole line

    o = _Output()
    o.add("é".encode()[:1])
    o.add("é".encode()[1:])                                             # split inside a character
    assert o.fresh and o.text() == "é" and not o.fresh


def _running(cmdline: str) -> bool:
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").strip() == cmdline.encode():
                return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
        except OSError:
            continue
    return False


# -- TextFile --

def test_textfile_read_save_and_watch(kit):
    f = make(kit, """
Item {
    property var file: tf
    property int external: 0
    TextFile { id: tf; path: "notes/today.md"; onChangedOnDisk: external++ }
}""")
    tf, path = f.property("file"), kit.ctx.data_dir / "notes" / "today.md"
    assert tf.property("exists") is False and tf.property("text") == ""
    assert tf.save("first") is True
    assert path.read_text() == "first" and tf.property("exists") is True
    spin(100)
    assert f.property("external") == 0          # our own write is not "changed on disk"

    gc.collect()                                # the shared watcher holds TextFiles weakly
    path.write_text("edited elsewhere")
    assert wait_until(lambda: tf.property("text") == "edited elsewhere", 2000)
    tmp = path.with_name(".swap")
    tmp.write_text("renamed over")
    os.replace(tmp, path)                       # how editors save
    assert wait_until(lambda: tf.property("text") == "renamed over", 2000)
    tmp.write_text("and again")
    os.replace(tmp, path)
    assert wait_until(lambda: tf.property("text") == "and again", 2000)
    assert f.property("external") >= 3

    tf.setProperty("text", "in memory")
    assert path.read_text() == "and again"
    assert tf.save() is True and path.read_text() == "in memory"
    assert tf.remove() is True and not path.exists() and tf.property("exists") is False


def _inotify_instances() -> int:
    n = 0
    for fd in os.listdir("/proc/self/fd"):
        with contextlib.suppress(OSError):
            n += os.readlink(f"/proc/self/fd/{fd}") == "anon_inode:inotify"
    return n


def test_textfiles_share_one_inotify_instance(kit):
    """A user may have 128 inotify instances in all; one per TextFile would run out."""
    (kit.ctx.data_dir / "many").mkdir(parents=True, exist_ok=True)
    make(kit, 'Item { TextFile { path: "many/warm-up.md" } }')
    before = _inotify_instances()
    root = make(kit, """
Item {
    property var last: last
    Repeater { model: 40; delegate: Item { TextFile { path: "many/note-" + index + ".md" } } }
    TextFile { id: last; path: "many/last.md" }
}""")
    assert _inotify_instances() == before
    (kit.ctx.data_dir / "many" / "last.md").write_text("seen")
    assert wait_until(lambda: root.property("last").property("text") == "seen", 2000)
    destroy(root)
    from bombadil.appkit.native import textfile

    assert not [p for p in textfile._watcher._qt.files() if "/many/note-" in p]


def test_textfile_paths_and_check(kit, checking):
    tf = make(kit, 'Item { property var f: f; TextFile { id: f; path: "~/.kit-test-nothing" } }').property("f")
    assert tf.property("exists") is False and tf.property("error") == ""
    tf.setProperty("path", "dry.txt")
    assert tf.save("never written") is True
    assert tf.property("text") == "never written"
    assert not (kit.ctx.data_dir / "dry.txt").exists()


# -- System --

def test_system_fields(kit):
    root = make(kit, "Item { property var s: System; property real cpu: System.cpu }")
    from bombadil.appkit.native import system

    sys_ = system._instance
    assert sys_ is not None
    spin(50)
    v = {name: js(sys_.property(name)) for name in (
        "cpu", "cpus", "cpuCount", "memory", "memoryUsage", "meminfo", "pressure", "load", "uptime",
        "processCount", "disks", "network", "battery", "temperature", "hostname", "kernel", "user",
        "interval")}
    assert 0 <= v["cpu"] <= 1 and len(v["cpus"]) == v["cpuCount"] >= 1
    mem = v["memory"]
    assert set(mem) == {"total", "used", "available", "free", "cached", "buffers", "shared", "swapTotal",
                        "swapUsed", "swapCached", "dirty"}
    assert 0 < mem["used"] < mem["total"] and mem["total"] == v["meminfo"]["MemTotal"]
    assert 0 < v["memoryUsage"] < 1 and v["meminfo"]["MemTotal"] > 100 * 1024 * 1024
    assert v["pressure"] is None or set(v["pressure"]) == {"cpu", "memory", "io"}
    assert len(v["load"]) == 3 and v["uptime"] > 0 and v["processCount"] > 1
    for d in v["disks"]:
        assert d["fs"] not in ("tmpfs", "proc", "sysfs", "overlay", "squashfs") and d["total"] > 0
        assert d["used"] + d["free"] <= d["total"]
    assert set(v["network"]) == {"rx", "tx"}
    assert set(v["battery"]) == {"present", "percent", "charging"}
    assert v["temperature"] is None or -50 < v["temperature"] < 150
    assert v["hostname"] and v["kernel"] == os.uname().release and v["user"] and v["interval"] == 1000
    assert root.property("cpu") == v["cpu"] or 0 <= root.property("cpu") <= 1


def test_system_disks_never_stat_network_mounts(monkeypatch):
    """statvfs on a mount whose server is gone blocks the UI thread of every app using System."""
    from bombadil.appkit.native import system

    mounts = "\n".join([
        "/dev/sda2 / ext4 rw 0 0",
        "/dev/sda1 /boot vfat rw 0 0",
        "/dev/sdb1 /mnt/win fuseblk rw 0 0",
        "server:/export /mnt/nfs nfs4 rw 0 0",
        "//nas/share /mnt/smb cifs rw 0 0",
        "me@host:/ /mnt/ssh fuse.sshfs rw 0 0",
        "tmpfs /tmp tmpfs rw 0 0",
    ])
    statted = []

    def statvfs(path):
        statted.append(path)
        return SimpleNamespace(f_blocks=100, f_frsize=4096, f_bavail=50, f_bfree=60)

    monkeypatch.setattr(system, "read", lambda path: mounts if path == "/proc/self/mounts" else "")
    monkeypatch.setattr(system.os, "statvfs", statvfs)
    assert [d["mount"] for d in system.disks()] == ["/", "/boot", "/mnt/win"]
    assert statted == ["/", "/boot", "/mnt/win"]


def test_system_first_read_in_a_binding_is_clean(kit):
    """The singleton is made inside the first binding that mentions it; that must not look like a loop."""
    from PySide6.QtCore import qInstallMessageHandler

    messages = []
    qInstallMessageHandler(lambda mode, context, text: messages.append(text))
    try:
        engine = kit_engine.make_engine(kit.ctx)     # a fresh engine makes a fresh System
        comp = QQmlComponent(engine)
        comp.setData(b"""import QtQuick
import Bombadil
Item {
    Text { text: System.cpu }
    Text { text: System.memory.used + " " + System.load[0] + " " + System.disks.length }
    Text { text: System.hostname }
}""", QUrl.fromLocalFile(str(kit.ctx.dir / "binding.qml")))
        root = comp.create()
        assert root is not None, [e.toString() for e in comp.errors()]
        spin(1100)
    finally:
        qInstallMessageHandler(None)
    assert not [m for m in messages if "Binding loop" in m or "Error" in m], messages
    root.deleteLater()


def test_system_gives_qml_real_arrays_and_nulls(kit):
    root = make(kit, """
Item {
    property bool batteryNull: System.battery.present || System.battery.percent === null
    property bool tempOk: System.temperature === null || typeof System.temperature === "number"
    property bool arrays: Array.isArray(System.cpus) && Array.isArray(System.disks) && Array.isArray(System.load)
                          && System.cpus.length === System.cpuCount
    property bool objects: Object.keys(System.memory).length === 11 && System.meminfo.MemTotal > 0
}""")
    for name in ("batteryNull", "tempOk", "arrays", "objects"):
        assert root.property(name) is True, name


def test_lists_from_native_types_are_real_arrays(kit, fast_kdf):
    root = make(kit, """
Item {
    property bool procs: Array.isArray(p.list) && p.list.length > 0 && p.details(p.list[0].pid).pid === p.list[0].pid
    property bool lines: Array.isArray(c.lines) && c.lines.length === 2
    property bool json: Array.isArray(c2.json) && c2.json[1].b === null
    property bool vault: v.create("pw") && Array.isArray(v.data) && Array.isArray(v.data.concat([1]))
    Processes { id: p; interval: 0 }
    Command { id: c; command: "printf 'a\\nb\\n'"; running: true }
    Command { id: c2; command: "echo '[1, {\\"b\\": null}]'"; running: true }
    Vault { id: v; name: "arrays" }
}""")
    assert wait_until(lambda: all(root.property(n) for n in ("procs", "lines", "json")), 3000), \
        [(n, root.property(n)) for n in ("procs", "lines", "json")]
    assert root.property("vault") is True


# -- Processes --

def test_processes_list_sort_filter_details(kit):
    root = make(kit, """
Item {
    property var all: all
    property var mine: mine
    Processes { id: all; sortBy: "memory"; limit: 5 }
    Processes { id: mine; filter: "PYTEST"; sortBy: "pid"; descending: false; interval: 0 }
}""")
    procs, mine = root.property("all"), root.property("mine")
    assert wait_until(lambda: procs.property("count") > 0)
    rows = js(procs.property("list"))
    assert len(rows) == 5 and procs.property("count") >= 5
    assert set(rows[0]) == {"pid", "ppid", "name", "command", "user", "state", "cpu", "memory",
                            "memoryPercent", "threads", "started"}
    assert [r["memory"] for r in rows] == sorted((r["memory"] for r in rows), reverse=True)
    assert all(r["cpu"] >= 0 and r["started"] > 1e9 for r in rows)

    assert wait_until(lambda: mine.property("count") > 0)
    pids = [r["pid"] for r in js(mine.property("list"))]
    assert os.getpid() in pids and pids == sorted(pids)
    procs.setProperty("sortBy", "name")
    procs.setProperty("descending", False)
    names = [r["name"].lower() for r in js(procs.property("list"))]
    assert names == sorted(names)

    d = js(procs.details(os.getpid()))
    assert d["pid"] == os.getpid() and d["rss"] > 0 and d["threads"] >= 1 and d["fds"] > 0
    assert d["cwd"] == os.getcwd() and d["exe"] and d["started"] > 1e9
    assert d["pss"] is None or 0 < d["pss"] <= d["rss"] * 2
    assert d["uss"] is None or d["uss"] > 0
    assert {"swap", "shared", "oomScore", "user", "command", "name"} <= set(d)
    assert js(procs.details(2 ** 22 + 12345)) is None
    assert procs.kill(2 ** 22 + 12345) is False


def test_processes_list_is_there_in_on_completed(kit):
    root = make(kit, """
Item {
    property int seen: -1
    property int count: -1
    Processes { id: p; sortBy: "pid"; descending: false; limit: 3; interval: 0 }
    Component.onCompleted: { seen = p.list.length; count = p.count }
}""")
    assert root.property("seen") == 3 and root.property("count") > 3


def test_processes_kill_does_nothing_in_check(kit, checking):
    import subprocess

    child = subprocess.Popen(["sleep", "10"])
    try:
        procs = make(kit, "Item { property var p: p; Processes { id: p; interval: 0 } }").property("p")
        assert procs.kill(child.pid, "KILL") is False
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_processes_refresh_is_fast():
    from bombadil.appkit.native.processes import Processes

    procs = Processes()
    procs.refresh()
    started = time.perf_counter()
    procs.refresh()
    assert time.perf_counter() - started < 0.5


# -- Highlighter --

def test_highlighter_colors_every_language(kit):
    from bombadil.appkit.native.highlighter import LANGUAGES

    samples = {
        "python": 'def f(x):\n    """doc\n    more"""\n    return "s" + 1  # c',
        "markdown": "# Title\n\n**bold** and `code`\n\n```\nfenced\n```\n",
        "json": '{"a": [1, true, null], "b": "x"}',
        "qml": "Item {\n    id: root\n    /* long\n comment */\n    width: 10 // c\n}",
        "javascript": "const x = `t ${1}`; function g() { return null }",
        "shell": 'echo "$HOME" # c\nfor i in 1 2; do ls -la; done',
        "toml": '[s]\nkey = "v"\nn = 1',
        "ini": "[s]\nkey=value\n; c",
        "plain": "nothing 123 # here",
    }
    assert set(samples) == set(LANGUAGES) | {"plain"}
    root = make(kit, """
Item {
    property alias text: te.text
    property var doc: te.textDocument
    property var hl: hl
    TextEdit { id: te; width: 400; height: 300 }
    Highlighter { id: hl; textDocument: te.textDocument }
}""")
    hl = root.property("hl")
    for language, text in samples.items():
        hl.setProperty("language", language)
        root.setProperty("text", text + " 🙂 ünï")
        spin(10)
        doc = root.property("doc").textDocument()
        formats = [r for b in _blocks(doc) for r in b.layout().formats()]
        assert bool(formats) == (language != "plain"), language
    hl.setProperty("language", "notes.md")
    assert hl.property("language") == "markdown"
    hl.setProperty("language", "cobol")
    assert hl.property("language") == "plain"


def _blocks(doc):
    b = doc.begin()
    while b.isValid():
        yield b
        b = b.next()


# -- Clipboard, KitFiles, Agent --

def test_clipboard_copy_and_clear(kit):
    root = make(kit, "Item { property string seen: Clipboard.text }")
    clip = QGuiApplication.clipboard()
    make(kit, 'Item { Component.onCompleted: Clipboard.copy("hello") }')
    assert clip.text() == "hello" and root.property("seen") == "hello"
    make(kit, 'Item { Component.onCompleted: Clipboard.copy("secret", 0.2) }')
    assert clip.text() == "secret"
    assert wait_until(lambda: clip.text() == "", 2000)
    make(kit, 'Item { Component.onCompleted: Clipboard.copy("short", 0.1) }')
    clip.setText("user copied something else")
    spin(1300)                             # past the first 1 s check
    assert clip.text() == "user copied something else"


def test_clipboard_clear_counts_time_asleep(kit, monkeypatch):
    from bombadil.appkit.native import clipboard as clipboard_mod

    clip = QGuiApplication.clipboard()
    make(kit, 'Item { Component.onCompleted: Clipboard.copy("slept on", 30) }')
    assert clip.text() == "slept on"
    resumed = clipboard_mod._now() + 3600
    monkeypatch.setattr(clipboard_mod, "_now", lambda: resumed)
    assert wait_until(lambda: clip.text() == "", 3000)


def test_clipboard_does_nothing_in_check(kit, checking):
    QGuiApplication.clipboard().setText("before")
    make(kit, 'Item { Component.onCompleted: Clipboard.copy("during check") }')
    assert QGuiApplication.clipboard().text() == "before"


def test_kitfiles(kit, tmp_path):
    from bombadil.appkit.native.files import atomic_write

    target = tmp_path / "a" / "b.txt"
    atomic_write(target, "one")
    target.chmod(0o640)
    atomic_write(target, "two")
    assert target.read_text() == "two" and stat.S_IMODE(target.stat().st_mode) == 0o640
    assert [p.name for p in target.parent.iterdir()] == ["b.txt"]
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    atomic_write(link, "three")
    assert link.is_symlink() and target.read_text() == "three"

    root = make(kit, """
QtObject {
    property bool wrote: KitFiles.writeText("kitfiles/x.txt", "hi")
    property string back: KitFiles.readText("kitfiles/x.txt")
    property string missing: KitFiles.readText("kitfiles/none.txt")
    property string resolved: KitFiles.resolve("kitfiles/x.txt")
}""")
    assert root.property("wrote") is True and root.property("back") == "hi" and root.property("missing") == ""
    assert root.property("resolved") == str(kit.ctx.data_dir / "kitfiles" / "x.txt")


def test_agent_reply_only_for_this_apps_turns(kit, monkeypatch, tmp_path):
    from bombadil.appkit.native.agent import Agent

    a = Agent(kit.ctx)
    replies = []
    a.replied.connect(replies.append)
    a.handle({"type": "status", "busy": False, "provider": "claude"})
    assert a.property("provider") == "claude"
    a._waiting = 2                               # two prompts sent
    for msg in (
        {"type": "event", "kind": "turn_start", "turn": 6, "prompt": "someone else"},
        {"type": "queued", "turn": 7},
        {"type": "event", "kind": "text", "turn": 6, "text": "not for us"},
        {"type": "event", "kind": "turn_end", "turn": 6},
        {"type": "event", "kind": "turn_start", "turn": 7, "prompt": f"[from app {kit.ctx.name}] hi"},
        {"type": "event", "kind": "text", "turn": 7, "text": "Hello"},
        {"type": "event", "kind": "text", "turn": 7, "text": "there"},
        {"type": "event", "kind": "result", "turn": 7, "ok": True, "text": "Hello\n\nthere"},
        {"type": "event", "kind": "turn_end", "turn": 7},
    ):
        a.handle(msg)
    assert replies == ["Hello\n\nthere"] and a.property("reply") == "Hello\n\nthere"
    for msg in (                                 # agentd fails a turn before it starts
        {"type": "queued", "turn": 8},
        {"type": "event", "kind": "error", "turn": 8, "text": "claude is not installed yet"},
        {"type": "event", "kind": "turn_end", "turn": 8, "seconds": 0},
    ):
        a.handle(msg)
    assert replies[-1] == a.property("reply") == "Error: claude is not installed yet"


@contextlib.contextmanager
def _agentd(monkeypatch, tmp_path, provider):
    """A real agentd on a socket in tmp_path, served from a thread."""
    from bombadil import agentd

    sock = tmp_path / "agentd.sock"
    monkeypatch.setenv("BOMBADIL_SOCKET", str(sock))
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "state"))
    daemon = agentd.AgentD(provider, agentd._NoSnapshots(), socket_path=sock)
    loop = asyncio.new_event_loop()
    task = loop.create_task(daemon.serve())

    def serve():
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        rest = asyncio.all_tasks(loop)
        for t in rest:
            t.cancel()
        loop.run_until_complete(asyncio.gather(*rest, return_exceptions=True))
        loop.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield
    finally:
        loop.call_soon_threadsafe(task.cancel)
        thread.join(2)


def test_agent_talks_to_agentd(kit, monkeypatch, tmp_path):
    from bombadil import providers
    from bombadil.appkit.native.agent import Agent

    with _agentd(monkeypatch, tmp_path, providers.Fake("x")):
        a = Agent(kit.ctx)
        try:
            replies = []
            a.replied.connect(replies.append)
            a.ask("what is running?")               # queued until the connection is up
            a.start()
            assert wait_until(lambda: a.property("connected"), 5000)
            assert a.property("provider") == "fake"
            assert wait_until(lambda: replies, 5000)
            assert replies[0] == f"echo: [from app {kit.ctx.name}] what is running?"
            assert a.property("reply") == replies[0]
        finally:
            a._retry.stop()
            a._socket.abort()


def test_agent_reports_a_provider_that_is_not_installed(kit, monkeypatch, tmp_path):
    """agentd sends error and turn_end with no turn_start then; the app still gets its answer."""
    from bombadil import providers
    from bombadil.appkit.native.agent import Agent

    class Missing(providers.Fake):
        installed = False

    with _agentd(monkeypatch, tmp_path, Missing("x")):
        a = Agent(kit.ctx)
        try:
            replies = []
            a.replied.connect(replies.append)
            a.start()
            assert wait_until(lambda: a.property("connected"), 5000)
            a.ask("hello")
            assert wait_until(lambda: replies, 5000)
            assert replies[0].startswith("Error: ") and "not installed" in replies[0]
            assert a.property("reply") == replies[0]
        finally:
            a._retry.stop()
            a._socket.abort()


def test_agent_stays_offline_in_check(kit, checking, monkeypatch, tmp_path):
    from bombadil.appkit.native.agent import Agent

    monkeypatch.setenv("BOMBADIL_SOCKET", str(tmp_path / "none.sock"))
    a = Agent(kit.ctx)
    a.start()
    a.ask("hello")
    assert a._retry.isActive() is False and a._outbox == [] and a.property("connected") is False
