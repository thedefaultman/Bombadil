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
import tomllib
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
    BOMBADIL_BAR_INJECT="1",   # the bar takes injected notices (section 10); a bar on the image does not
    BOMBADIL_VITALS="0",    # the Machine card is injected below; the real machine must not answer over it
    # Connections run on their fake engine, in the container's own folders: the service, agentd and the shell all read
    # these, and a container's user is outside the service's default range of people (1000 to 59999).
    BOMBADIL_CONNECT_ENGINE="fake",
    BOMBADIL_CONNECT_UIDS="0-65535",
    BOMBADIL_CONNECT_SOCKET=str(xdg / "connect" / "connect.sock"),
    BOMBADIL_CONNECT_STATE=str(xdg / "connect" / "state"),
    DBUS_SESSION_BUS_ADDRESS=f"unix:path={xdg}/bus",   # the shell's NotificationServer owns the name on this bus
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
# The session bus first, so that the shell's NotificationServer is the only owner of org.freedesktop.Notifications
# (nothing else in the image claims it), then the connection service on its fake engine, so that agentd's first look
# at it finds it. A notification is posted to the bus the way a web page's is (gdbus or busctl; libnotify is not in the image).
start("dbus", ["dbus-daemon", "--session", "--nofork", f"--address={env['DBUS_SESSION_BUS_ADDRESS']}"])
(xdg / "connect" / "state").mkdir(mode=0o700, parents=True, exist_ok=True)
start("connect", [str(REPO / "bin" / "bombadil-connect")])
for path in (xdg / "bus", xdg / "connect" / "connect.sock"):
    for _ in range(100):
        if path.exists():
            break
        time.sleep(0.1)
start("agentd", [str(REPO / "bin" / "agentd")])
sock_path = xdg / "bombadil" / "agentd.sock"
for _ in range(100):
    if sock_path.exists():
        break
    time.sleep(0.1)
qs_proc = start("quickshell", [str(REPO / "bin" / "bombadil-shell")])

events, lock = [], threading.Lock()


def listen(mode="w"):
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(sock_path))
    with open(OUT / "events.jsonl", mode) as log:
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


def api_models():
    try:
        return [json.loads(line).get("model") for line in (OUT / "api-requests.jsonl").read_text().splitlines()]
    except OSError:
        return []


def api_requests():
    try:
        return len((OUT / "api-requests.jsonl").read_text().splitlines())
    except OSError:
        return 0


def until(pred, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


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
    px = mean_pixel("00-resting", 640, 180)
    # Wait past the bare ground (16, 18, 20) the desk shows until the picture is in, and past a first frame
    # that is still black (the output's own, before the bar has drawn anything): either one read as "drawn"
    # once and made every wallpaper pixel check below fail on that run.
    if px != (16.0, 18.0, 20.0) and max(px) > 3:
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

# 0. Bombadil says hello. No persona.toml yet, so the first bar to connect gets the card (name and voice),
# with the keyboard already on it; Enter saves, the greeting follows, and the next turn's system prompt
# carries the persona. Then the launcher word `voice` reopens it.
HOME = Path(os.environ["HOME"])
persona_toml = HOME / ".config" / "bombadil" / "persona.toml"
said_json = HOME / ".local" / "state" / "bombadil" / "said.json"
turns_jsonl = HOME / ".local" / "state" / "bombadil" / "turns.jsonl"
greeted = xdg / "bombadil" / "greeted"


def persona_now():
    try:
        return tomllib.loads(persona_toml.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def boot_rows():
    try:
        rows = [json.loads(line) for line in turns_jsonl.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if r.get("action") == "boot"]


def card_up():
    # The card's words are light text; nothing else above the pill is on a fresh desk (the wallpaper is dark
    # to its brightest point, and the pill's own rows are left out). File size would say nothing: the
    # wallpaper alone makes a picture of tens of KB.
    from PySide6.QtGui import QImage
    probe = OUT / "probe.png"
    run("grim", str(probe))
    img = QImage(str(probe))
    if img.isNull():
        return False
    light = 0
    for y in range(0, min(730, img.height()), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            light += c.red() + c.green() + c.blue() > 3 * 170
    return light > 60


check("the card is asked on the first sign-in", until(lambda: persona_now() is not None, 20), persona_now())
# The card is asked when sign-in turns ready, and the bar may still be starting: it gets the card the moment
# it says hello, so wait until it is on screen before typing into it.
check("the card is on screen", until(card_up, 30))
time.sleep(1.0)
shot("00a-card")
p0 = persona_now() or {}
check("defaults are written and this login counts as greeted", p0.get("voice") == "merry" and greeted.exists(), p0)
typ("Daniel")
shot("00b-card-typed")
key("Return")
check(
    "Enter on the card saves the name, with no summon first",
    until(lambda: (persona_now() or {}).get("name") == "Daniel", 10),
    persona_now(),
)
time.sleep(1.2)
shot("00c-welcome-line")
check(
    "the first hello was shown, and logged as a boot row that said something",
    until(lambda: any(r.get("greeted") for r in boot_rows()), 15),
    boot_rows(),
)
ledger = json.loads(said_json.read_text()) if said_json.exists() else {}
check("the ledger has the day it began", bool(ledger.get("setup_day")), ledger)

# the word `voice` reopens the card with what is saved; Down picks the next voice, Enter keeps it
summon()
typ("voice")
n = mark()
key("Return")
vd = wait(ev("local", action="voice", phase="done"), 10, n)
time.sleep(1.0)
shot("00d-voice-word")
check("the word voice answers locally", vd and vd.get("ok"), vd)
key("Down")
time.sleep(0.3)
shot("00e-voice-plain")
key("Return")
check(
    "Down then Enter picks the Plain voice and keeps the name",
    until(lambda: (persona_now() or {}).get("voice") == "plain", 10),
    persona_now(),
)
check("the name is unchanged", (persona_now() or {}).get("name") == "Daniel", persona_now())
time.sleep(0.8)
shot("00f-after-voice")

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
reqs = [json.loads(x) for x in (OUT / "api-requests.jsonl").read_text().splitlines()]
check(
    "the turn's system prompt carried the name and voice",
    any(r.get("persona") and "install ffmpeg" in r.get("first", "") for r in reqs),
    [(r.get("persona"), r.get("first", "")[:30]) for r in reqs[:4]],
)

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

# 5b. The model: Sonnet by default, "use opus" for the next turns and "use sonnet" back, each word
# answering at once without a model call.
check("turns so far asked for Sonnet", api_models() and set(api_models()) == {"claude-sonnet-5-5"}, set(api_models()))


def model_turn(word, expect):
    summon()
    before = api_requests()
    typ(word)
    n = mark()
    key("Return")
    sw = wait(ev("local", action="model", phase="done"), 10, n)
    check(f"{word!r} answers without a model call", sw and sw.get("ok") and api_requests() == before, sw)
    time.sleep(0.4)
    summon()
    typ("tell me a joke")
    n = mark()
    key("Return")
    end = wait(ev("turn_end"), 40, n)
    check(f"the next turn ran {expect}", end is not None and api_models()[-1] == expect, api_models()[-3:])


model_turn("use opus", "claude-opus-5-5")
model_turn("use sonnet", "claude-sonnet-5-5")

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
# The pill is 360 beside a window (460 to 820 on this 1280 screen); the picture takes about 560 (360 to 920).
inside_l, outside_l = glass_pixels(368, 480, 200), glass_pixels(340, 480, 200)
inside_r, outside_r = glass_pixels(912, 480, 200), glass_pixels(940, 480, 200)
check("beside a window the picture is wider than the pill, about 560 px", inside_l > 60 and inside_r > 60 and outside_l < 10 and outside_r < 10,
      f"left {inside_l}/{outside_l}, right {inside_r}/{outside_r}")
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
# rate_limit_event and "You've hit your 5-hour limit" (api_error "usage_limit_reached"). The machine rests: the ask
# waits as a chip, launcher words and "!" commands still run, nothing more is sent to the API, and once the plan is
# back the same ask runs again in the same conversation: the first time the owner says so, the second time the clock
# does.
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

# 12. dragging: a card by its title, or a chip, to another rail, another place or the row, with a virtual pointer
# (pointer.py: the test session has none). The grab has to go on delivering the pointer beyond the window it began
# in, from a rail to the other rail and to the bar, and agentd's answer is what moves the card.
ptr = subprocess.Popen([sys.executable, str(E2E / "pointer.py")], env=env, stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
procs.append(ptr)


def pointer(word):
    """One word to the virtual pointer (pointer.py); "ok" once the compositor has taken it."""
    ptr.stdin.write(word + "\n")
    ptr.stdin.flush()
    return ptr.stdout.readline().strip()


def drag(points, hold=None):
    """Press at the first point, move through the rest and, unless `hold` is called with the last one first, let go."""
    pointer(f"move {points[0][0]} {points[0][1]}")
    time.sleep(0.3)
    pointer("press")
    time.sleep(0.2)
    for x, y in points[1:]:
        pointer(f"move {x} {y}")
        time.sleep(0.05)
    time.sleep(0.4)
    if hold:
        hold()
    pointer("release")
    time.sleep(1.0)


def line(a, b, steps=14):
    return [(round(a[0] + (b[0] - a[0]) * i / steps), round(a[1] + (b[1] - a[1]) * i / steps)) for i in range(steps + 1)]


inject({"type": "jobs", "jobs": [
    {"id": "a1b2c3", "title": "Ubuntu 26.04 ISO", "kind": "job", "state": "running", "started": time.time() - 240,
     "deadline": None, "ended": None, "pct": 43.0, "last": "12 MB/s", "unit": "bombadil-job-a1b2c3"}]})
inject({"type": "dev", "sessions": [
    REVIEWER,
    {"key": "bld", "project": "bombadil", "projectTitle": "Bombadil", "role": "builder", "tool": "codex",
     "toolTitle": "Codex", "title": "builder", "state": "asked", "alive": True, "unseen": False, "yours": False,
     "copy": False, "since": now_s - 20, "last": "install qemu-full?", "lines": []}],
    "attention": ["rev", "bld"], "front": "", "line": ""})
inject({"type": "machine", "present": True, "asked": False, "why": "Memory is nearly full",
        "strip": {"text": "memory 91%", "dot": "amber"},
        "rows": [{"key": "memory", "kind": "meter", "title": "Memory", "meterText": "14.5 of 16 GB", "meter": 0.91,
                  "tone": "amber", "opens": ""}]})
time.sleep(1.5)
st = desk_state()
shot("40-drag-ready")
check("a card on each rail to drag between: Watching on the left, Needs you and Machine on the right",
      st["faces"]["watching"] == st["faces"]["needs"] == st["faces"]["machine"] == "full"
      and [st["rails"][w] for w in ("watching", "needs", "machine")] == ["left", "right", "right"], st.get("faces"))
w, nd, mc = (st["slots"][k] for k in ("watching", "needs", "machine"))
grab = (w["x"] + 60, w["y"] + 22)                      # the title of Watching
over = (nd["x"] + 150, nd["y"] + nd["h"] // 2 + 12)    # the right rail, just under the middle of Needs you
mid = {}


def midway():
    mid["state"] = desk_state()
    shot("41-drag-over-right-rail")


drag(line(grab, over), midway)
s = mid["state"]
check("held over the right rail, the card in hand is Watching and the drop would put it first on the right",
      s.get("drag") and s["drag"]["id"] == "watching" and s["dropTarget"] and s["dropTarget"]["kind"] == "rank"
      and s["dropTarget"]["side"] == "right" and s["dropTarget"]["rank"] == 0, (s.get("drag"), s.get("dropTarget")))
check("the shell puts the pointer where it is on the screen, from the left rail's window to the right's",
      s.get("drag") and abs(s["drag"]["x"] - over[0]) <= 2 and abs(s["drag"]["y"] - over[1]) <= 2, (s.get("drag"), over))
mark_y = (s.get("dropTarget") or {}).get("markY", 0)


def ink(name):
    """The card's text and meter, as pixels."""
    return region_pixels(name, (w["x"], w["y"], 300, w["h"]), "#e6e8eb")


marks = region_pixels("41-drag-over-right-rail", (nd["x"], round(mark_y) - 3, 300, 6), "#e6e8eb")
check("the right rail's window draws the mark under the pointer and the card in hand is dimmed where it stood",
      marks > 600 and ink("40-drag-ready") > 100 and ink("41-drag-over-right-rail") < ink("40-drag-ready") // 4,
      (mark_y, marks, ink("40-drag-ready"), ink("41-drag-over-right-rail")))
st = None
for _ in range(40):
    st = desk_state()
    if st.get("rails", {}).get("watching") == "right":
        break
    time.sleep(0.1)
shot("42-drag-dropped")
check("the drop moves Watching to the right rail's first place, and the dim is gone",
      st and st["rails"]["watching"] == "right" and st["order"]["right"][0] == "watching" and st.get("drag") is None
      and st.get("settling") == "" and st["slots"]["watching"]["side"] == "right", st and (st["order"], st.get("settling")))
toml = (Path.home() / ".local" / "state" / "bombadil" / "desk.toml").read_text()
check("and desk.toml keeps it", 'watching = "right"' in toml, toml)

# a card dropped into the row is folded alone
mc = st["slots"]["machine"]
grab = (mc["x"] + 60, mc["y"] + 22)
mid.clear()


def over_the_row():
    mid["state"] = desk_state()
    shot("43-drag-over-the-row")


drag(line(grab, (640, 770)), over_the_row)
s = mid["state"]
check("held over the row the drop would fold it", s.get("dropTarget") == {"kind": "fold"}, s.get("dropTarget"))
st = None
for _ in range(40):
    st = desk_state()
    if st.get("stripped"):
        break
    time.sleep(0.1)
time.sleep(0.8)
shot("44-drag-folded")
check("dropped in the row, Machine is a strip on its own and the desk is not folded",
      st and st["stripped"] == ["machine"] and st["faces"]["machine"] == "strip" and not st["folded"]
      and "machine" not in st["slots"] and st["faces"]["needs"] == "full", st and (st.get("stripped"), st.get("faces")))
check("its chip stands beside the pill", [c for c in st["strips"]["right"] if "memory" in c], st["strips"])

# the chip dragged back into a rail takes a place in it
chip = (640 + round(st["pillWidth"] / 2) + 12 + 40, 762)
mid.clear()


def over_the_left_rail():
    mid["state"] = desk_state()
    shot("45-drag-chip-over-left-rail")


drag(line(chip, (150, 500)), over_the_left_rail)
s = mid["state"]
check("a chip in hand shows where it would land", s.get("drag") and s["drag"]["from"] == "strip"
      and s["drag"]["id"] == "machine" and s["dropTarget"] and s["dropTarget"]["side"] == "left", (s.get("drag"), s.get("dropTarget")))
check("and the shell puts the pointer where it is on the screen, from the bar's window",
      s.get("drag") and abs(s["drag"]["x"] - 150) <= 2 and abs(s["drag"]["y"] - 500) <= 2, s.get("drag"))
st = None
for _ in range(40):
    st = desk_state()
    if st.get("rails", {}).get("machine") == "left":
        break
    time.sleep(0.1)
shot("46-drag-chip-dropped")
check("dropped on the left rail it is a card again, there",
      st and st["rails"]["machine"] == "left" and st["stripped"] == [] and st["faces"]["machine"] == "full"
      and st["slots"]["machine"]["side"] == "left", st and (st["rails"], st["stripped"], st["faces"]))

# a drop where nothing can be dropped changes nothing and sends nothing
n = mark()
before = (st["rails"], st["order"], st["stripped"])
grab = (st["slots"]["needs"]["x"] + 60, st["slots"]["needs"]["y"] + 22)
drag(line(grab, (640, 300)))
time.sleep(0.5)
st = desk_state()
check("a card let go in the middle of the screen stays where it was",
      (st["rails"], st["order"], st["stripped"]) == before and st.get("drag") is None
      and wait(lambda m: m.get("type") == "desk", 0.5, n) is None, (st["order"], st.get("drag")))

# put the desk back as it was, for what follows
send({"type": "desk", "op": "move", "widget": "machine", "rail": "right"})
send({"type": "desk", "op": "move", "widget": "watching", "rail": "left", "rank": 1})
inject({"type": "jobs", "jobs": []})
inject({"type": "dev", "sessions": [], "attention": [], "front": "", "line": ""})
inject({"type": "machine", "present": False, "asked": False, "why": "", "strip": {"text": "", "dot": ""}, "rows": []})
time.sleep(0.8)
st = desk_state()
check("and the desk is as it was", st["order"] == {"left": ["now", "watching", "alive"], "right": ["needs", "away", "machine"]}
      and st["stripped"] == [], (st["order"], st["stripped"]))

# 13. Away: the card that says what happened while the person was gone. The turn log is agentd's own file, so the
# section writes the rows a stretch away would have left (a turn that failed, a job that failed, a turn that went
# well), asks for the card by name, puts it away with a tap of the virtual pointer, and asks again.
turn_log = Path.home() / ".local" / "state" / "bombadil" / "turns.jsonl"
left = time.time()
with turn_log.open("a") as f:
    for row in ({"t": left - 6, "prompt": "build the site", "ok": False, "result": "the build broke\nmore", "n": 901},
                {"t": left - 5, "kind": "local", "prompt": "background job", "action": "job",
                 "target": "Build the image", "result": "Build the image failed: no network", "ok": False,
                 "job": "0a0b0c"},
                {"t": left - 4, "prompt": "tidy the notes", "ok": True, "result": "", "n": 902}):
        f.write(json.dumps(row) + "\n")
summon()
typ("show away")
before = api_requests()
n = mark()
key("Return")
asked = wait(ev("local", phase="done"), 10, n)
check("asking for what happened while away answers in a line, with no model", asked and asked.get("ok")
      and asked.get("text") == "Here is what happened while you were away." and api_requests() == before,
      asked and asked.get("text"))
key("Escape")
time.sleep(1.5)
st = desk_state()
shot("47-desk-away")
aw = st.get("away") or {}
slot = (st.get("slots") or {}).get("away") or {}
check("the Away card is up in full, on the right rail", st["present"]["away"] and st["faces"]["away"] == "full"
      and slot.get("side") == "right", (st.get("faces"), slot))
check("it says what failed first, then what went well, and why it is there",
      [r["key"] for r in aw.get("rows", [])][:3] == ["job:0a0b0c", "turn:901", "turn:902"]
      and aw["rows"][0]["button"] == "Why?" and aw["rows"][1]["sub"] == "the build broke"
      and aw["why"].startswith("since ") and aw["why"].endswith("puts this away"), aw)
box = (slot.get("x", 0), slot.get("y", 0), slot.get("w", 0), slot.get("h", 0))
dots = {name: region_pixels("47-desk-away", box, colour) for name, colour in
        (("red", "#d05555"), ("green", "#5fb36b"))}
check("a failure is drawn red and what went well green", dots["red"] > 40 and dots["green"] > 20, dots)
pointer(f"move {slot['x'] + 60} {slot['y'] + 22}")           # the title
time.sleep(0.3)
pointer("press")
time.sleep(0.15)
pointer("release")
time.sleep(1.0)
st = desk_state()
check("a tap on the card puts it away, and nothing reached the model",
      st["faces"]["away"] == "hidden" and not st["present"]["away"] and st.get("away") is None
      and api_requests() == before, (st.get("faces"), st.get("away")))
summon()
typ("show away")
n = mark()
key("Return")
wait(ev("local", phase="done"), 10, n)
key("Escape")
time.sleep(1.5)
st = desk_state()
shot("48-desk-away-nothing")
check("what was seen is not news again: asked once more it says nothing happened",
      [r["key"] for r in (st.get("away") or {}).get("rows", [])] == ["none"], st.get("away"))
summon()
typ("how's the machine?")
n = mark()
key("Return")
wait(ev("local", phase="done"), 10, n)
key("Escape")
time.sleep(1.0)
st = desk_state()
check("the person's next ask puts it away too", st["faces"]["away"] == "hidden" and st.get("away") is None,
      (st.get("faces"), st.get("away")))
# The shell's half, with a card agentd did not build: five rows, three colours and a count of the rest.
inject({"type": "away", "since": time.time() - 3600, "count": 7, "why": "since 09:05 · a click puts this away",
        "rows": [
            {"key": "turn:7", "kind": "dot", "title": "build the site", "sub": "the build broke", "tone": "red", "button": ""},
            {"key": "dev:rev", "kind": "dot", "title": "reviewer on Bombadil", "sub": "apply the migration?",
             "tone": "sessions", "button": "Open"},
            {"key": "job:0a0b0c", "kind": "dot", "title": "Build the image", "sub": "no network", "tone": "red",
             "button": "Why?"},
            {"key": "turn:5", "kind": "dot", "title": "tidy the notes", "sub": "at 09:05", "tone": "ok", "button": ""},
            {"key": "more", "kind": "plain", "title": "and 3 more", "sub": "", "tone": "you", "button": ""}]})
time.sleep(1.5)
st = desk_state()
shot("49-desk-away-rows")
aw = st.get("away") or {}
slot = (st.get("slots") or {}).get("away") or {}
check("a card of five rows is drawn in full, and its chip would say seven things",
      st["faces"]["away"] == "full" and len(aw.get("rows", [])) == 5 and slot.get("h") == 50 + 5 * 44 + 18
      and aw["strip"]["text"] == "back · 7 things", (st.get("faces"), slot, aw.get("strip")))
box = (slot.get("x", 0), slot.get("y", 0), slot.get("w", 0), slot.get("h", 0))
dots = {name: region_pixels("49-desk-away-rows", box, colour) for name, colour in
        (("red", "#d05555"), ("blue", "#5b9bd5"), ("green", "#5fb36b"))}
check("it draws a dot in each colour, and a button on the two rows that have one",
      dots["red"] > 100 and dots["blue"] > 40 and dots["green"] > 20, dots)
inject({"type": "away", "since": 0, "count": 0, "why": "", "rows": []})
time.sleep(0.8)
st = desk_state()
check("and agentd saying there is none takes it away", st["faces"]["away"] == "hidden" and st.get("away") is None,
      st.get("faces"))

# 10. Mail's notices: another service's line takes the idle line above the pill, with chips, and the
# cross puts it away. First a message as agentd would send it (injected), then the real chain: the mail
# service on its fake engine says new mail, agentd turns it into a notice, the bar shows it.
def pill_state():
    r = run("quickshell", "ipc", "-p", str(REPO / "shell" / "shell.qml"), "call", "line", "state")
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


def until_state(pred, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = pill_state()
        if st and pred(st):
            return st
        time.sleep(0.3)
    return pill_state()


key("Escape")
until_state(lambda st: st.get("mode") == "idle", 15)
inject({"type": "notice", "id": 901, "source": "mail", "line": "Priya Shah: Launch date", "tone": "ask",
        "actions": [{"id": "reply", "label": "Reply", "style": "primary"},
                    {"id": "open", "label": "Open", "style": "quiet"}], "ttl": 300, "at": time.time()})
st = until_state(lambda st: st.get("noticeShown"))
time.sleep(0.6)
shot("27-notice-new-mail")
check("a notice takes the idle line, with its two chips", st.get("noticeShown") and [
    (n["line"], [a["label"] for a in n["actions"]]) for n in st["notices"]] == [("Priya Shah: Launch date", ["Reply", "Open"])], st.get("notices"))
inject({"type": "notice", "id": 902, "source": "mail", "line": "I can't tell whether that went. Look in Sent before you press Send again.",
        "tone": "error", "actions": [], "ttl": 0, "at": time.time()})
inject({"type": "notice", "id": 903, "source": "mail", "line": "Leo Park: Quick question", "tone": "ask",
        "actions": [{"id": "reply", "label": "Reply", "style": "primary"}], "ttl": 300, "at": time.time()})
st = until_state(lambda st: len(st.get("notices", [])) == 3)
time.sleep(0.6)
shot("28-notice-warning-ahead-of-news")
check("a warning stays ahead of newer news", [n["id"] for n in st["notices"]] == [902, 903, 901], [n["id"] for n in st.get("notices", [])])
for nid in (901, 902, 903):
    inject({"type": "notice_end", "id": nid})
st = until_state(lambda st: not st.get("notices"))
check("agentd ending them clears the line", not st.get("notices") and not st.get("noticeShown"), st.get("notices"))

# The fake's drip says Leo, Priya, then two senders nobody knows (no notice), and agentd's watch starts a few
# seconds after the service: whichever of the two known senders comes first is the first notice.
KNOWN_SENDERS = ("Leo Park: ", "Priya Shah: ")
env.update(BOMBADIL_MAIL_ENGINE="fake", BOMBADIL_MAIL_FAKE_DRIP="2")
mail_proc = start("mail", [str(REPO / "bin" / "bombadil-mail")])
n = mark()
got = wait(lambda m: m.get("type") == "notice" and m.get("source") == "mail" and m["id"] != 901, 60, n)
check("new mail from the fake service reaches the bar's socket as a notice with Reply and Open",
      got and [a["id"] for a in got["actions"]] == ["reply", "open"] and got["line"].startswith(KNOWN_SENDERS), got)
st = until_state(lambda st: st.get("noticeShown"), 20)
time.sleep(0.6)
shot("29-notice-from-the-mail-service")
check("and the bar shows that line", st.get("noticeShown") and any(
    n["line"].startswith(KNOWN_SENDERS) for n in st.get("notices", [])), st.get("notices"))
mail_proc.terminate()   # no more mail; what is live is put away the way the cross does
live = [n["id"] for n in st.get("notices", [])]
for nid in live:
    send({"type": "notice_dismiss", "id": nid})
ended = wait(lambda m: m.get("type") == "notice_end" and m.get("id") in live, 10, n)
st = until_state(lambda st: not st.get("notices"))
check("a dismissal reaches agentd and the line goes", ended and not st.get("notices"), (ended, st.get("notices")))

# `desk inject` is open to any process of the person's, an agent's shell included, so a bar that was not started for
# a test takes no notice and no end of one from it (the other kinds still go in, as they always did).
qs_proc.terminate()
qs_proc.wait(10)
plain = {k: v for k, v in env.items() if k != "BOMBADIL_BAR_INJECT"}
with open(OUT / "quickshell-plain.log", "w") as log:
    plain_bar = subprocess.Popen([str(REPO / "bin" / "bombadil-shell")], env=plain, stdout=log,
                                 stderr=subprocess.STDOUT)
procs.append(plain_bar)
until_state(lambda st: "mode" in st, 30)
inject({"type": "notice", "id": 950, "source": "mail", "line": "Sent to Priya from maya@acme.example · 09:08",
        "tone": "done", "actions": [], "ttl": 0, "at": time.time()})
inject({"type": "notice_end", "id": 950})
inject({"type": "event", "kind": "local", "action": "panel", "phase": "done", "ok": True, "text": "Opened the browser."})
st = until_state(lambda st: st.get("line") == "Opened the browser.", 10)
check("a bar not started for a test takes no injected notice, and still takes the other kinds",
      st.get("line") == "Opened the browser." and st.get("notices") == [] and not st.get("noticeShown"), st)
plain_bar.terminate()   # the sections after this start their own bar
plain_bar.wait(10)

# Quickshell logs a QML error as a warning and carries on (a colour left undefined draws white), so
# none of the checks above would notice one.
qml_errors = [ln for ln in re.sub(r"\x1b\[[0-9;]*m", "", (OUT / "quickshell.log").read_text()).splitlines()
              if re.search(r"\.qml\[|\.qml:\d+|Unable to assign|is not defined|TypeError|ERROR", ln)]
check("the shell logged no QML errors", not qml_errors, "; ".join(qml_errors[:3]))

# 31. Messages and connections (docs/CONNECT.md): the real chain end to end. The connection service runs on its fake
# engine (Acme and Linear, started above); the bar's own NotificationServer owns the session bus's notification
# name; a virtual pointer and wtype are the person's hands. Nothing is injected into the bar's own state: a
# notification is posted on the bus, a message is said to the service, and the person's Reply, typing and Send are
# real clicks and keys.
def click(x, y):
    pointer(f"move {x} {y}")
    time.sleep(0.3)
    pointer("press")
    time.sleep(0.1)
    pointer("release")
    time.sleep(0.6)


def say_to_connect(op, **args):
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(xdg / "connect" / "connect.sock"))
    s.sendall((json.dumps({"id": 1, "op": op, **args}) + "\n").encode())
    answer = json.loads(s.makefile("r").readline())
    s.close()
    return answer


def notify(app, summary, body):
    """A page's notification, as a browser posts it on the session bus."""
    return run("busctl", "--user", "call", "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
               "org.freedesktop.Notifications", "Notify", "susssasa{sv}i", app, "0", "", summary, body, "0", "0", "30000")


# A task card is built and checked here the way agentd does it.
sys.path.insert(0, str(REPO / "src"))
from bombadil import cards, paths
from bombadil.connect import cardmake

key("Escape")
connect_bar = start("quickshell-connect", [str(REPO / "bin" / "bombadil-shell")])
until_state(lambda st: "mode" in st, 30)
time.sleep(2)
r = run("busctl", "--user", "status", "org.freedesktop.Notifications")
check("the bar owns the notification name on the session bus (nothing else does)", "Comm=quickshell" in r.stdout,
      (r.stdout[:200] + r.stderr[:200]).replace("\n", " "))

# a web page's notification becomes the line above the pill, with Reply and Open
n = mark()
notify("Teams", "Priya Shah", "Legal just signed off")
got = wait(lambda m: m.get("type") == "notice" and m.get("source") == "notify", 10, n)
check("a notification posted on the session bus reaches agentd and comes back as a notice with Reply and Open",
      got and got["line"] == "Priya Shah: Legal just signed off" and [a["id"] for a in got["actions"]] == ["reply", "open"], got)
st = until_state(lambda st: st.get("noticeShown"))
time.sleep(0.6)
shot("50-notification-notice")
check("and the bar shows it", st.get("noticeShown") and [x["line"] for x in st["notices"]] == ["Priya Shah: Legal just signed off"],
      st.get("notices"))

# Reply (a real click on the chip) opens a card whose box takes the keys; Copy and open puts the words on the
# clipboard and is no send
pointer("move 956 704")
time.sleep(0.3)
click(956, 704)
c = wait(lambda m: m.get("type") == "card" and m["card"].get("source") == "notification", 10, n)
st = until_state(lambda st: st.get("card") and st["card"]["type"] == "message")
check("Reply on that line opens a card of the notification, with a box and Copy and open (no draft, no ring)",
      c and c["card"]["reply"]["kind"] == "copy" and st.get("card") and st["card"]["draft"]["proposal"] == "", (c, st.get("card")))
typ("Thanks, will do.")
time.sleep(1)
shot("51-notification-reply")
n = mark()
click(444, 669)
inv = wait(lambda m: m.get("type") == "notification_invoke", 10, n)
pasted = run("wl-paste", "--no-newline")
check("Copy and open puts what was typed on the clipboard (the keys went to the card's box) and brings the page forward",
      inv is not None and pasted.stdout == "Thanks, will do.", (inv, pasted.stdout, pasted.stderr))
key("Escape")
time.sleep(1)

# a message in the service: the notice, the card, a draft that lights only when it is what was drawn, the person's
# click, one receipt, and a second click that does nothing
n = mark()
check("the service takes a message", say_to_connect("fake_inject", message={
    "from": {"id": "U0PRIYA", "name": "Priya Shah"}, "text": "Legal just signed off. Can we lock the 14th for the launch?",
    "ts": time.time()}).get("ok") is True)
got = wait(lambda m: m.get("type") == "notice" and m.get("source") == "connect", 15, n)
check("it reaches the bar as a notice with Reply and Open", got and got["line"].startswith("Priya Shah: Legal just signed off")
      and [a["id"] for a in got["actions"]] == ["reply", "open"], got)
until_state(lambda st: st.get("noticeShown"))
time.sleep(0.6)
shot("52-message-notice")
click(956, 704)
c = wait(lambda m: m.get("type") == "card" and m["card"].get("type") == "message" and m["card"].get("source") == "connect", 10, n)
st = until_state(lambda st: st.get("card") and st["card"]["draft"] and st["card"]["draft"]["proposal"])
check("Reply opens the message card with an empty draft of the person's own that is not lit",
      c and c["card"]["reply"]["by"] == "person" and st["card"]["draft"]["state"] == "open" and st["card"]["draft"]["ready"] is False,
      st.get("card"))
time.sleep(1)
shot("53-message-card")
typ("Yes, lock the 14th.")
st = until_state(lambda st: st.get("card") and st["card"]["draft"] and st["card"]["draft"]["ready"], 10)
time.sleep(0.8)
shot("54-message-ready")
check("what was typed lights the button only once agentd has the draft and the card has drawn it",
      st.get("card") and st["card"]["draft"]["ready"] is True, st.get("card"))
n = mark()
click(411, 668)
res = wait(lambda m: m.get("type") == "notice" and m.get("source") == "connect"
           and "Replied to Priya in a direct message" in m.get("line", ""), 10, n)
check("a click on Send is one press, and the receipt is a notice saying where it went",
      res and res["tone"] == "done" and res["line"].startswith("Replied to Priya in a direct message"), res)
time.sleep(1.2)
shot("55-message-sent")
n = mark()
click(411, 668)
time.sleep(1.5)
check("a second click on the same place sends nothing more",
      not [m for m in events[n:] if m.get("type") == "notice" and "Replied" in m.get("line", "")],
      [m for m in events[n:] if m.get("type") in ("press_result", "notice")])
key("Escape")
time.sleep(1)

# the next card does not inherit what the last one said
n = mark()
say_to_connect("fake_inject", message={"from": {"id": "U0MARCUS", "name": "Marcus Webb"},
                                       "text": "Can you look at the pricing copy before noon?", "ts": time.time()})
got = wait(lambda m: m.get("type") == "notice" and m.get("source") == "connect" and m["line"].startswith("Marcus Webb"), 15, n)
until_state(lambda st: st.get("noticeShown"))
click(956, 704)
st = until_state(lambda st: st.get("card") and st["card"]["draft"] and st["card"]["draft"]["proposal"] not in ("", "p1"))
time.sleep(1)
shot("56-next-card")


def reply_box(name):
    """Where the card's reply box is in a screenshot: its dark inside is a run of pixels down the box's right side,
    whatever stands above the card (a receipt line, a chip row) has moved it to."""
    from PySide6.QtGui import QImage
    img, run, on_card = QImage(str(OUT / f"{name}.png")), [], False
    for y in range(380, 740):
        c = img.pixelColor(892, y)
        if abs(c.red() - 25) < 4 and abs(c.green() - 29) < 4 and abs(c.blue() - 34) < 4:
            on_card = True          # (the page behind the card is darker than the box is, so start from the card)
        elif on_card and abs(c.red() - 13) < 4 and abs(c.green() - 13) < 4 and abs(c.blue() - 17) < 4:
            run.append(y)
        elif len(run) >= 40:
            break
        else:
            run = []
    return (390, run[0] + 4, 500, run[-1] - run[0] - 8) if len(run) >= 40 else None     # (x, y, w, h)


where_box = reply_box("56-next-card")
box = region_pixels("56-next-card", where_box, "#e6e6e6", tol=60) if where_box else -1
check("a new reply to someone else opens with an empty box (no words of the one before)",
      st.get("card") and st["card"]["draft"]["ready"] is False and 0 <= box < 40, (st.get("card"), box, where_box))
words_box = reply_box("54-message-ready")     # the same look at the box that had words in it finds them
check("and looking at a box that holds words does find them (the look has teeth)",
      words_box is not None and region_pixels("54-message-ready", words_box, "#e6e6e6", tol=60) > 100, words_box)
key("Escape")
time.sleep(1)

# the launcher's words: unread, and what is connected, with no model
summon()
typ("unread")
key("Return")
c = wait(lambda m: m.get("type") == "card" and m["card"].get("type") == "list", 10, n)
time.sleep(1.2)
shot("57-unread")
check("the word 'unread' opens the list of what waits, without the model",
      c and c["card"]["id"] == "list:unread" and len(c["card"]["rows"]) >= 2, c and c["card"].get("rows"))
key("Escape")
time.sleep(0.8)
summon()
typ("connections")
key("Return")
c = wait(lambda m: m.get("type") == "card" and m["card"].get("type") == "list" and m["card"].get("id") != "list:unread", 10, n)
time.sleep(1.2)
shot("58-connected-here")
rows = c["card"]["rows"] if c else []
check("the word 'connections' opens Connected here: Slack and Linear, no token or key anywhere on it",
      c and any("Acme" in r["title"] or "Slack" in r["title"] for r in rows) and any("Linear" in r["title"] for r in rows)
      and "xox" not in json.dumps(c) and "xapp" not in json.dumps(c), rows)
key("Escape")
time.sleep(0.8)

# the agent's side, through the real CLI and the real os-mcp server (the API is scripted): it reads what is unread and
# drafts a reply; the draft is on a card for the person, lit only by what the card drew, and nothing has gone until
# the person's own click
def find_colour(name, colour, box, tol=10):
    """The middle of the pixels of this colour in the box of a screenshot (None when there are none)."""
    from PySide6.QtGui import QColor, QImage
    want, img, xs, ys = QColor(colour), QImage(str(OUT / f"{name}.png")), [], []
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = img.pixelColor(x, y)
            if abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < tol * 3:
                xs.append(x)
                ys.append(y)
    return (sum(xs) // len(xs), sum(ys) // len(ys)) if xs else None


n = mark()
summon()
typ("answer priya, say yes")
key("Return")
c = wait(lambda m: m.get("type") == "card" and m["card"].get("type") == "message"
         and m["card"].get("reply", {}).get("by") == "agent", 90, n)
check("the agent read the messages and its draft arrived as a card of its own, with the words it wrote",
      c and c["card"]["reply"]["content"] == "Yes, the 14th works for us." and c["card"]["reply"]["kind"] == "slack_reply", c)
end = wait(ev("turn_end"), 60, n)
check("the turn ended on its own words and nothing in it sent", end is not None and not end.get("stopped"), end and end.get("summary"))
st = until_state(lambda st: st.get("card") and st["card"]["draft"] and st["card"]["draft"]["ready"], 15)
time.sleep(1)
shot("60-agent-draft")
check("what the agent wrote is lit for the person's press once the card has drawn it, and sent nothing yet",
      st.get("card") and st["card"]["draft"]["ready"] is True and st["card"]["draft"]["state"] == "open", st.get("card"))
turn_log = paths.turns_log()
turns = [json.loads(ln) for ln in turn_log.read_text().splitlines()] if turn_log.exists() else []
check("the turn log keeps no message text", "Can we lock the 14th" not in json.dumps(turns) and "Priya Shah" not in json.dumps(turns),
      json.dumps(turns)[-300:])
where = find_colour("60-agent-draft", "#d97757", (360, 440, 450, 735))
n = mark()
if where:
    click(*where)
res = wait(lambda m: m.get("type") == "notice" and m.get("source") == "connect" and "Posted in #launch" in m.get("line", ""), 10, n)
check("the person's click on Send is the one thing that sends it", where and res and res["tone"] == "done", (where, res))
key("Escape")
time.sleep(1)

# a task card, built the way agentd builds one, drawn by the real shell
card = cardmake.task_card(service="linear", task={
    "ref": "linear:LIN-42", "service": "linear", "connection": "mcp:linear", "title": "Review the launch checklist",
    "why": "assigned to you", "status": "In progress", "due": "2026-10-03", "url": "https://linear.acme.test/issue/LIN-42",
    "fields": [{"key": "team", "label": "Team", "value": "Launch"}]},
    task_fields={"comment": [{"key": "text", "label": "Comment", "edit": "text", "required": True}]})
card, errors = cards.accept(card, now=time.time())
check("agentd's own check takes the card it built", card is not None, errors)
inject({"type": "card", "card": card})
st = until_state(lambda st: st.get("card") and st["card"]["type"] == "task")
time.sleep(1.2)
shot("59-task-card")
check("a task card is drawn by the shell, with its comment box and no lit button",
      st.get("card") and st["card"]["type"] == "task" and not st["card"]["draft"]["ready"], st.get("card"))
key("Escape")

qml_errors = [ln for ln in re.sub(r"\x1b\[[0-9;]*m", "", (OUT / "quickshell-connect.log").read_text()).splitlines()
              if re.search(r"\.qml\[|\.qml:\d+|Unable to assign|is not defined|TypeError|ERROR", ln)]
check("the shell logged no QML errors while connections were on screen", not qml_errors, "; ".join(qml_errors[:3]))
connect_bar.terminate()
connect_bar.wait(10)

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

# 10. Coding sessions: the real Claude Code in zellij, a window that is only a viewer, the dots.
home = Path.home()
proj = home / "Projects" / "spike"
proj.mkdir(parents=True, exist_ok=True)
run("git", "init", "-q", "-b", "main", str(proj))
run("git", "-C", str(proj), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "one")
cfg_path = home / ".claude.json"
try:
    cfg = json.loads(cfg_path.read_text())
except (OSError, ValueError):
    cfg = {}
cfg.update(hasCompletedOnboarding=True, theme="dark", numStartups=5,
           customApiKeyResponses={"approved": ["sk-ant-fake"], "rejected": []})
cfg.setdefault("projects", {}).update({str(p): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}
                                       for p in (proj, home / "Projects" / ".work" / "spike" / "reviewer")})
cfg_path.write_text(json.dumps(cfg))


def zscreen(name):
    return run("zellij", "-s", name, "action", "dump-screen", "-p", "terminal_0").stdout


def zlisted():
    return run("zellij", "list-sessions", "--short", "--no-formatting").stdout.split()


def devmsg(pred, timeout=20, since=0):
    return wait(lambda m: m.get("type") == "dev" and pred(m), timeout, since)


def states(m):
    return {s["key"]: s["state"] for s in m.get("sessions", [])}


def viewer_open(app_id):
    return f'"app_id": "{app_id}"' in run("swaymsg", "-t", "get_tree").stdout


# a. "claude spike" starts the unchanged TUI in the project, in a window of its own.
summon()
typ("claude spike")
time.sleep(0.3)
shot("30-claude-spike-typed")
n = mark()
t0 = time.monotonic() + 0.2
key("Return")
o = wait(ev("local", phase="done"), 20, n)
results["timings"]["session_start_s"] = round(o["_t"] - t0, 3) if o else None
check("claude spike started from the pill", o and o.get("ok") and o.get("text") == "Started claude on Spike.", o)
check("its window opened", until(lambda: viewer_open("bombadil-session-spike-claude"), 15))
panes = run("zellij", "-s", "bombadil-spike-claude", "action", "list-panes", "--all", "--json").stdout
check("only the tool shows, no zellij bars", "tab-bar" not in panes and "status-bar" not in panes, panes[-300:])
check("Claude Code runs in the project", until(lambda: "claude on Spike" in zscreen("bombadil-spike-claude"), 30),
      zscreen("bombadil-spike-claude")[-400:])
time.sleep(1)
shot("31-claude-tui")

# b. Typed into the window, it works; its hooks move the dot; it finishes and is your turn.
n = mark()
typ("tell me a joke")
key("Return")
w = devmsg(lambda m: states(m).get("spike/claude") == "working", 20, n)
check("its dot moves while it works", w is not None)
d = devmsg(lambda m: states(m).get("spike/claude") == "done", 30, n)
check("its dot is lit when it finishes", d is not None and "finished" in (d or {}).get("line", ""), d and d.get("line"))
time.sleep(1)
shot("32-joke-done-dot-lit")
try:
    geo = json.loads(run("quickshell", "ipc", "-p", str(REPO / "shell" / "shell.qml"), "call", "dev", "chips").stdout)
except ValueError:
    geo = {}
check("the bar shows the project's chip above the pill's left end",
      geo.get("visible") and geo["width"] > 40 and geo["height"] == 28 and geo["y"] + geo["height"] <= geo["pillY"]
      and geo["pillX"] <= geo["x"] < geo["pillX"] + geo["pillWidth"] / 2, geo)

# c. Shift+Enter reaches Claude Code through foot and zellij: a newline, not a send.
before = api_requests()
typ("line one")
run("wtype", "-s", "200", "-M", "shift", "-k", "Return", "-m", "shift")
time.sleep(0.3)
typ("line two")
time.sleep(1)
scr = zscreen("bombadil-spike-claude")
check("Shift+Enter makes a new line in Claude Code", "line one" in scr and "line two" in scr and api_requests() == before,
      scr[-300:])
shot("33-shift-enter")

# d. Closing the window only closes the viewer.
run("swaymsg", '[app_id="bombadil-session-spike-claude"] kill')
check("the window closed", until(lambda: not viewer_open("bombadil-session-spike-claude"), 10))
time.sleep(2)
check("the session runs on", "bombadil-spike-claude" in zlisted(), zlisted())
shot("34-window-closed-dot-stays")

# e. Its name brings it back mid-sentence.
summon()
typ("claude spike")
n = mark()
key("Return")
o = wait(ev("local", phase="done"), 20, n)
check("typing its name brings it back", o and o.get("text") == "Back to claude on Spike.", o)
check("in a new window", until(lambda: viewer_open("bombadil-session-spike-claude"), 15))
time.sleep(1.5)
scr = zscreen("bombadil-spike-claude")
check("mid-sentence: the unsent lines and the joke are still there",
      "line two" in scr and "penguins" in scr, scr[-300:])
shot("35-back-mid-sentence")

# f. A second session on the repository gets its own copy, and asks a question.
summon()
typ("claude spike reviewer")
n = mark()
key("Return")
o = wait(ev("local", phase="done"), 20, n)
copy = home / "Projects" / ".work" / "spike" / "reviewer"
check("a second session gets its own copy", o and "in its own copy" in (o.get("text") or "") and copy.is_dir(), o)
until(lambda: "reviewer on Spike" in zscreen("bombadil-spike-reviewer"), 30)
n = mark()
typ("ask me")
key("Return")
a = devmsg(lambda m: states(m).get("spike/reviewer") == "asked", 30, n)
check("a question lights its dot and becomes the pill's line",
      a and a.get("line") == "reviewer on Spike: Run the migration on the local database?", a and a.get("line"))
st = desk_state()
check("and the stone knocks for it, the same message the desk's Needs you reads",
      st.get("needsYou") and st.get("face") == "needs", {k: st.get(k) for k in ("needsYou", "face")})
run("swaymsg", '[app_id="bombadil-session-spike-reviewer"] kill')
time.sleep(1.5)
shot("36-your-turn-line")
summon()
key("Tab")
time.sleep(1)
nx = wait(ev("local", action="session", phase="done"), 10, n)
check("Tab from the empty pill goes to the session waiting for you",
      nx and nx.get("text") == "Back to reviewer on Spike." and until(
          lambda: viewer_open("bombadil-session-spike-reviewer"), 10), nx)
time.sleep(1)
shot("37-tab-to-reviewer")
key("Return")   # answer its question in its own window
time.sleep(3)
check("answered, the stone stops knocking", until(lambda: not desk_state().get("needsYou"), 20), desk_state().get("face"))

# g. agentd restarting touches no session.
agentd = next(p for p in procs if p.args[0].endswith("agentd"))
agentd.terminate()
agentd.wait(5)
procs.remove(agentd)
time.sleep(1)
start("agentd-2", [str(REPO / "bin" / "agentd")])
until(lambda: sock_path.exists(), 10)
time.sleep(4)
threading.Thread(target=listen, args=("a",), daemon=True).start()
check("sessions outlive an agentd restart", {"bombadil-spike-claude", "bombadil-spike-reviewer"} <= set(zlisted()),
      zlisted())
time.sleep(2)
shot("38-after-agentd-restart")

# h. "end reviewer" ends it and keeps its copy.
summon()
typ("end reviewer")
n = mark()
key("Return")
e = wait(ev("local", phase="done"), 20, n)
check("end reviewer ends it and keeps its copy",
      e and e.get("text") == "Ended reviewer on Spike. Its copy is kept." and until(
          lambda: "bombadil-spike-reviewer" not in zlisted(), 10) and copy.is_dir(), e)
time.sleep(1)
shot("39-ended")

(OUT / "results.json").write_text(json.dumps(results, indent=2))
for p in procs[::-1]:
    p.terminate()
fails = [c for c in results["checks"] if not c["ok"]]
print(f"DONE pass={len(results['checks']) - len(fails)} fail={len(fails)}", flush=True)
