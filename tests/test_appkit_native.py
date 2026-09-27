"""The native types behind `import Bombadil`, driven from Python and from small QML snippets.

Native types register once per process and their singletons belong to one engine, so the
whole module shares one offscreen app, one AppContext and one QML engine.
"""

import asyncio
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


def test_store_never_writes_in_check(kit, checking):
    store = make(kit, STORE % "checked").property("store")
    store.setProperty("filter", "changed")
    spin(450)
    QMetaObject.invokeMethod(store, "save")
    assert not (kit.ctx.data_dir / "checked.json").exists()


# -- Vault --

def vault(kit, name: str, extra: str = ""):
    return make(kit, f'Item {{ property var vault: v; Vault {{ id: v; name: "{name}"; {extra} }} }}').property("vault")


def test_vault_create_unlock_and_change_password(kit):
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


def test_vault_survives_hot_reload_until_locked(kit):
    first = vault(kit, "reloaded")
    assert first.create("pw")
    first.setProperty("data", [{"k": "v"}])
    destroy(first.parent() or first)

    second = vault(kit, "reloaded")
    assert second.property("unlocked") is True and js(second.property("data")) == [{"k": "v"}]
    second.lock()
    third = vault(kit, "reloaded")
    assert third.property("unlocked") is False and third.property("exists") is True


def test_vault_auto_lock(kit):
    v = vault(kit, "shortlived", "autoLock: 1")
    assert v.create("pw")
    assert wait_until(lambda: not v.property("unlocked"), 3000)
    assert vault(kit, "shortlived").property("unlocked") is False


def test_vault_in_check_works_in_memory_only(kit, checking):
    v = vault(kit, "dryrun")
    assert v.create("pw") is True and v.property("unlocked") is True
    v.setProperty("data", [1, 2])
    assert js(v.property("data")) == [1, 2]
    assert not (kit.ctx.data_dir / "dryrun.vault").exists()


def test_vault_passwords():
    from bombadil.appkit.native.vault import SYMBOLS, generate_password, strength

    for length, symbols in ((20, True), (8, False), (4, True)):
        pw = generate_password(length, symbols)
        assert len(pw) == length
        assert any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
        assert any(c in SYMBOLS for c in pw) == symbols
    assert len({generate_password() for _ in range(20)}) == 20
    assert strength("") == 0 and strength("password") == 0 and strength("aaaaaaaaaaaa") == 0
    assert strength("Tr0ub4dor&3") >= 2
    assert strength(generate_password()) == 4


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


def test_lists_from_native_types_are_real_arrays(kit):
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
    spin(250)
    assert clip.text() == "user copied something else"


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


def test_agent_reply_only_for_this_apps_prompts(kit, monkeypatch, tmp_path):
    from bombadil.appkit.native.agent import Agent

    a = Agent(kit.ctx)
    replies = []
    a.replied.connect(replies.append)
    a.handle({"type": "status", "busy": False, "provider": "claude"})
    assert a.property("provider") == "claude"
    a._sent.append(f"[from app {kit.ctx.name}] hi")
    for msg in (
        {"type": "event", "kind": "turn_start", "prompt": "someone else"},
        {"type": "event", "kind": "text", "text": "not for us"},
        {"type": "event", "kind": "turn_end"},
        {"type": "event", "kind": "turn_start", "prompt": f"[from app {kit.ctx.name}] hi"},
        {"type": "event", "kind": "text", "text": "Hello"},
        {"type": "event", "kind": "text", "text": "there"},
        {"type": "event", "kind": "result", "ok": True, "text": "Hello\n\nthere"},
        {"type": "event", "kind": "turn_end"},
    ):
        a.handle(msg)
    assert replies == ["Hello\n\nthere"] and a.property("reply") == "Hello\n\nthere"


def test_agent_talks_to_agentd(kit, monkeypatch, tmp_path):
    from bombadil import agentd, providers
    from bombadil.appkit.native.agent import Agent

    sock = tmp_path / "agentd.sock"
    monkeypatch.setenv("BOMBADIL_SOCKET", str(sock))
    monkeypatch.setenv("BOMBADIL_STATE", str(tmp_path / "state"))
    daemon = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots(), socket_path=sock)
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
    a = None
    try:
        a = Agent(kit.ctx)
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
        if a is not None:
            a._retry.stop()
            a._socket.abort()
        loop.call_soon_threadsafe(task.cancel)
        thread.join(2)


def test_agent_stays_offline_in_check(kit, checking, monkeypatch, tmp_path):
    from bombadil.appkit.native.agent import Agent

    monkeypatch.setenv("BOMBADIL_SOCKET", str(tmp_path / "none.sock"))
    a = Agent(kit.ctx)
    a.start()
    a.ask("hello")
    assert a._retry.isActive() is False and a._outbox == [] and a.property("connected") is False
