"""Drive the real bar, agentd and Claude Code CLI in headless sway, taking screenshots.

Runs inside the test container (tests/desktop/run.sh) as an ordinary user with passwordless sudo.
Typing goes through wtype, Super through `bombadil pill`; each check prints PASS or FAIL.
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path("/repo")
OUT = Path("/out")
E2E = Path("/e2e")
OUT.mkdir(exist_ok=True)
xdg = Path(f"/tmp/xdg-{os.getuid()}")
xdg.mkdir(mode=0o700, exist_ok=True)
env = dict(
    os.environ,
    XDG_RUNTIME_DIR=str(xdg),
    WLR_BACKENDS="headless",
    WLR_RENDERER="pixman",
    WLR_LIBINPUT_NO_DEVICES="1",
    WLR_HEADLESS_OUTPUTS="1",
    BOMBADIL_PROVIDER="claude",
    BOMBADIL_SHARE=str(REPO / "share"),
    QT_QUICK_BACKEND="software",
    LANG="C.UTF-8",
    PATH=f"{E2E}/bin:{REPO}/bin:/usr/local/bin:/usr/bin:/bin",
)
env.pop("DISPLAY", None)
results = {"checks": [], "timings": {}}
procs = []


def check(name, ok, detail=""):
    results["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def start(name, argv, **kw):
    log = open(OUT / f"{name}.log", "w")
    p = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT, **kw)
    procs.append(p)
    return p


def run(*argv, **kw):
    return subprocess.run(argv, env=env, capture_output=True, text=True, **kw)


def shot(name):
    r = run("grim", str(OUT / f"{name}.png"))
    if r.returncode:
        print("grim failed:", r.stderr, flush=True)


def stone_pixels(name, rows=(737, 787), colour="#5fb36b", tol=14):
    """How many pixels in the pill's rows are the stone's green (the pill draws no other green)."""
    from PySide6.QtGui import QColor, QImage
    want, img, n = QColor(colour), QImage(str(OUT / f"{name}.png")), 0
    for y in range(*rows):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            n += abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < tol * 3
    return n


# -- start everything --
start("fake-api", [sys.executable, str(E2E / "fake_api.py"), "18555", str(OUT / "api-requests.jsonl")])
(xdg / "sway.conf").write_text(
    "output HEADLESS-1 resolution 1280x800 bg #33404d solid_color\n"
    'default_border none\nfor_window [app_id=".*"] floating enable\n'
)
start("sway", ["sway", "-c", str(xdg / "sway.conf")])
for _ in range(100):
    socks = [p for p in xdg.glob("wayland-*") if not p.name.endswith(".lock")]
    if socks:
        break
    time.sleep(0.1)
env["WAYLAND_DISPLAY"] = socks[0].name
env["SWAYSOCK"] = next(iter(xdg.glob("sway-ipc.*")), Path("")).as_posix()
start("agentd", [str(REPO / "bin" / "agentd")])
sock_path = xdg / "bombadil" / "agentd.sock"
for _ in range(100):
    if sock_path.exists():
        break
    time.sleep(0.1)
start("quickshell", [str(REPO / "bin" / "bombadil-shell")])

events, lock = [], threading.Lock()


def listen():
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_path))
    with open(OUT / "events.jsonl", "w") as log:
        for raw in s.makefile("r"):
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            m["_t"] = time.monotonic()
            with lock:
                events.append(m)
            log.write(json.dumps(m) + "\n")
            log.flush()


threading.Thread(target=listen, daemon=True).start()


def wait(pred, timeout=30, since=0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with lock:
            for m in events[since:]:
                if pred(m):
                    return m
        time.sleep(0.03)
    return None


def mark():
    with lock:
        return len(events)


def ev(kind, **match):
    return lambda m: (
        m.get("type") == "event"
        and m.get("kind") == kind
        and all((v(m.get(k)) if callable(v) else m.get(k) == v) for k, v in match.items())
    )


def send(msg):
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_path))
    s.sendall((json.dumps(msg) + "\n").encode())
    time.sleep(0.2)
    s.close()


def summon():
    run("bombadil", "pill")
    time.sleep(0.5)


# Each wtype run uploads its own keymap; a key sent right after it is dropped, so wait first.
def typ(text):
    run("wtype", "-s", "200", text)
    time.sleep(0.25)


def key(k):
    run("wtype", "-s", "200", "-k", k)


def api_requests():
    try:
        return len((OUT / "api-requests.jsonl").read_text().splitlines())
    except OSError:
        return 0


def sleeps():
    r = run("ps", "-eo", "pid,user,args")
    return [line for line in r.stdout.splitlines() if "sleep 120" in line and "ps -eo" not in line]


time.sleep(4)
shot("00-resting")
check("bar connects to agentd", "Ask anything" and wait(lambda m: m.get("type") == "status", 5) is not None)
# The first check of the provider runs the real CLI (a cold start can take seconds): typing before it says
# ready would be typing into a pill that is still checking.
check("agentd finds the provider ready", wait(lambda m: m.get("type") == "setup" and m.get("state") == "ready", 60) is not None)
check("the stone rests green in the pill", stone_pixels("00-resting") > 100, stone_pixels("00-resting"))

# 1. install ffmpeg: On it at once, then the step in plain words with its exact command.
summon()
typ("install ffmpeg")
shot("01-typed")
n = mark()
t0 = time.monotonic() + 0.2
key("Return")
time.sleep(0.15)
shot("02-on-it-150ms")
st = wait(ev("turn_start"), 5, n)
results["timings"]["enter_to_turn_start_s"] = round(st["_t"] - t0, 3) if st else None
check(
    "turn_start arrives within 200 ms of Enter",
    st and st["_t"] - t0 < 0.2,
    results["timings"]["enter_to_turn_start_s"],
)
inst = wait(ev("status", text=lambda t: (t or "").startswith("Installing ffmpeg")), 40, n)
time.sleep(0.4)
shot("03-installing-ffmpeg")
check("the stone is not green while a turn runs", stone_pixels("03-installing-ffmpeg") < 20, stone_pixels("03-installing-ffmpeg"))
check(
    "step reads Installing ffmpeg, marked system, with the exact command",
    inst and inst.get("risk") == "system" and "pacman -S" in (inst.get("command") or ""),
    inst,
)
seen = [m.get("text") for m in events[n:] if m.get("kind") == "status"]
results["ffmpeg_lines"] = seen
end = wait(ev("turn_end"), 40, n)
time.sleep(0.6)
shot("04-ffmpeg-done")
check("ffmpeg turn ends changed, with a summary", end and end.get("changed"), end)

# 2. make me a password manager: the line counts lines as the QML streams.
summon()
typ("make me a password manager")
n = mark()
key("Return")
b = wait(
    ev(
        "status",
        text=lambda t: (
            bool(re.match(r"Building Passwords, (\d+) lines", t or ""))
            and int(re.findall(r"\d+", t)[0]) >= 12
        ),
    ),
    60,
    n,
)
shot("05-building-passwords")
counts = [m["text"] for m in events[n:] if m.get("kind") == "status" and "Building" in (m.get("text") or "")]
results["building_lines"] = counts
check("app building line counts up while streaming", b and len(counts) >= 5, counts[:3] + counts[-2:])
end = wait(ev("turn_end"), 60, n)
time.sleep(3)
shot("06-passwords-made")
tree = run("swaymsg", "-t", "get_tree").stdout
check("Passwords window opened", "bombadil-app-passwords" in tree or "Passwords" in tree)

# 3. the launcher: quit, then reopen by name, without the model.
before = api_requests()
summon()
typ("quit passwords")
n = mark()
key("Return")
q = wait(ev("local", phase="done"), 10, n)
time.sleep(0.3)
shot("07-quit-passwords")
check("quit passwords answered locally", q and q.get("ok"), q)
summon()
typ("pass")
time.sleep(0.3)
shot("08-tab-ghost")
key("Tab")
time.sleep(0.3)
shot("09-exact-hint")
n = mark()
t0 = time.monotonic() + 0.2
key("Return")
o = wait(ev("local", phase="done"), 10, n)
results["timings"]["launcher_open_s"] = round(o["_t"] - t0, 3) if o else None
time.sleep(2.5)
shot("10-passwords-opened")
tree = run("swaymsg", "-t", "get_tree").stdout
check(
    "passwords opened by name with no model call",
    o and o.get("ok") and api_requests() == before,
    (o, api_requests() - before),
)
check("launcher answered in under 0.5 s", o and o["_t"] - t0 < 0.5, results["timings"]["launcher_open_s"])

# 4. set up docker, queue a prompt behind it, then Esc stops the whole turn (root sleep included).
summon()
typ("set up docker")
n = mark()
key("Return")
dk = wait(ev("status", text=lambda t: (t or "").startswith("Installing docker")), 40, n)
docker_turn = dk and dk.get("turn")
time.sleep(1.0)
shot("11-docker-system-step")
check("docker step is amber with its command", dk and dk.get("risk") == "system", dk)
check("root sleep running under sudo", any("root" in s for s in sleeps()), sleeps())
summon()
typ("tell me a joke")
key("Return")
qd = wait(ev("queued"), 5, n)
time.sleep(0.4)
shot("12-queued-chip")
check("second prompt waits as a chip", qd is not None, qd)
summon()
n2 = mark()
t0 = time.monotonic() + 0.2
key("Escape")
time.sleep(0.25)
shot("13-stopping")
end = wait(ev("turn_end", turn=docker_turn), 20, n2)
results["timings"]["stop_s"] = round(end["_t"] - t0, 3) if end else None
time.sleep(0.2)
shot("14-stopped")
check("Esc stopped the turn and said what it stopped", end and end.get("stopped"), end and end.get("line"))
time.sleep(0.5)
check("no sleep 120 left, root one included", not sleeps(), sleeps())
jk = wait(ev("turn_end", turn=lambda t: t != docker_turn), 40, n2)
time.sleep(0.5)
shot("15-queued-joke-ran")
check("queued prompt ran after the stop", jk is not None, jk and jk.get("summary"))
key("Escape")
time.sleep(0.5)
shot("16-esc-dismissed")

# 5. "!" runs a shell command without the model; a turn that changed nothing fades.
summon()
before = api_requests()
typ("!uname -sr")
n = mark()
key("Return")
bang = wait(ev("turn_end"), 20, n)
time.sleep(0.4)
shot("17-bang-command")
check("! command ran without the model", bang and api_requests() == before, bang)
time.sleep(13)
shot("18-faded-after-12s")

# 6. undo and details.
summon()
typ("undo")
n = mark()
key("Return")
u = wait(ev("local", action="undo", phase="done"), 10, n)
time.sleep(0.4)
shot("19-undo")
check("undo answers plainly without restore points here", u is not None, u and u.get("text"))
send({"type": "details", "turn": docker_turn})
time.sleep(2.5)
shot("20-details")
tree = run("swaymsg", "-t", "get_tree").stdout
check("details drawer opened", "bombadil-details" in tree)


def drawer_open():
    return "bombadil-details" in run("swaymsg", "-t", "get_tree").stdout


def focused_app():
    todo = [json.loads(run("swaymsg", "-t", "get_tree").stdout or "{}")]
    while todo:
        node = todo.pop()
        if node.get("focused"):
            return node.get("app_id") or node.get("name")
        todo += node.get("nodes", []) + node.get("floating_nodes", [])
    return None


def until(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


# 7. every way out of the drawer: Esc in it, Details again, Esc in the pill. (Clicks on the line
# and its Details button are covered offscreen in tests/test_pill_qml.py: headless sway has no pointer.)
check("details drawer has the keyboard", until(lambda: focused_app() == "bombadil-details", 5), focused_app())
key("Escape")
check("Esc in the drawer closes it", until(lambda: not drawer_open()))
send({"type": "details", "turn": docker_turn})
until(drawer_open)
time.sleep(1)
send({"type": "details", "turn": docker_turn})
check("Details again closes it", until(lambda: not drawer_open()))
send({"type": "details", "turn": docker_turn})
until(drawer_open)
time.sleep(1)
summon()
key("Escape")
check("Esc in the pill closes it", until(lambda: not drawer_open()))
shot("21-details-closed")

# 8. the desk: Now on the left rail while a two-step plan runs, folded by a window over it, and the
# `desk` word. (Hyprland's own window list is not here: the desk is told where the windows are.)
def desk_ipc(*args):
    return run("quickshell", "ipc", "-p", str(REPO / "shell" / "shell.qml"), "call", "desk", *args)


def desk_state():
    try:
        return json.loads(desk_ipc("state").stdout)
    except ValueError:
        return {}


summon()
typ("show me the route")
n = mark()
key("Return")
n0 = n
pl = wait(ev("plan", steps=lambda s: bool(s) and len(s) == 2), 40, n)
check("the plan arrives as two steps with the first running", pl and [x["status"] for x in pl["steps"]] in (
    ["pending", "pending"], ["in_progress", "pending"]), pl and pl.get("steps"))
st = None
for _ in range(100):
    st = desk_state()
    if st.get("faces", {}).get("now") == "full":
        break
    time.sleep(0.2)
time.sleep(0.6)
shot("22-desk-now")
check("Now is on the desk while the plan runs", st and st["present"]["now"] and st["faces"]["now"] == "full", st and st.get("faces"))
check("Now is one of the left rail's slots, 300 wide", st and st["slots"]["now"]["x"] == 16 and st["slots"]["now"]["w"] == 300, st and st.get("slots"))
check("Now lists both steps", st and [x["label"] for x in st["now"]["model"]["steps"]][-1] == "Write it down", st and st["now"]["model"]["steps"])
desk_ipc("cover", json.dumps({"windows": [{"x": 0, "y": 560, "w": 500, "h": 160}]}))
time.sleep(0.4)
st = desk_state()
shot("23-desk-covered")
check("a window over Now folds it to a strip", st.get("faces", {}).get("now") == "strip" and st.get("mode") == "shared", st.get("faces"))
check("the pill narrows to 360 while a window shares the stage", st.get("pillWidth") == 360, st.get("pillWidth"))
desk_ipc("cover", '{"windows": []}')
time.sleep(1.0)
st = desk_state()
check("Now comes back once the window is gone", st.get("faces", {}).get("now") == "full" and st.get("mode") == "open", st.get("faces"))
summon()
typ("desk")
n = mark()
key("Return")
d = wait(ev("local", action="desk", phase="done"), 10, n)
time.sleep(0.6)
st = desk_state()
shot("25-desk-folded")
check("the desk word folds every card", d and d.get("ok") and st.get("folded") and st["faces"]["now"] == "strip", (d, st.get("faces")))
summon()
typ("desk")
n = mark()
key("Return")
wait(ev("local", action="desk", phase="done"), 10, n)
time.sleep(0.6)
check("and again brings them back", not desk_state().get("folded"))
end = wait(ev("turn_end"), 60, n0)
time.sleep(0.5)
shot("24-desk-done")
summon()
typ("hide needs you")
n = mark()
key("Return")
h = wait(ev("local", phase="done"), 10, n)
check("Needs you cannot be hidden", h and not h.get("ok") and "cannot be hidden" in (h.get("text") or ""), h and h.get("text"))
key("Escape")

# 9. Watching and Needs you: the jobs table and the coding sessions agentd sends, injected as the
# shell would get them (real jobs run under systemd in the VM smoke; no systemd here).
def inject(msg):
    desk_ipc("inject", json.dumps(msg))


now_s = time.time()
inject({"type": "jobs", "jobs": [
    {"id": "a1b2c3", "title": "Ubuntu 26.04 ISO", "kind": "job", "state": "running", "started": now_s - 240,
     "deadline": None, "ended": None, "pct": 43.0, "last": "12 MB/s", "unit": "bombadil-job-a1b2c3"},
    {"id": "d4e5f6", "title": "Timer, 10 min", "kind": "timer", "state": "running", "started": now_s - 200,
     "deadline": now_s + 400, "ended": None, "pct": None, "last": "", "unit": "bombadil-timer-d4e5f6"},
    {"id": "0a0b0c", "title": "Build the image", "kind": "watch", "state": "failed", "started": now_s - 60,
     "deadline": None, "ended": now_s - 5, "pct": None, "last": "pacman: could not resolve host",
     "unit": "bombadil-job-0a0b0c"}]})
inject({"type": "dev", "sessions": [
    {"key": "rev", "project": "bombadil", "projectTitle": "Bombadil", "role": "reviewer", "tool": "claude",
     "toolTitle": "Claude Code", "title": "reviewer", "state": "asked", "alive": True, "unseen": False,
     "yours": False, "copy": False, "since": now_s - 30, "last": "apply the migration to the local database?",
     "lines": []},
    {"key": "bld", "project": "bombadil", "projectTitle": "Bombadil", "role": "builder", "tool": "codex",
     "toolTitle": "Codex", "title": "builder", "state": "asked", "alive": True, "unseen": False, "yours": False,
     "copy": False, "since": now_s - 20, "last": "install qemu-full?", "lines": []}],
    "attention": ["rev", "bld"], "front": "", "line": ""})
time.sleep(1.5)
st = desk_state()
shot("26-desk-watching-needs")
w, nd = st.get("watching", {}), st.get("needs", {})
check("Watching shows the meter, the timer and the failed job", [r["key"] for r in w.get("rows", [])] == ["a1b2c3", "d4e5f6", "0a0b0c"], w.get("rows"))
check("the meter row carries 43% and a stop", w["rows"][0]["kind"] == "meter" and abs(w["rows"][0]["meter"] - 0.43) < 1e-6 and w["rows"][0]["remove"], w["rows"][0])
check("the failed row says why in one line, with Why?", w["rows"][2]["button"] == "Why?" and "resolve host" in w["rows"][2]["sub"], w["rows"][2])
check("Needs you lists both waiting sessions", [r["title"] for r in nd.get("rows", [])] == ["reviewer on Bombadil", "builder on Bombadil"], nd.get("rows"))
check("both cards are in full on their own rails", st["faces"]["watching"] == "full" and st["faces"]["needs"] == "full"
      and st["slots"]["watching"]["side"] == "left" and st["slots"]["needs"]["side"] == "right", st.get("faces"))
inject({"type": "dev", "sessions": [], "attention": [], "front": "", "line": ""})
inject({"type": "jobs", "jobs": []})
time.sleep(0.8)
st = desk_state()
check("both cards leave when nothing is counting or waiting", st["faces"]["watching"] == "hidden" and st["faces"]["needs"] == "hidden", st.get("faces"))

# 10. The loop: one thing asked three times on three days is counted by agentd (nothing model-made), offered
# in the "noticed" chip beside the pill, and a press on the offer makes the word. The asks are written to
# the ledger as earlier days' turns; the next row agentd writes (a word that fails) makes it look again.
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests" / "fixtures" / "loop"))
import golden_corpus as gc  # noqa: E402
from bombadil import apps as bapps  # noqa: E402
from bombadil import paths as bpaths  # noqa: E402


def loop_ipc(*args):
    return run("quickshell", "ipc", "-p", str(REPO / "shell" / "shell.qml"), "call", "loop", *args)


def loop_state():
    try:
        return json.loads(loop_ipc("state").stdout)
    except ValueError:
        return {}


check("nothing is noticed before anything is asked three times", loop_state().get("count") == 0, loop_state())
bapps.create("Passwords", "import QtQuick\nItem {}\n")
logs = bpaths.state_dir() / "turns"
logs.mkdir(parents=True, exist_ok=True)
with bpaths.turns_log().open("a") as f:
    for i, (days_ago, said) in enumerate([(3, "show me my passwords"), (2, "open my passwords"),
                                          (1, "can you open passwords please")]):
        ask = {"id": f"seed{i}", "text": said, "seconds": 7, "changed": False, "t": time.time() - days_ago * 86400,
               "events": [["mcp__bombadil-os__open_app", {"name": "passwords"}]]}
        (logs / f"{ask['id']}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in gc.tool_events(ask)))
        f.write(json.dumps(gc.row_of(ask, logs)) + "\n")
summon()
typ("hide needs you")
key("Return")
time.sleep(0.3)
key("Escape")
st = {}
for _ in range(100):
    st = loop_state()
    if st.get("count", 0) >= 1 and st.get("chip"):
        break
    time.sleep(0.3)
time.sleep(0.4)
shot("27-noticed-chip")
check("three asks on three days put one offer in the chip", st.get("count") == 1 and st.get("chip")
      and [r["kind"] for r in st.get("rows", [])] == ["offer"], st)
asked = {"show me my passwords", "open my passwords", "can you open passwords please"}
row = (st.get("rows") or [{}])[0]
check("the offer is in his own words, says how often and what would happen",
      row.get("title") in asked and row.get("meta") == "3 times on 3 days"
      and "my passwords" in row.get("what", "") and row.get("primary") == "Make the word", row)
loop_ipc("chip", "HEADLESS-1")
time.sleep(0.8)
st2 = loop_state()
shot("28-noticed-card")
check("a click on the chip keeps the card up", st2.get("card") and st2.get("kept"), st2)
# (The line "Made ... Undo" goes to the bar that was tapped, not to this listener: the screenshot shows
# it, and the ledger row below is what it undoes.)
loop_ipc("press", row.get("id", ""))
word_file = ""
for _ in range(60):
    word_file = bpaths.words_file().read_text() if bpaths.words_file().exists() else ""
    if 'phrase = "my passwords"' in word_file:
        break
    time.sleep(0.25)
time.sleep(0.8)
shot("29-noticed-made")
check("pressing the offer makes the word", 'phrase = "my passwords"' in word_file
      and 'opens = { kind = "app", name = "passwords" }' in word_file, word_file)
for _ in range(50):
    st3 = loop_state()
    if st3.get("count") == 0:
        break
    time.sleep(0.2)
check("the chip goes once nothing waits", st3.get("count") == 0 and not st3.get("chip"), st3)
improve = [json.loads(x) for x in bpaths.turns_log().read_text().splitlines() if '"improve"' in x]
check("the change is in the ledger as his own tap, with an undo that takes the word away again",
      improve and improve[-1].get("title") == "Made “my passwords” open Passwords."
      and improve[-1].get("undo", {}).get("op") == "remove_word", improve[-1:])

# Quickshell logs a QML error as a warning and carries on (a colour left undefined draws white), so
# none of the checks above would notice one.
qml_errors = [ln for ln in re.sub(r"\x1b\[[0-9;]*m", "", (OUT / "quickshell.log").read_text()).splitlines()
              if re.search(r"\.qml\[|\.qml:\d+|Unable to assign|is not defined|TypeError|ERROR", ln)]
check("the shell logged no QML errors", not qml_errors, "; ".join(qml_errors[:3]))

(OUT / "results.json").write_text(json.dumps(results, indent=2))
for p in procs[::-1]:
    p.terminate()
fails = [c for c in results["checks"] if not c["ok"]]
print(f"DONE pass={len(results['checks']) - len(fails)} fail={len(fails)}", flush=True)
