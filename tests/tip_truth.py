"""The real sentences a tip's scene may show, made by the code that says them (tests/test_tip_truth.py).

A scene is a storyboard, so it could show a line the machine never says. This module calls the real functions
(narrate, launcher, desk, jobs, vitals, rest, sysmap, agentd and the shell's own QML) with fixture inputs and
writes down what they said, by the place a scene shows it: a line above the pill, a command, a card's title,
a row, a strip. The truth test then holds every `template` string of every scene to those pools, whole, so a
changed real line fails the test, and a scene that shows words no code says fails it too.

Nothing here knows a scene: it only says what the code says. The scene's example names are in EXAMPLE.
"""

import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from bombadil import (
    agentd,
    apps,
    desk,
    jobs,
    launcher,
    narrate,
    paths,
    providers,
    rest,
    snapshots,
    sysmap,
    vitals,
)

ROOT = Path(__file__).resolve().parents[1]

# The names the scenes use for the {app}, {project} and {ai} of a tip's words.
EXAMPLE = {"app": "Tickets", "project": "shop", "ai": "Claude"}

# The roles a template string can have in a scene: where on the screen it is.
ROLES = ("line", "command", "button", "wait", "title", "why", "label", "value", "picture", "box", "bar", "strip",
         "window", "lock")


class Said:
    """What the code says, by role: {role: {text: where it comes from}}."""

    def __init__(self):
        self.pool = defaultdict(dict)
        self.risk = {}                 # (line, command) -> the risk narrate gave that step
        self.changes = set()           # the step lines that change something (narrate gives them a `done`)

    def add(self, role, text, where):
        assert role in ROLES, role
        self.pool[role].setdefault(text, where)

    def has(self, role, text):
        return text in self.pool[role]

    def where(self, role, text):
        return self.pool[role].get(text)


def step(said, name, args, where=None):
    """A tool's step line, as the narrator says it while the tool runs."""
    s = narrate.tool_step(name, args)
    said.add("line", s.text, where or f"narrate.tool_step({name})")
    if s.done:
        said.changes.add(s.text)
    return s


def shell(said, command, description=None):
    """A shell command's step: its line, its command and its edge."""
    s = narrate.shell_step(command, description)
    where = f"narrate.shell_step({command!r})"
    said.add("line", s.text, where)
    if s.command:
        said.add("command", s.command, where)
    said.risk[(s.text, s.command)] = s.risk
    if s.done:
        said.changes.add(s.text)
    return s


# -- the sentences made with no screen: narrate, launcher, desk, jobs, vitals, rest, sysmap, the brain --

def narrated(said, home):
    os_tool = "mcp__bombadil-os__"
    lines = "\n".join(["line"] * 140) + "\n"
    step(said, os_tool + "create_app", {"title": "Tickets", "qml": lines})
    step(said, os_tool + "create_app", {"title": "Passwords", "qml": "\n".join(["line"] * 210) + "\n"})
    # "Changing" is what the line says once the app is there to change.
    folder = paths.apps_dir() / apps.slug("Tickets")
    folder.mkdir(parents=True)
    (folder / "main.qml").write_text("Item {}\n")
    step(said, os_tool + "create_app", {"title": "Tickets", "qml": "\n".join(["line"] * 180) + "\n"})
    step(said, os_tool + "job", {"op": "start", "seconds": 1500})
    step(said, os_tool + "job", {"op": "start", "title": "Shop tests", "command": "make test"})
    step(said, os_tool + "show_card", {"title": "How a VPN works"})
    step(said, os_tool + "show_card", {"title": "How the shop modules fit"})
    for command in ("sudo pacman -S --noconfirm docker", "sudo pacman -S --noconfirm htop",
                    "sudo pacman -S --noconfirm rustup", "uname -r"):
        shell(said, command)
    shell(said, "rustup default stable", "Install stable Rust")
    # What a stopped turn says, a finished one and the counts of one still going.
    stopped = narrate.Narrator()
    stopped.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S --noconfirm docker"}})
    said.add("line", stopped.stopped_line(), "narrate.Narrator.stopped_line")
    done = narrate.Narrator()
    done.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S --noconfirm htop"}})
    said.add("line", done.summary(), "narrate.Narrator.summary")
    going = narrate.Narrator()
    going.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S --noconfirm rustup"}})
    said.touched = going.touched_text()
    # The reason "why" answers: the sentence the agent said before the step, as the narrator keeps it.
    asked = narrate.Narrator()
    asked.on_event({"kind": "message_start"})
    asked.on_event({"kind": "text", "text": "Docker comes first in this setup."})
    asked.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S --noconfirm docker"}})
    said.reason = asked.why_text()
    said.reason_said = "Docker comes first in this setup."
    jobs_end = {"title": "shop tests", "kind": "job", "state": "done", "started": 100.0, "ended": 280.0}
    said.add("line", jobs.ending(jobs_end)[0], "jobs.ending")
    said.add("label", jobs.timer_title(1500), "jobs.timer_title")


def launched(said, home, monkeypatch):
    """The launcher's words for what it does: its lines while it works, what it answers, and where words go."""
    routes = {}
    for words in ("How am I connected?", "Where did my disk go?", "How's the machine?", "pause Claude",
                  "resume claude", "undo", "desk", "widgets", "brain", "where did this come from?"):
        routes[words] = launcher.match(words, [SimpleNamespace(name="tickets", title="Tickets")], busy=True)
    said.routes = routes
    for action in (routes["How am I connected?"], routes["Where did my disk go?"], routes["undo"]):
        said.add("line", launcher.Launcher.doing(action), f"launcher.Launcher.doing({action.kind})")
    # "Opened Tickets.": the drawer's answer is not the sentence; the launcher's is.
    placement = SimpleNamespace(show=lambda name: f"{name} shown", hide=lambda name: "", close=lambda name: "")
    monkeypatch.setattr(launcher, "_placement", lambda: placement)
    lx = launcher.Launcher(snaps=_Snaps(), desk=desk.Desk())
    ok, text = lx.run(launcher.Action("app", "tickets", "open", "Tickets"))
    assert ok
    said.add("line", text, "launcher.Launcher._app")
    ok, text = lx.run(routes["How's the machine?"])
    assert ok
    said.add("line", text, "launcher.Launcher._widget (desk.ASKABLE)")
    said.add("line", desk.ASKABLE["machine"], "desk.ASKABLE")
    # The undo sentence names what was undone as the restore point's description does.
    ok, text = lx.run(routes["undo"])
    assert ok
    said.add("line", text, "launcher.Launcher._undo")
    # The desk answers the word "desk" by folding and then unfolding.
    d = desk.Desk()
    said.add("line", d.apply("toggle")[1], "desk.Desk.apply(toggle)")
    said.add("line", d.apply("toggle")[1], "desk.Desk.apply(toggle)")
    # The word "widgets" opens the Widgets card and says so; a press on its Keep part says what it did.
    ok, text = lx.run(routes["widgets"])
    assert ok
    said.add("line", text, "launcher.Launcher._widgets (desk.Desk.apply)")
    said.opened = text
    ok, text = desk.Desk().apply("set", widget="machine", state="keep")
    assert ok
    said.add("line", text, "desk.Desk.apply(set keep), the press on the Widgets card")
    said.pressed = text
    for wid, widget in desk.WIDGETS.items():
        said.add("label", widget.title, f"desk.WIDGETS[{wid}]")


class _Snaps(snapshots.Snapshots):
    """Restore points as the launcher reads them: "turn:<n>: <what was asked>"."""

    def __init__(self):
        self.rolled = []

    @property
    def available(self):
        return True

    def list(self, limit=20):
        return [snapshots.Snapshot(1, "turn:1: install htop")]

    def rollback(self, number):
        self.rolled.append(number)
        return True


def rested(said):
    gone = rest.Rest("claude", "hand")
    words = rest.words(gone, "Claude")
    said.add("line", words["line"], "rest.words")
    said.add("wait", words["wait"], "rest.words")
    said.add("line", rest.kept(gone, "Claude", False), "rest.kept")
    said.add("line", rest.back("Claude", 1), "rest.back")


def vital(said):
    reading = vitals.Reading(cpu=0.12, memory=vitals.Memory(42, 100), disk=vitals.Disk(61, 100))
    said.add("why", vitals.Vitals._why([], reading), "vitals.Vitals._why")
    said.machine = {}
    for row in (vitals.Vitals._memory_row(reading, []), vitals.Vitals._disk_row(reading, []),
                vitals.Vitals._cpu_row(reading)):
        said.add("label", row["title"], "vitals row")
        said.machine[row["title"]] = round(row["meter"] * 100)


NETWORK_WIFI = " :Neighbour:31\n"      # nobody is on Wi-Fi: the machine is on a cable


def drawn(said):
    """The pictures of the machine, from the commands' own output (as tests/test_sysmap.py feeds them)."""
    from test_sysmap import DEFAULTS, DEVICES, ROUTE_CABLE, fake

    outputs = {"ip -j route get": ROUTE_CABLE, "ip -j route show": DEFAULTS, "nmcli -t -f GENERAL.DEVICE": DEVICES,
               "nmcli -t -f IN-USE": NETWORK_WIFI, "nmcli -t -g CONNECTIVITY": "full\n"}
    net = sysmap.capture_network(fake(outputs), probe=lambda host: 38, read=lambda p: "nameserver 10.0.0.1\n",
                                 wireless=lambda dev: dev.startswith("wl"), provider="claude")
    said.network = net
    said.add("picture", net["card"]["title"], "sysmap.capture_network")
    for node in net["card"]["nodes"]:
        said.add("box", node["label"], "sysmap.capture_network")
    lsblk = json.dumps({"blockdevices": [
        {"name": "nvme0n1", "size": 512110190592, "type": "disk", "fstype": None, "label": None, "model": "Disk",
         "rm": False, "mountpoints": [None], "children": [
             {"name": "nvme0n1p2", "size": 200_000_000_000, "type": "part", "fstype": "ext4", "mountpoints": ["/"]},
             {"name": "nvme0n1p3", "size": 300_000_000_000, "type": "part", "fstype": "ext4",
              "mountpoints": ["/home"]}]},
        {"name": "sda", "size": 32_000_000_000, "type": "disk", "fstype": None, "label": None, "model": "USB",
         "rm": True, "mountpoints": [None], "children": [
             {"name": "sda1", "size": 32_000_000_000, "type": "part", "fstype": "exfat", "mountpoints": ["/data"]}]},
    ]})
    findmnt = json.dumps({"filesystems": [
        {"target": "/", "source": "/dev/nvme0n1p2", "fstype": "ext4", "size": 200_000_000_000,
         "used": 124_000_000_000, "children": [
             {"target": "/home", "source": "/dev/nvme0n1p3", "fstype": "ext4", "size": 300_000_000_000,
              "used": 273_000_000_000},
             {"target": "/data", "source": "/dev/sda1", "fstype": "exfat", "size": 32_000_000_000,
              "used": 3_840_000_000}]}]})
    disks = sysmap.capture_disks(fake({"lsblk": lsblk, "findmnt": findmnt}))
    said.disks = disks
    said.add("picture", disks["card"]["title"], "sysmap.capture_disks")
    # A bar is a filesystem: its mount point, how full it says it is, amber from where the picture marks it.
    said.bars = {}
    for node in disks["card"]["nodes"]:
        mount = node["sub"].split(" · ")[0]
        full = re.search(r"(\d+)% full", node["sub"])
        if mount.startswith("/") and full:
            said.add("bar", mount, "sysmap.capture_disks")
            said.bars[mount] = (int(full.group(1)), node.get("state") in ("warn", "bad"))


def agent_says(said, monkeypatch):
    """agentd's own sentences: Stopping, and what it says after a picture word."""
    async def run():
        d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
        seen = []

        async def event(kind, **kw):
            seen.append((kind, kw))

        async def show(card, *a, **k):
            return None
        d.event, d._show = event, show
        stop = narrate.Narrator()
        stop.on_event({"kind": "tool", "name": "Bash", "input": {"command": "sudo pacman -S --noconfirm docker"}})
        d.current, d.narrator = 1, stop
        await d.stop()
        for kind, kw in seen:
            if kind == "status":
                said.add("line", kw["text"], "agentd.AgentD.stop")
        for words, card in (("How am I connected?", said.network), ("Where did my disk go?", said.disks)):
            seen.clear()
            action = said.routes[words]
            monkeypatch.setattr(agentd.sysmap, "capture", lambda *a, _c=card, **k: {"card": {**_c["card"], "text": "x"}})
            await d.picture(action, words)
            for kind, kw in seen:
                if kind == "local" and kw.get("text"):
                    said.add("line", kw["text"], "agentd.AgentD.picture")
    asyncio.run(run())


def brain_says(said, brain, monkeypatch):
    """What the Brain answers to "where did this come from?", and what "brain" opens, through the launcher."""
    ing = brain.ingest
    tue = brain_module().TUE
    ing.visit("https://www.rent-portal.com/lease/renewal?id=7#top", "Lease renewal 2026", tue - 300)
    part = brain.file("Downloads/lease-2026.pdf.crdownload", b"%PDF-1.7\n", tue)
    pdf = brain.home / "Downloads/lease-2026.pdf"
    part.rename(pdf)
    brain.you("rename", pdf, tue, chain=brain_module().CHROME, old=str(part))
    ing.download(str(pdf), "https://files.rent-portal.com/lease.pdf", "https://www.rent-portal.com/lease/renewal?id=7",
                 tue)
    brain.stamp(pdf, tue, opened=True)
    focus = brain.focus(pdf)
    ask = {"why": lambda **kw: brain.why(pdf), "show": lambda **kw: focus["thing"]}
    monkeypatch.setattr(launcher.this, "resolve", lambda *a, **k: str(pdf))
    monkeypatch.setattr(launcher.brain_client, "request", lambda op, **kw: ask[op](**kw))
    lx = launcher.Launcher(snaps=_Snaps(), desk=desk.Desk())
    monkeypatch.setattr(lx, "open_brain", lambda: None)
    ok, text = lx._whyhere(launcher.Action("whyhere"))
    assert ok
    said.add("line", text, "brain focus.why through launcher._whyhere")
    ok, text = lx._brain(launcher.Action("brain"))
    assert ok
    said.add("line", text, "launcher.Launcher._brain")
    said.add("window", focus["thing"]["title"], "the brain's title for the file")


def brain_module():
    import test_brain_focus
    return test_brain_focus


def bang_ends(said, bar, monkeypatch):
    """A typed `!command` through a real agentd, and what the real pill draws when it ends: its turn_end, the line, and
    the buttons under it. Undo and Details come only after a turn that changed something (shell/StatusLine.qml)."""
    from test_agentd import _ask, _read_until, _start

    # a turn looks at the machine before it starts (for its receipt); this is not about the machine it runs on
    monkeypatch.setattr(agentd.sysmap, "snapshot", lambda *a, **k: {})

    async def run():
        d = agentd.AgentD(providers.Fake("x"), agentd._NoSnapshots())
        server, r, w = await _start(d)
        await _ask(w, "!uname -r")
        msgs = await _read_until(r, "turn_end")
        w.close()
        server.cancel()
        return [m for m in msgs if m.get("type") == "event"]
    events = asyncio.run(run())
    for ev in events:
        bar.send(**{k: v for k, v in ev.items() if k != "type"})
    said.bang = SimpleNamespace(
        end=next(e for e in events if e["kind"] == "turn_end"), line=bar.text(),
        buttons=[label for name, label in (("undoButton", "Undo"), ("detailsButton", "Details")) if bar.shown(name)])


def shell_says(said):
    """A "!" command's result is the last lines of its output, whole."""
    p = providers.Shell()
    out = list(p.parse("6.17.2-arch1-1\n"))
    p.returncode = 0
    result = next(e for e in p.finish() if e["kind"] == "result")
    said.shell = (out, result)


# -- the sentences the shell's own QML says --

def desk_says(said, desk_qml, touched):
    """The desk's cards and strips, driven by agentd's messages as the shell is (tests/test_desk_qml.py)."""
    from test_desk_qml import T0, job

    d = desk_qml
    title = jobs.timer_title(1500)
    d.jobs(job("e5f6", title, kind="timer", deadline=T0 + 1500))
    for seconds in (0, 1):
        d.clock(seconds)
        row = d.watch["rows"][0]
        said.add("label", row["title"], "the Watching card")
        said.add("value", row["sub"], "the Watching card")
    said.add("strip", d.call("_stripOf", "watching")["text"], "DeskState._stripOf")
    d.jobs(job("a1b2", "Shop tests", kind="job"))
    d.clock(40)
    row = d.watch["rows"][0]
    said.add("label", row["title"], "the Watching card")
    said.add("value", row["sub"], "the Watching card")
    # The same job once it has ended: the row reads "done in 3 min" and the card says "finished".
    d.jobs(job("a1b2", "Shop tests", kind="job", state="done", ended=T0 + 180))
    d.clock(181)
    row = d.watch["rows"][0]
    said.add("label", row["title"], "the Watching card, done")
    said.add("value", row["sub"], "the Watching card, done")
    said.add("why", d.watch["why"], "the Watching card, done")
    # The Now card: the ask for its title, the step counter for its why line, strips for a folded one.
    d.jobs()
    prompt = "Install rust and set it up"
    plan = [("Install rustup", "Installing rustup", "in_progress"),
            ("Install stable Rust", None, "pending"), ("Check cargo", "Checking cargo", "pending")]
    d.turn(1, prompt, steps=plan)
    said.add("title", d.now["title"], "the Now card")
    said.add("why", d.now["why"], "the Now card")
    d.plan(1, [("Install rustup", "Installing rustup", "completed"),
               ("Install stable Rust", "Installing stable Rust", "in_progress"),
               ("Check cargo", "Checking cargo", "pending")])
    d.send(kind="status", turn=1, text="Installing rustup", source="step", risk="system",
           command="sudo pacman -S --noconfirm rustup", touched_text=touched)
    said.add("why", d.now["why"], "the Now card, after a package")
    d.end(1, seconds=41, changed=True)
    said.add("why", d.now["why"], "the Now card, done")
    d.send(kind="turn_end", turn=1, seconds=1, changed=False, stopped=False)
    d.turn(2, prompt, steps=[("Install rustup", None, "completed"), ("Get stable Rust", "Getting stable Rust",
                                                                      "in_progress")])
    said.add("why", d.now["why"], "the Now card")
    said.add("strip", d.prop("nowStripText"), "the Now strip")


def widgets_says(said, card):
    """The Widgets card the word "widgets" opens, drawn by the shell's own QML (tests/test_widgets_card_qml.py) from
    the rows the real desk sends: its title, the widgets it lists in its order, the words of the three parts of
    each control, and which part is raised for the desk as it ships (nothing kept) and for one that keeps Machine."""
    from PySide6 import QtGui
    from qml_theme import THEME

    rows = desk.Desk().snapshot()["widgets"]
    parts = ("keep", "needed", "off")
    raised = QtGui.QColor(THEME["raised"]).name(QtGui.QColor.HexArgb)
    said.widgets = SimpleNamespace(order=[], part={}, raised={})
    for name, kept in (("shipped", []), ("kept", ["machine"])):
        card.open(kept=kept, widgets=rows)
        said.add("window", card.item("widgetsTitle").property("text"), "the Widgets card (shell/WidgetsCard.qml)")
        said.widgets.raised[name] = {}
        for r in rows:
            row = card.row(r["id"])
            title = card.items("widgetsName", root=row)[0].property("text")
            said.add("label", title, "the Widgets card")
            if name == "shipped":
                said.widgets.order.append(title)
            for state in parts:
                item = card.part(r["id"], state)
                label = card.text_of(item).property("text")
                said.add("value", label, "the Widgets card, a part of a control")
                said.widgets.part[state] = label
                if item.property("color").name(QtGui.QColor.HexArgb) == raised:
                    said.widgets.raised[name][title] = label


def pill_says(said, bar):
    """What the pill puts up the moment Enter is pressed, before agentd answers."""
    assert bar.call("submit", "install ffmpeg") is True
    said.add("line", bar.text(), "PillState")


def entries_complete(bar, app_list):
    """The rest of a word Tab would fill in, with the apps the launcher lists."""
    bar.send(type="entries", entries=launcher.entries(app_list, None))
    return lambda typed: bar.call("completion", typed)


def buttons(said):
    """The labels on the line's buttons (shell/StatusLine.qml)."""
    qml = (ROOT / "shell" / "StatusLine.qml").read_text()
    for label in re.findall(r'label: "([^"]+)"', qml):
        said.add("button", label, "shell/StatusLine.qml")
