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
start("quickshell", ["quickshell", "-p", str(REPO / "shell" / "shell.qml")])

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

# 7b. a click on a box in a picture that names a file opens it in the same drawer, in the viewer
# (`bombadil view`: the image has no pager), and one Esc puts it away.
m = mark()
send({"type": "open", "kind": "path", "value": "/etc/os-release"})
done = wait(ev("local", action="open", phase="done"), 15, m)
check("a clicked file is opened and the line says so", done is not None and done.get("ok") is True, done and done.get("text"))
check("the viewer opens in the drawer", until(drawer_open, 5) and "bombadil view --file /etc/os-release" in run("pgrep", "-af", "bombadil").stdout,
      run("pgrep", "-af", "bombadil view").stdout[:200])
time.sleep(1)
shot("21-open-file")
check("the viewer has the keyboard", until(lambda: focused_app() == "bombadil-details", 5), focused_app())
key("Escape")
check("one Esc puts the viewer away", until(lambda: not drawer_open()))

# 22. a picture: show_card streams into the bar while the model writes it (the kit's Diagram, drawn by
# the real Quickshell), Esc puts it away, and a picture word draws with no model at all.
def glass_pixels(x, y, h):
    """How many pixels of a one-pixel-wide strip are the bar's glass (#e61a1d21 over a dark screen)."""
    r = subprocess.run(["grim", "-g", f"{x},{y} 1x{h}", "-t", "ppm", "-"], env=env, capture_output=True)
    data = r.stdout
    try:
        head, rest = data.split(b"\n255\n", 1)
    except ValueError:
        return -1
    return sum(1 for i in range(0, len(rest) - 2, 3)
               if 21 <= rest[i] <= 30 and 24 <= rest[i + 1] <= 34 and 28 <= rest[i + 2] <= 38)


m = mark()
summon()
typ("explain the vpn")
key("Return")
half = wait(ev("card", card=lambda c: bool(c and c.get("partial"))), 60, m)
check("a picture streams into the bar while the model writes it", half is not None, half and half["card"].get("id"))
full = wait(ev("card", card=lambda c: bool(c and not c.get("partial") and not c.get("gone"))), 60, m)
check("the finished picture takes the streamed one's id",
      full is not None and half is not None and full["card"]["id"] == half["card"]["id"], full and full["card"].get("id"))
end = wait(ev("turn_end"), 60, m)
check("the turn that drew it ends plainly", end is not None and not end.get("stopped"), end and end.get("summary"))
time.sleep(1.0)
shot("22-picture")
with_card = glass_pixels(200, 300, 420)
check("Quickshell draws the picture above the line", with_card > 80, with_card)
qs_log = (OUT / "quickshell.log").read_text() if (OUT / "quickshell.log").exists() else ""
bad = [ln for ln in qs_log.splitlines() if re.search(r"CardHost|Diagram|Theme\.qml|ReferenceError|TypeError", ln)]
check("the bar loads the kit's Diagram without QML errors", not bad, bad[:3])
summon()
key("Escape")
time.sleep(0.8)
shot("22-picture-away")
without = glass_pixels(200, 300, 420)
check("Esc puts the picture away", without < with_card // 2, f"{with_card} -> {without}")

before = api_requests()
m = mark()
summon()
typ("how am i connected")
key("Return")
pic = wait(ev("local", action="picture", phase="done"), 20, m)
check("a picture word is answered with no model and no turn", pic is not None and api_requests() == before
      and wait(ev("turn_start"), 1, m) is None, pic and pic.get("text"))
time.sleep(0.8)
shot("23-picture-word")


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
# a picture with both rails up stays between them, as wide as the narrowed pill (328..951 at 1280 wide)
m = mark()
summon()
typ("how am i connected")
key("Return")
wait(ev("local", action="picture", phase="done"), 20, m)
time.sleep(1.0)
shot("26-desk-picture-between-rails")
inside = glass_pixels(400, 480, 200)
beside = glass_pixels(322, 480, 200)
check("a picture with both rails up sits between them", inside > 60 and beside < 10, f"inside {inside}, beside {beside}")
summon()
key("Escape")
time.sleep(0.8)
inject({"type": "dev", "sessions": [], "attention": [], "front": "", "line": ""})
inject({"type": "jobs", "jobs": []})
time.sleep(0.8)
st = desk_state()
check("both cards leave when nothing is counting or waiting", st["faces"]["watching"] == "hidden" and st["faces"]["needs"] == "hidden", st.get("faces"))

(OUT / "results.json").write_text(json.dumps(results, indent=2))
for p in procs[::-1]:
    p.terminate()
fails = [c for c in results["checks"] if not c["ok"]]
print(f"DONE pass={len(results['checks']) - len(fails)} fail={len(fails)}", flush=True)
