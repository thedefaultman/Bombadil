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

# 7. Coding sessions: the real Claude Code in zellij, a window that is only a viewer, the dots.
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


def until(pred, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


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
