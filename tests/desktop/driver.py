"""Drive the real bar, agentd and Claude Code CLI in headless sway, taking screenshots.

Runs inside the test container (tests/desktop/run.sh) as an ordinary user with passwordless sudo.
Typing goes through wtype, Super through `bombadil pill`; each check prints PASS or FAIL.
"""

import http.client
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
    BOMBADIL_VITALS="0",    # the Machine card is injected below; the real machine must not answer over it
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


def colour_pixels(name, colour, box, tol=14):
    """How many pixels of a screenshot's box (x0, y0, x1, y1) are this colour."""
    from PySide6.QtGui import QColor, QImage
    want, img, n = QColor(colour), QImage(str(OUT / f"{name}.png")), 0
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = img.pixelColor(x, y)
            n += abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < tol * 3
    return n


def pixel(name, x, y):
    """(r, g, b) of one pixel of a screenshot."""
    from PySide6.QtGui import QImage
    c = QImage(str(OUT / f"{name}.png")).pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def mean_pixel(name, x, y, half=3):
    """(r, g, b) averaged over the (2*half)-pixel square around x, y: a dithered picture's true colour."""
    from PySide6.QtGui import QImage
    img, tot, n = QImage(str(OUT / f"{name}.png")), [0, 0, 0], 0
    for yy in range(y - half, y + half):
        for xx in range(x - half, x + half):
            c = img.pixelColor(xx, yy)
            tot = [tot[0] + c.red(), tot[1] + c.green(), tot[2] + c.blue()]
            n += 1
    return tuple(round(v / n, 1) for v in tot)


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
qs_proc = start("quickshell", [str(REPO / "bin" / "bombadil-shell")])

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


def api_log():
    try:
        return [json.loads(ln) for ln in (OUT / "api-requests.jsonl").read_text().splitlines()]
    except (OSError, ValueError):
        return []


def api_control(limit):
    """The scripted API's plan: {"reset": epoch seconds} refuses the haiku ask as a used-up plan does, None lets it through."""
    c = http.client.HTTPConnection("127.0.0.1", 18555, timeout=5)
    c.request("POST", "/__control", json.dumps({"limit": limit}), {"content-type": "application/json"})
    c.getresponse().read()
    c.close()


def sleeps():
    r = run("ps", "-eo", "pid,user,args")
    return [line for line in r.stdout.splitlines() if "sleep 120" in line and "ps -eo" not in line]


time.sleep(4)
shot("00-resting")
for _ in range(25):   # the picture is a 2560x1440 PNG decoded off the main thread: slow on a cold, busy machine
    if mean_pixel("00-resting", 640, 180) != (16.0, 18.0, 20.0):
        break
    time.sleep(1)
    shot("00-resting")
check("bar connects to agentd", "Ask anything" and wait(lambda m: m.get("type") == "status", 5) is not None)
check("the stone rests green in the pill", stone_pixels("00-resting") > 100, stone_pixels("00-resting"))

# 0. the ground: Bombadil's wallpaper is under the desk, not the compositor's own colour (sway's
# #33404d above). The corners are the ground falling to its darkest, the middle is lit, the stone
# lies in the middle, and nothing in it is orange or anywhere near as light as the glass.
corners = [mean_pixel("00-resting", x, y) for x, y in ((6, 6), (1273, 6), (6, 700), (1273, 700))]
check("the corners of the desk are the wallpaper's ground, not sway's colour",
      all(11 <= r <= 17 and 13 <= g <= 19 and 15 <= b <= 21 for r, g, b in corners), corners)
lit, side = mean_pixel("00-resting", 640, 180), mean_pixel("00-resting", 250, 180)
check("the wallpaper is lit softly in the middle", lit[2] - side[2] >= 3 and lit[2] <= 34, f"{lit} vs {side}")
body = mean_pixel("00-resting", 640, 260)
check("the stone lies faintly in the middle, lighter than its ground", body[2] - lit[2] >= 5 and body[2] <= 46, f"{body} vs {lit}")
check("nothing in the wallpaper is orange", all(r - b <= 3 for r, g, b in corners + [lit, body]), [lit, body])

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


def desk_ipc(*args):
    return run("quickshell", "ipc", "-p", str(REPO / "shell" / "shell.qml"), "call", "desk", *args)


def desk_state():
    try:
        return json.loads(desk_ipc("state").stdout)
    except ValueError:
        return {}


# 22b. what a window does to a picture. The pill is 360 wide while a window shares the stage and a
# 100 px capsule under a full-screen one; a picture over the middle of either cannot be read next to
# the window. A window that opens puts it away; a window going full-screen hides it, and it returns.
time.sleep(2.0)                 # a window right after a picture is the same ask's: it is let be
middle = glass_pixels(500, 480, 200)
check("the picture word's picture is up in the middle of the bar", middle > 40, middle)
tiled = json.dumps({"windows": [{"x": 0, "y": 40, "w": 1280, "h": 600}]})
full = json.dumps({"windows": [{"x": 0, "y": 40, "w": 1280, "h": 600, "fullscreen": True}]})
desk_ipc("cover", tiled)
time.sleep(1.0)
shot("23-picture-window-opened")
gone = glass_pixels(500, 480, 200)
check("a window that opens puts the picture away", desk_state().get("mode") == "shared" and gone < middle // 3, f"{middle} -> {gone}")
m = mark()
summon()
typ("how am i connected")
key("Return")
wait(ev("local", action="picture", phase="done"), 20, m)
time.sleep(1.2)
shot("23-picture-beside-window")
shared = glass_pixels(500, 480, 200)
check("a picture asked for beside a window still shows", shared > 20, shared)
desk_ipc("cover", full)
time.sleep(1.0)
st = desk_state()
shot("23-picture-fullscreen")
check("a window that goes full-screen makes the pill the capsule", st.get("mode") == "immersive" and st.get("pillWidth") == 100, (st.get("mode"), st.get("pillWidth")))
hidden = glass_pixels(500, 480, 200)
check("and hides the picture over it", hidden < shared // 3, f"{shared} -> {hidden}")
desk_ipc("cover", tiled)
time.sleep(1.2)
back = glass_pixels(500, 480, 200)
check("the picture comes back when the window leaves full screen", back > shared // 2, f"{hidden} -> {back}")
desk_ipc("cover", '{"windows": []}')
time.sleep(0.8)


# 8. the desk: Now on the left rail while a two-step plan runs, folded by a window over it, and the
# `desk` word. (Hyprland's own window list is not here: the desk is told where the windows are.)
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
REVIEWER = {"key": "rev", "project": "bombadil", "projectTitle": "Bombadil", "role": "reviewer", "tool": "claude",
            "toolTitle": "Claude Code", "title": "reviewer", "state": "asked", "alive": True, "unseen": False,
            "yours": False, "copy": False, "since": now_s - 30, "last": "apply the migration to the local database?",
            "lines": []}
# One session waiting is the line in the pill and no card, and the stone knocks all the same.
inject({"type": "dev", "sessions": [REVIEWER], "attention": ["rev"], "front": "", "line": ""})
time.sleep(1.0)
st = desk_state()
shot("26a-desk-one-waiting")
check("one waiting session: no card, and the stone is amber",
      not st["present"]["needs"] and st["needsYou"] and st["face"] == "needs"
      and stone_pixels("26a-desk-one-waiting", colour="#e0a93b") > 60,
      f"present {st['present']['needs']}, face {st.get('face')}")
inject({"type": "dev", "sessions": [dict(REVIEWER, state="working")], "attention": [], "front": "", "line": ""})
time.sleep(1.0)
st = desk_state()
shot("26b-desk-answered")
check("answered: the session stays listed and the stone goes still",
      not st["needsYou"] and st["face"] != "needs" and stone_pixels("26b-desk-answered", colour="#e0a93b") < 20,
      f"face {st.get('face')}")
inject({"type": "dev", "sessions": [
    REVIEWER,
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
# The stone knocks while anything waits: amber, not the green of rest or the orange of work.
amber = stone_pixels("26-desk-watching-needs", colour="#e0a93b")
check("the stone is amber while two sessions wait for you", st.get("needsYou") and st.get("face") == "needs" and amber > 60,
      f"face {st.get('face')}, amber pixels {amber}")
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
shot("27-desk-clear")
check("and the stone is still: nothing waits, nothing is amber",
      not st.get("needsYou") and st.get("face") != "needs" and stone_pixels("27-desk-clear", colour="#e0a93b") < 20,
      f"face {st.get('face')}")

# 10. out of plan: the AI's plan runs out. The scripted API answers the marked ask (a haiku) with the 429 a used-up
# claude.ai plan gets, so the real CLI (a claude.ai login for this part, see bin/claude) prints its own
# rate_limit_event and "You've hit your session limit". The machine rests: the ask waits as a chip, launcher words
# and "!" commands still run, nothing more is sent to the API, and once the plan is back the same ask runs again in
# the same conversation: the first time the owner says so, the second time the clock does.
STONE = (198, 744, 232, 780)   # the stone in the pill, 24 px wide, at the field's left end
LINE = (195, 658, 30)          # a one-pixel strip down the line above the pill, over its chip row (see glass_pixels)
claude_ai = Path.home() / ".e2e-claude-ai"
claude_ai.touch()
rest_file = Path.home() / ".local" / "state" / "bombadil" / "rest.json"


def ink(shot_name):
    """What colours the stone is drawn in: grey while the AI rests, green at rest, amber for needs, red for offline."""
    tokens = {"grey": "#8b939c", "green": "#5fb36b", "amber": "#e0a93b", "red": "#d05555"}
    return {c: colour_pixels(shot_name, v, STONE) for c, v in tokens.items()}


summon()
typ("tell me a joke")   # a first turn that works, so the haiku has a conversation to come back to
n = mark()
key("Return")
first = wait(ev("result"), 40, n)
check("the CLI works as a claude.ai login too", first is not None and first.get("ok"), first and first.get("text"))
time.sleep(1.5)
reset = int(time.time()) + 7200
api_control({"reset": reset})
before = len(api_log())
summon()
typ("write a haiku about rain")
n = mark()
key("Return")
asked = wait(ev("turn_start"), 10, n)
haiku = asked and asked.get("turn")
resting = wait(lambda m: m.get("type") == "setup" and m.get("state") == "resting", 60, n)
end = wait(ev("turn_end", turn=haiku), 20, n)
time.sleep(1.0)
shot("27-resting")
refusals = [r for r in api_log()[before:] if r["refused"]]
check(
    "the API refused the ask once and the CLI did not retry it",
    len(refusals) == 1 and len(api_log()) == before + 1,
    len(api_log()) - before,
)
r = (resting or {}).get("rest") or {}
check(
    "the CLI's own limit notice puts the machine to rest, with the time the plan gave",
    r.get("provider") == "claude" and r.get("why") == "limit" and r.get("kind") == "five_hour" and r.get("until") == reset,
    resting,
)
check(
    "the line says when and that the apps still work, and offers no button",
    resting and resting["line"].startswith("Claude is at its limit until ")
    and resting["line"].endswith("Your apps and files still work.") and resting["actions"] == [] and resting["tone"] == "step",
    resting and (resting["line"], resting["actions"]),
)
check(
    "the empty field says what waits and until when",
    r.get("hint", "").startswith("Open or find anything. Asks wait for ") and r.get("when") and r["when"] in r["hint"],
    r.get("hint"),
)
check(
    "the turn ends to run again, with no error and no result",
    end and end.get("requeued") is True and end.get("line") == resting["line"] and not any(
        m.get("type") == "event" and m.get("kind") in ("error", "result") and m.get("turn") == haiku for m in events[n:]
    ),
    end and end.get("line"),
)
check(
    "the log of the turn says the limit stopped it",
    wait(ev("rest", turn=haiku, window="five_hour", until=reset), 1, n) is not None,
)
check(
    "the ask goes back to the front of the queue under the same id",
    wait(ev("queued", turn=haiku, prompt="write a haiku about rain"), 1, n) is not None,
)
with lock:
    sts = [m for m in events[n:] if m.get("type") == "status" and m.get("setup") == "resting"]
st = sts[-1] if sts else None
check(
    "the ask waits at the front as a chip that says when, and nothing is on it",
    st and st["queue"] and st["queue"][0]["turn"] == haiku and st["queue"][0].get("wait") == r.get("wait") and not st["busy"],
    st and st["queue"],
)
row = wait(lambda m: m.get("type") == "ai" and any(x["name"] == "claude" and x["state"] == "limit" for x in m["rows"]), 5, n)
row = row and next(x for x in row["rows"] if x["name"] == "claude")
check(
    "the AI card has a row for Claude that says when it is back",
    row and row["text"].startswith("at its limit until ") and row["current"] and row["enabled"] and row["on"],
    row,
)
stone = ink("27-resting")
check(
    "the stone rests as a grey hollow: no green, no amber, no red",
    stone["grey"] > 20 and not (stone["green"] or stone["amber"] or stone["red"]) and ink("00-resting")["grey"] < 10,
    stone,
)
check("the line above the pill says it", glass_pixels(*LINE) > 20, glass_pixels(*LINE))
check(
    "the line fades by itself and the empty field carries the state",
    until(lambda: glass_pixels(*LINE) < 5, 30),
    glass_pixels(*LINE),
)
shot("28-resting-field")
summon()
time.sleep(0.8)
shot("29-resting-summoned")
check("the line comes back when the pill is summoned", glass_pixels(*LINE) > 20, glass_pixels(*LINE))
key("Escape")

# words that never need the AI still work while it rests: an app by name, and a "!" command.
before = len(api_log())
words_from = mark()   # the finder below looks at what these two do not send: no "found" for either
m = mark()
summon()
typ("passwords")
key("Return")
o = wait(ev("local", phase="done"), 10, m)
time.sleep(0.5)
shot("30-resting-passwords")
check("an app opens by name while the AI rests", o is not None and o.get("ok"), o and o.get("text"))
summon()
typ("!echo hi")
m = mark()
key("Return")
bang = wait(ev("turn_end"), 20, m)
time.sleep(0.4)
shot("31-resting-bang")
check(
    "a ! command runs while the AI rests, with no model",
    bang is not None and not bang.get("requeued") and wait(ev("turn_start", prompt="!echo hi"), 1, m) is not None
    and len(api_log()) == before,
    bang,
)

# no "found" for what the finder is not for: a launcher word ("passwords" runs at once, and would be found if it were
# looked for) and a "!" command (neither waits for the AI).
check(
    "no found for a launcher word or a ! command",
    wait(lambda x: x.get("type") == "found", 1.0, words_from) is None,
    [x for x in events[words_from:] if x.get("type") == "found"],
)


# the finder: while the AI rests, a sentence that has to wait is kept as a chip, and agentd also looks, on this computer
# only (no model), for the apps, launcher words and past asks it nearly names. It only offers: one "found" message
# with the line and up to three matches. A press (found_open, which the bar sends on a click) opens one.
def passwords_up():
    return "bombadil-app-passwords" in run("swaymsg", "-t", "get_tree").stdout


def kept_ask(prompt):
    """Type an ask while the AI rests: (its turn, the mark before it) once agentd has kept it as a chip."""
    summon()
    typ(prompt)
    n = mark()
    key("Return")
    q = wait(ev("queued", prompt=prompt), 5, n)
    return (q or {}).get("turn"), n


def found_for(turn, since, timeout=10):
    return wait(lambda x: x.get("type") == "found" and x.get("turn") == turn, timeout, since)


def kept_queue():
    """The waiting asks as the latest status says them, [(turn, the chip's label)]."""
    with lock:
        sts = [x for x in events if x.get("type") == "status"]
    return [(q["turn"], q.get("wait")) for q in sts[-1]["queue"]] if sts else []


# The bar's state does not say what chips it shows (the desk's `state` has only the stone's face), so they are read off the
# screenshots. The stack above the pill grows up from it: the line, then (while there is something found) a row of
# chips 30 px high and 8 px apart, then the setup's chips, then the waiting asks. So a chip row is the line standing 38 px
# higher than it does without one, and the chips are the rounded stretches of glass across the row under the line.
CHIP_ROW = 30 + 8
FOUND_ROW = 666       # a pixel row just inside the top of that row of chips: glass, no text yet
LINE_ROW = 622        # and one just inside the top of the line


def line_top(name, x=195, rows=(560, 735)):
    """The first row (from the top) of a screenshot where the bar's glass is at x: the top of the line above the pill,
    whose left end is at x=190 and over which nothing else is drawn that far left. None when there is no line."""
    from PySide6.QtGui import QImage
    img = QImage(str(OUT / f"{name}.png"))

    def glass(y):
        c = img.pixelColor(x, y)
        return 21 <= c.red() <= 30 and 24 <= c.green() <= 34 and 28 <= c.blue() <= 38

    # A single row of it is not a line: the bar's window edge is a hairline that comes and goes at the
    # edge of that colour. The line is a box a few dozen rows high.
    for y in range(*rows):
        if all(glass(y + i) for i in range(12)):
            return y
    return None


def glass_runs(name, y, x0=190, x1=1090):
    """The stretches (from, to) of one row of a screenshot that are the bar's glass; a gap of 5 px or more parts two."""
    from PySide6.QtGui import QImage
    img, runs = QImage(str(OUT / f"{name}.png")), []
    for x in range(x0, x1):
        c = img.pixelColor(x, y)
        if 21 <= c.red() <= 30 and 24 <= c.green() <= 34 and 28 <= c.blue() <= 38:
            if runs and x - runs[-1][1] <= 5:
                runs[-1][1] = x
            else:
                runs.append([x, x])
    return [tuple(run) for run in runs]


def chip_shapes(name, count):
    """True when `count` chips stand under the line: that many rounded stretches of glass in the chip row, each far
    narrower than the line, each with the label's light text on it."""
    runs = glass_runs(name, FOUND_ROW)
    texts = [colour_pixels(name, "#e6e8eb", (a, FOUND_ROW - 2, b, FOUND_ROW + 24)) for a, b in runs]
    line = glass_runs(name, LINE_ROW)
    return (len(runs) == count and all(b - a < 700 for a, b in runs) and all(t > 15 for t in texts)
            and len(line) == 1 and line[0][1] - line[0][0] > 800), (runs, texts, line)


def crop(name, box=(180, 560, 920, 240)):
    """The pill's part of a screenshot (x, y, w, h) saved beside it as <name>-pill.png: no window is in it."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage
    QImage(str(OUT / f"{name}.png")).copy(QRect(*box)).save(str(OUT / f"{name}-pill.png"))


rest_when, rest_wait = r.get("when"), r.get("wait")
no_chips_at = line_top("29-resting-summoned")   # the resting line over the waiting haiku: where the line stands with no chips

# 1. a sentence that names an app: the app is found, and the ask is kept all the same.
summon()
typ("quit passwords")   # the Passwords window is up from above: put it away, so that a press has to bring it back
key("Return")
check("the Passwords window is put away first", until(lambda: not passwords_up(), 10), passwords_up())
asks0 = api_requests()
t_pw, n = kept_ask("my password app")
f1 = found_for(t_pw, n)
time.sleep(0.8)
shot("36-found-app")
m0 = (f1 or {}).get("matches") or [{}]
check(
    "my password app: found, with the line that says when the ask runs and the Passwords app first",
    f1 and f1["prompt"] == "my password app" and f1["line"] == f"Kept for {rest_when}. Found on this computer:"
    and (m0[0].get("id"), m0[0].get("kind"), m0[0].get("label"), m0[0].get("hint")) == ("1", "app", "Passwords", "App"),
    f1 and (f1["line"], f1["matches"]),
)
check(
    "the finder sent nothing to the API, and the ask waits as a chip that says when",
    api_requests() == asks0 and dict(kept_queue()).get(t_pw) == rest_wait and rest_wait,
    (api_requests() - asks0, kept_queue()),
)
stone = ink("36-found-app")
check(
    "the stone is still the resting grey while it looks",
    stone["grey"] > 20 and not (stone["green"] or stone["amber"] or stone["red"]) and desk_state().get("face") == "resting",
    (stone, desk_state().get("face")),
)
ok, seen = chip_shapes("36-found-app", len(f1["matches"]) if f1 else 0)
check(
    "the bar shows one quiet chip under the line for each thing found, a row above where the line stands with none",
    f1 and ok and line_top("36-found-app") == no_chips_at - CHIP_ROW,
    (seen, line_top("36-found-app"), no_chips_at),
)
crop("36-found-app")

# 2. a press on it: the app opens as if its word was typed, and the ask it came from is let go.
m = mark()
send({"type": "found_open", "turn": t_pw, "id": "1"})
done = wait(ev("local", phase="done"), 15, m)
gone = wait(ev("unqueued", turn=t_pw), 5, m)
up = until(passwords_up, 10)
time.sleep(1.0)
shot("37-found-opened")
check("a press opens the app: a local answer that went well, and the Passwords window is up", done and done.get("ok") and up, done and done.get("text"))
check("and lets go of the kept ask, with nothing sent to the API",
      gone is not None and t_pw not in dict(kept_queue()) and api_requests() == asks0, kept_queue())
check("and its chips are gone from the bar", line_top("37-found-opened") in (None, no_chips_at)
      and not glass_runs("37-found-opened", LINE_ROW), (line_top("37-found-opened"), no_chips_at))

# 3. a sentence that names nothing here: kept, and the line says there is nothing.
t_fr, n = kept_ask("what is the capital of france")
f3 = found_for(t_fr, n)
time.sleep(0.8)
shot("38-found-nothing")
check(
    "a sentence that names nothing is kept, and the line says nothing matches",
    f3 and f3["matches"] == [] and f3["line"] == f"Kept for {rest_when}. Nothing on this computer matches."
    and dict(kept_queue()).get(t_fr) == rest_wait and api_requests() == asks0,
    f3 and (f3["line"], f3["matches"]),
)
check(
    "and the bar shows no chip under it: the line stands where it does with none",
    line_top("38-found-nothing") == no_chips_at and not glass_runs("38-found-nothing", LINE_ROW),
    (line_top("38-found-nothing"), no_chips_at),
)
crop("38-found-nothing")

# 4. a past ask: "install ffmpeg" went well at the start of this run, and "ffmpeg" nearly names it.
t_ff, n = kept_ask("ffmpeg")
f4 = found_for(t_ff, n)
time.sleep(0.8)
shot("39-found-ask")
past = next((x for x in (f4 or {}).get("matches", []) if x["kind"] == "ask"), {})
check(
    "ffmpeg finds the past ask, by its words and how long ago, with its steps behind it",
    f4 and f4["line"] == f"Kept for {rest_when}. Found on this computer:"
    and past.get("label", "").startswith("You asked: install ffmpeg (") and past.get("hint") == "Its steps",
    f4 and f4["matches"],
)
ok, seen = chip_shapes("39-found-ask", len(f4["matches"]) if f4 else 0)
check("the bar shows its chip under the line", f4 and ok and line_top("39-found-ask") == no_chips_at - CHIP_ROW, seen)
crop("39-found-ask")
# the line fades after 12 s, like the resting line, and the chips with it
gone_line = until(lambda: glass_pixels(195, 560, 175) < 5, 25)
shot("39-found-ask-faded")
check("the chips fade with the line", gone_line and not glass_runs("39-found-ask-faded", FOUND_ROW) and line_top("39-found-ask-faded") is None,
      (glass_runs("39-found-ask-faded", FOUND_ROW), line_top("39-found-ask-faded")))

# 5. a press on what was not offered does nothing: another id, a turn that is no longer waiting.
m = mark()
send({"type": "found_open", "turn": t_ff, "id": "9"})
send({"type": "found_open", "turn": t_pw, "id": "1"})
check(
    "a press on an id that was not offered, or on a ask that left, opens nothing and drops nothing",
    wait(lambda x: x.get("type") == "event" and x.get("kind") in ("local", "unqueued"), 1.2, m) is None
    and t_ff in dict(kept_queue()) and not drawer_open(),
    [x for x in events[m:] if x.get("type") == "event"],
)

# 4b. the press on the past ask opens the steps of that turn in the Details drawer, and lets the kept ask go.
m = mark()
send({"type": "found_open", "turn": t_ff, "id": past.get("id", "1")})
gone = wait(ev("unqueued", turn=t_ff), 5, m)
shown = until(drawer_open, 10)
time.sleep(1.5)
shot("40-found-ask-opened")
watch = [ln for ln in run("pgrep", "-af", "bombadil").stdout.splitlines() if "watch --file" in ln]
check("a press on the past ask lets go of the kept ask and opens its steps in the Details drawer",
      gone is not None and t_ff not in dict(kept_queue()) and shown and bool(watch), (gone, shown, watch[:1]))
rows = []
for ln in (rest_file.parent / "turns.jsonl").read_text().splitlines():
    try:
        rows.append(json.loads(ln))
    except ValueError:
        pass
steps = next((x.get("details") for x in rows if x.get("prompt") == "install ffmpeg" and x.get("ok") is True), None)
check("the drawer shows the steps of that very turn: the log of the ask that installed ffmpeg",
      steps and any(f"watch --file {steps}" in ln for ln in watch), (steps, watch[:1]))
has_keys = until(lambda: focused_app() == "bombadil-details", 5)
key("Escape")
check("the drawer has the keyboard and one Esc puts it away", has_keys and until(lambda: not drawer_open(), 5), focused_app())
if drawer_open():
    send({"type": "close_details"})
check("nothing of this went to the API", api_requests() == asks0, api_requests() - asks0)

# an app's own ask is not looked for either, and the one kept above that names nothing goes, so that the count at the
# return is the haiku alone.
m = mark()
send({"type": "prompt", "text": "[from app passwords] list my logins"})
app_ask = wait(ev("queued", prompt=lambda p: (p or "").startswith("[from app passwords]")), 5, m)
check("an app's ask that waits is not looked for", app_ask is not None and wait(lambda x: x.get("type") == "found", 1.0, m) is None)
for t in (app_ask and app_ask.get("turn"), t_fr):
    send({"type": "unqueue", "turn": t})
time.sleep(0.5)
check("the asks of the finder's tests are let go: the haiku alone waits", [q[0] for q in kept_queue()] == [haiku], kept_queue())

# the plan comes back: the API lets the ask through, the state file says the time has passed, and the owner says so.
api_control(None)
data = json.loads(rest_file.read_text())
data["providers"]["claude"]["until"] = time.time() - 600
rest_file.write_text(json.dumps(data))
m = mark()
summon()
typ("resume the ai")
key("Return")
back = wait(lambda x: x.get("type") == "setup" and x.get("state") == "ready", 20, m)
check(
    "the machine says it is back and what runs now",
    back and back["line"] == "Claude is back. Running your waiting ask." and back["tone"] == "done" and "rest" not in back,
    back and back["line"],
)
again = wait(ev("turn_start", turn=haiku), 20, m)
done = wait(ev("turn_end", turn=haiku), 60, m)
res = wait(ev("result", turn=haiku), 1, m)
check(
    "the same ask runs again under the same id",
    again is not None and again["prompt"] == "write a haiku about rain",
    again and again.get("prompt"),
)
check(
    "it ends well, with the answer, in the same conversation",
    done and not done.get("requeued") and res and res.get("ok") and "kettle" in res.get("text", "")
    and res.get("session_id") == first.get("session_id") and api_log()[-1]["prompts"] > 1 and not api_log()[-1]["refused"],
    res and (res.get("session_id"), first.get("session_id"), api_log()[-1]),
)
time.sleep(2.5)
shot("32-back")
check("the stone is green again", stone_pixels("32-back") > 100, stone_pixels("32-back"))

# and by itself: the plan says it is back in fifteen seconds, a second ask waits behind the first, and the machine
# wakes on the clock, a minute after the time (agentd's REST_POLL looks, the wall clock decides).
reset = int(time.time()) + 15
api_control({"reset": reset})
summon()
typ("write a haiku about rain")
n = mark()
key("Return")
asked = wait(ev("turn_start"), 10, n)
haiku = asked and asked.get("turn")
resting = wait(lambda m: m.get("type") == "setup" and m.get("state") == "resting", 60, n)
check(
    "a second refusal rests the machine again, with the new time",
    resting and resting["rest"]["until"] == reset,
    resting and resting.get("rest"),
)
summon()
typ("tell me a joke")
key("Return")
joke = wait(lambda m: m.get("type") == "status" and len(m.get("queue", [])) == 2, 10, n)
shot("33-two-waiting")
check(
    "a second ask waits behind the first, each with its time",
    joke and [q["turn"] for q in joke["queue"]][0] == haiku and all(q.get("wait") for q in joke["queue"]),
    joke and joke["queue"],
)
api_control(None)
woke = wait(lambda m: m.get("type") == "setup" and m.get("state") == "ready", 120, n)
woke_at = time.time()
check(
    "the machine wakes by itself, a minute after the time, and says what runs now",
    woke and woke["line"] == "Claude is back. Running your 2 waiting asks." and woke["tone"] == "done" and woke_at >= reset + 59,
    woke and (woke["line"], round(woke_at - reset, 1)),
)
with lock:
    k = events.index(woke) if woke else n
d1 = wait(ev("turn_end", turn=haiku), 60, k)
d2 = wait(ev("turn_end", turn=lambda t: t is not None and t > haiku), 60, k)
check(
    "both asks run, the first one first",
    d1 is not None and d2 is not None and d1["_t"] < d2["_t"] and not d1.get("requeued"),
    (d1 and d1.get("line"), d2 and d2.get("line")),
)
time.sleep(2.5)
shot("34-woke")
check("the stone is green again after the wake", stone_pixels("34-woke") > 100, stone_pixels("34-woke"))

# a CLI that does not end the turn: in its unattended retry mode it sleeps until the reset and says api_retry, so
# agentd ends the turn for it (the first long wait), puts the CLI away and rests the same way.
watchdog = Path.home() / ".e2e-retry-watchdog"
watchdog.touch()
reset = int(time.time()) + 7200
api_control({"reset": reset})
summon()
typ("write a haiku about rain")
n = mark()
key("Return")
asked = wait(ev("turn_start"), 10, n)
haiku = asked and asked.get("turn")
resting = wait(lambda m: m.get("type") == "setup" and m.get("state") == "resting", 30, n)
end = wait(ev("turn_end", turn=haiku), 10, n)
check(
    "a CLI that waits out the limit instead of ending is ended for it, and the machine rests",
    resting and resting["rest"]["until"] == reset and resting["rest"]["kind"] == "five_hour" and end and end.get("requeued") is True,
    resting and resting.get("rest"),
)
check(
    "putting it away is not an error, and says no result",
    not any(m.get("type") == "event" and m.get("kind") in ("error", "result") and m.get("turn") == haiku for m in events[n:]),
    [(m["kind"], m.get("text")) for m in events[n:] if m.get("kind") in ("error", "result") and m.get("turn") == haiku],
)
clis = lambda: [ln for ln in run("ps", "-eo", "pid,args").stdout.splitlines() if "--output-format stream-json" in ln]
check("and the CLI is not left sleeping", until(lambda: not clis(), 10), clis())
watchdog.unlink()
api_control(None)
data = json.loads(rest_file.read_text())
data["providers"]["claude"]["until"] = time.time() - 600
rest_file.write_text(json.dumps(data))
m = mark()
summon()
typ("resume the ai")
key("Return")
res = wait(ev("result", turn=haiku), 60, m)
check(
    "the same ask runs again once it is back",
    res is not None and res.get("ok") and "kettle" in res.get("text", ""),
    res and res.get("text"),
)
time.sleep(2.5)

# paused by hand: the same rest with no time and one button; an ask waits as "paused", and the button lets it run.
summon()
typ("pause claude")
n = mark()
key("Return")
paused = wait(lambda m: m.get("type") == "setup" and m.get("state") == "resting", 10, n)
row = wait(lambda m: m.get("type") == "ai" and any(x["name"] == "claude" and x["state"] == "paused" for x in m["rows"]), 5, n)
row = row and next(x for x in row["rows"] if x["name"] == "claude")
check("the AI card's row says paused, with its switch off", row and row["text"] == "paused" and not row["on"] and row["enabled"], row)
check(
    "pause claude rests it by hand, with one button to resume",
    paused and paused["rest"]["why"] == "hand" and paused["rest"]["until"] is None
    and paused["line"] == "Claude is paused. Your apps and files still work."
    and [(a["id"], a["label"]) for a in paused["actions"]] == [("resume", "Resume Claude")],
    paused and (paused["line"], paused["actions"]),
)
before = len(api_log())
summon()
typ("write a haiku about rain")
n = mark()
key("Return")
queued = wait(lambda m: m.get("type") == "status" and m.get("queue"), 10, n)
time.sleep(1.0)
shot("35-paused")
check(
    "an ask waits as paused, with nothing sent and the stone resting grey",
    queued and queued["queue"][0].get("wait") == "paused" and len(api_log()) == before and ink("35-paused")["grey"] > 20
    and not ink("35-paused")["green"],
    queued and queued["queue"],
)
# the finder while paused by hand: the ask is kept "until you resume", and agentd looks all the same. The first ask is
# the haiku above, which went well earlier (a past ask now); the second names nothing and goes again.
f7 = wait(lambda x: x.get("type") == "found" and x.get("prompt") == "write a haiku about rain", 5, n)
check(
    "paused by hand the line says the ask is kept until you resume, and what is found",
    f7 and f7["line"] == "Kept until you resume Claude. " + (
        "Found on this computer:" if f7["matches"] else "Nothing on this computer matches."),
    f7 and (f7["line"], f7["matches"]),
)
check(
    "and the haiku that ran earlier is found as a past ask",
    f7 and any(x["kind"] == "ask" and x["label"].startswith("You asked: write a haiku about rain (") for x in f7["matches"]),
    f7 and f7["matches"],
)
t_nf, n7 = kept_ask("what is the capital of france")
f7b = found_for(t_nf, n7)
time.sleep(0.8)
shot("41-found-paused")
crop("35-paused")
crop("41-found-paused")
check(
    "paused, the bar shows a chip row under the line for what is found, and none for nothing",
    line_top("41-found-paused") - line_top("35-paused") == CHIP_ROW,
    (line_top("35-paused"), line_top("41-found-paused")),
)
check(
    "paused by hand, a sentence that names nothing says so too",
    f7b and f7b["line"] == "Kept until you resume Claude. Nothing on this computer matches." and f7b["matches"] == [],
    f7b and (f7b["line"], f7b["matches"]),
)
send({"type": "unqueue", "turn": t_nf})
time.sleep(0.5)
m = mark()
send({"type": "setup_action", "id": "resume"})
back = wait(lambda x: x.get("type") == "setup" and x.get("state") == "ready", 10, m)
res = wait(ev("result"), 60, m)
check(
    "Resume Claude brings it back and the waiting ask runs",
    back and back["line"] == "Claude is back. Running your waiting ask." and res and res.get("ok") and "kettle" in res.get("text", ""),
    back and back["line"],
)
claude_ai.unlink()


# 11. Machine: agentd's `machine` message, injected (the checks above are the shell's half; vitals.py and the
# loop that sends it are tested on a fake machine in pytest).
def region_pixels(name, box, colour, tol=14):
    """How many pixels in the box (x, y, w, h) are this colour."""
    from PySide6.QtGui import QColor, QImage
    want, img, n = QColor(colour), QImage(str(OUT / f"{name}.png")), 0
    x0, y0, w, h = box
    for y in range(max(0, y0), min(img.height(), y0 + h)):
        for x in range(max(0, x0), min(img.width(), x0 + w)):
            c = img.pixelColor(x, y)
            n += abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < tol * 3
    return n


summon()
typ("how's the machine?")
before = api_requests()
n = mark()
key("Return")
asked = wait(ev("local", phase="done"), 10, n)
check("asking how the machine is answers in a line, with no model", asked and asked.get("ok")
      and asked.get("text") == "Here is the machine." and api_requests() == before, asked and asked.get("text"))
key("Escape")
inject({"type": "machine", "present": True, "asked": False,
        "why": "Memory is nearly full \u00b7 sessions use most",
        "strip": {"text": "memory 91%", "dot": "amber"},
        "rows": [
            {"key": "memory", "kind": "stack", "title": "Memory", "meterText": "14.5 of 16 GB", "meter": 0.91,
             "tone": "amber", "parts": [{"tone": "machine", "fraction": 0.12}, {"tone": "sessions", "fraction": 0.40},
                                        {"tone": "you", "fraction": 0.39}], "opens": ""},
            {"key": "disk", "kind": "meter", "title": "Disk", "meterText": "214 of 230 GB", "meter": 0.93,
             "tone": "amber", "opens": "disk"},
            {"key": "cpu", "kind": "meter", "title": "Processor", "meterText": "37% busy \u00b7 62\u00b0", "meter": 0.37,
             "tone": "you", "opens": ""},
            {"key": "net", "kind": "plain", "title": "Network", "sub": "\u2193 1.2 MB/s   \u2191 40 kB/s",
             "tone": "you", "opens": ""}]})
time.sleep(1.5)
st = desk_state()
shot("28-desk-machine")
mc = st.get("machine") or {}
slot = (st.get("slots") or {}).get("machine") or {}
check("the Machine card is up in full, on the right rail", st["present"]["machine"] and st["faces"]["machine"] == "full"
      and slot.get("side") == "right", (st.get("faces"), slot))
check("it has memory, the disk, the processor and the network, memory in three parts",
      [r["key"] for r in mc.get("rows", [])] == ["memory", "disk", "cpu", "net"]
      and [p["tone"] for p in mc["rows"][0]["parts"]] == ["machine", "sessions", "you"], mc.get("rows"))
box = (slot.get("x", 0), slot.get("y", 0), slot.get("w", 0), slot.get("h", 0))
parts = {name: region_pixels("28-desk-machine", box, colour) for name, colour in
         (("amber", "#e0a93b"), ("machine", "#d97757"), ("sessions", "#5b9bd5"))}
check("the card draws amber for the lines crossed and one colour for each who uses the memory",
      parts["amber"] > 150 and parts["machine"] > 40 and parts["sessions"] > 150, parts)
check("the card is the face, so no chip beside the pill says memory", not st["strips"]["right"], st.get("strips"))
inject({"type": "machine", "present": False, "asked": False, "why": "", "strip": {"text": "", "dot": ""}, "rows": []})
time.sleep(0.8)
st = desk_state()
check("the card leaves when the machine says everything is back under its lines",
      st["faces"]["machine"] == "hidden" and not st["present"]["machine"], st.get("faces"))

# Quickshell logs a QML error as a warning and carries on (a colour left undefined draws white), so
# none of the checks above would notice one.
qml_errors = [ln for ln in re.sub(r"\x1b\[[0-9;]*m", "", (OUT / "quickshell.log").read_text()).splitlines()
              if re.search(r"\.qml\[|\.qml:\d+|Unable to assign|is not defined|TypeError|ERROR", ln)]
check("the shell logged no QML errors", not qml_errors, "; ".join(qml_errors[:3]))

# 30. the user's own wallpaper: ~/.config/bombadil/wallpaper names an image. The file new systems ship
# holds only comments, so the standard picture shows and a first path is noticed with no restart; the
# picture is dimmed toward the ground; a picture that will not load, a line that is not a path (an
# image written into the file) and a missing file all fall back to the standard one.
def paint(path, colour):
    from PySide6.QtGui import QColor, QImage
    img = QImage(2560, 1440, QImage.Format_RGB32)
    img.fill(QColor(colour))
    assert img.save(str(path))


def desk_colour(name, x=150, y=400):
    # The Passwords window opened above is still up in the middle; the left side is bare ground.
    time.sleep(1.5)   # the picture settles in over 300 ms, and a changed file is read a moment after
    shot(name)
    return mean_pixel(name, x, y)


def near(got, want, tol=8):
    return all(abs(g - w) <= tol for g, w in zip(got, want))


def dimmed(rgb, dim=0.6, ground=(16, 18, 20)):
    return tuple(c * (1 - dim) + g * dim for c, g in zip(rgb, ground))


standard = mean_pixel("00-resting", 150, 400)
home = Path.home()
choice = home / ".config" / "bombadil" / "wallpaper"
choice.parent.mkdir(parents=True, exist_ok=True)
shipped = REPO / "iso" / "airootfs" / "etc" / "skel" / ".config" / "bombadil" / "wallpaper"
choice.write_text(shipped.read_text())
paint(home / "first.png", "#336699")
paint(home / "second.png", "#993366")
paint(home / "white.png", "#ffffff")
qs_proc.terminate()
qs_proc.wait(10)
start("quickshell-wallpaper", [str(REPO / "bin" / "bombadil-shell")])
time.sleep(3)
got = desk_colour("30-own-wallpaper-shipped-file")
check("the file new systems ship (comments only) shows the standard picture", near(got, standard, 4),
      (got, standard))
choice.write_text(shipped.read_text() + "~/first.png\n")
got = desk_colour("30-own-wallpaper")
check("a first path is noticed with no restart, and the picture is dimmed toward the ground",
      near(got, dimmed((51, 102, 153)), 6), (got, dimmed((51, 102, 153))))
choice.write_text(choice.read_text() + "file://" + str(home / "second.png") + "\n")
got = desk_colour("30-own-wallpaper-changed")
check("adding a line changes the picture: the last line that is not a comment is the one used",
      near(got, dimmed((153, 51, 102)), 6), got)
choice.write_text("~/white.png\n")
got = desk_colour("30-own-wallpaper-white")
check("a white picture is no brighter than 115 on any channel, so the muted text reads over it",
      max(got) <= 115, got)


def paint_worst_case(path):
    """Saturated bands, a white ellipse and white and yellow stripes across the bottom third, where
    the pill and the cards sit: the picture the glass and the muted text have the hardest time over."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter
    img = QImage(2560, 1440, QImage.Format_RGB32)
    g = QLinearGradient(QPointF(0, 0), QPointF(2560, 960))
    for i, c in enumerate(("#ff2020", "#20ff40", "#2040ff", "#ff20e0", "#ffee20", "#20ffee", "#ff2020")):
        g.setColorAt(i / 6, QColor(c))
    q = QPainter(img)
    q.fillRect(QRectF(0, 0, 2560, 1440), QBrush(g))
    q.setBrush(QColor("#ffffff"))
    q.drawEllipse(QRectF(800, 160, 960, 640))
    for i, x in enumerate(range(0, 2560, 64)):
        q.fillRect(QRectF(x, 960, 32, 480), QColor("#ffffff" if i % 2 else "#ffee60"))
    q.end()
    assert img.save(str(path))


paint_worst_case(home / "worst.png")
choice.write_text("~/worst.png\n")
desk_colour("30-own-wallpaper-worst-case")
tmp = home / ".config" / "bombadil" / "wallpaper.new"
tmp.write_text("~/first.png\n")
os.replace(tmp, choice)
got = desk_colour("30-own-wallpaper-replaced")
check("a file replaced by renaming another over it is noticed too", near(got, dimmed((51, 102, 153)), 6), got)
choice.write_text("/nowhere/at/all.png\n")
got = desk_colour("30-own-wallpaper-missing")
check("a picture that will not load falls back to the standard one", near(got, standard, 4), (got, standard))
choice.write_bytes(Path(OUT / "30-own-wallpaper-missing.png").read_bytes())   # an image, not a path
got = desk_colour("30-own-wallpaper-image-in-file")
check("an image written into the file falls back to the standard one", near(got, standard, 4), (got, standard))
choice.write_text("~/first.png\n")
desk_colour("30-own-wallpaper-again")
choice.unlink()
got = desk_colour("30-own-wallpaper-gone")
check("taking the file away brings the standard picture back", near(got, standard, 4), (got, standard))
own_log = re.sub(r"\x1b\[[0-9;]*m", "", (OUT / "quickshell-wallpaper.log").read_text())
check("the log says once that the file holds a path, not the image",
      own_log.count("holds the path of an image, on one line, not the image itself") == 1, own_log[-300:])
check("the wallpaper's choices raised no QML error but the one for the picture that is not there",
      not [ln for ln in own_log.splitlines()
           if re.search(r"\.qml\[|\.qml:\d+|Unable to assign|is not defined|TypeError|ERROR", ln)
           and "Cannot open" not in ln and "wallpaper:" not in ln], own_log[-400:])

(OUT / "results.json").write_text(json.dumps(results, indent=2))
for p in procs[::-1]:
    p.terminate()
fails = [c for c in results["checks"] if not c["ok"]]
print(f"DONE pass={len(results['checks']) - len(fails)} fail={len(fails)}", flush=True)
