"""What the desktop costs while nothing is happening: the bar, agentd and the brain left alone in headless sway.

Runs inside the test container after driver.py (tests/desktop/inside.sh) as an ordinary user. A session that
is doing nothing must cost close to nothing, on a laptop's battery as much as in a virtual machine, so this
watches two things in each of a few quiet scenes:

  - the share of one core the stack uses (/proc, user + system time of the bar, agentd and the brain), and
  - how often the bar tells the compositor it drew a frame (`WAYLAND_DEBUG=client`, the `commit` requests).

The second is the sharper of the two. A bar that redraws once a second costs a hundredth of a core here,
where sway draws in memory, and several cores on a machine whose compositor draws in software (the cost is
the compositor's, not the bar's); the number of commits is the same on both. Every regression found in a real
virtual machine so far (docs/IDLE-COST.md) shows in it: the seconds counter drawing behind a setup line, the
stone's knock that never ended, a window Qt kept redrawing because the compositor answered its frame late.

Prints PASS or FAIL for each check and "IDLE-DONE pass=N fail=M" last; tests/desktop/run.sh looks for it.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/repo")
OUT = Path("/out")
OUT.mkdir(exist_ok=True)
SETTLE = float(os.environ.get("IDLE_SETTLE", 30))   # seconds from starting the bar to the first sample
WINDOW = float(os.environ.get("IDLE_WINDOW", 20))   # seconds each sample covers
CORE_LIMIT = float(os.environ.get("IDLE_CORE_PERCENT", 10))   # of one core, the whole stack together
COMMITS_PER_SECOND = float(os.environ.get("IDLE_COMMITS_PER_SECOND", 0.25))   # per window of the bar, on average
CLK = os.sysconf("SC_CLK_TCK")

xdg = Path(f"/tmp/xdg-idle-{os.getuid()}")
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
    BOMBADIL_BAR_INJECT="1",
    BOMBADIL_VITALS="0",   # the Machine card is injected below; the real machine must not answer over it
    QT_QUICK_BACKEND="software",
    LANG="C.UTF-8",
    PATH=f"{REPO}/bin:/usr/local/bin:/usr/bin:/bin",
)
env.pop("DISPLAY", None)
env.pop("QT_WAYLAND_FRAME_CALLBACK_TIMEOUT", None)   # the launcher's own default is part of what is tested
procs = {}
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def start(name, argv, **extra):
    log = open(OUT / f"idle-{name}.log", "w")
    procs[name] = subprocess.Popen(argv, env=dict(env, **extra), stdout=log, stderr=subprocess.STDOUT)
    return procs[name]


def ticks(pid):
    """User + system clock ticks of each thread of a process, by thread."""
    spent = {}
    try:
        for task in os.listdir(f"/proc/{pid}/task"):
            raw = Path(f"/proc/{pid}/task/{task}/stat").read_text()
            fields = raw[raw.rindex(")") + 2:].split()
            spent[task] = int(fields[11]) + int(fields[12])
    except OSError:
        pass
    return spent


def used(before, after):
    """Ticks spent between two readings (a thread that started in between counts whole)."""
    return sum(max(0, n - before.get(task, 0)) for task, n in after.items())


def commits(path, start_at):
    """Frames the bar reported since this byte of the Wayland trace, per surface."""
    with open(path, errors="replace") as f:
        f.seek(start_at)
        text = f.read()
        end = f.tell()
    per = {}
    for surface in re.findall(r"->\s+wl_surface#(\d+)\.commit\(\)", text):
        per[surface] = per.get(surface, 0) + 1
    return per, end


def ipc(target, *args):
    return subprocess.run(["quickshell", "ipc", "-p", str(REPO / "shell" / "shell.qml"), "call", target, *args],
                          env=env, capture_output=True, text=True)


trace = OUT / "idle-wayland-trace.log"


def sample(label):
    """Leave everything alone for WINDOW seconds; check what it cost."""
    watched = {name: procs[name].pid for name in ("quickshell", "agentd", "brain") if name in procs}
    before = {name: ticks(pid) for name, pid in watched.items()}
    sway_before = ticks(procs["sway"].pid)
    mark = trace.stat().st_size
    t0 = time.monotonic()
    time.sleep(WINDOW)
    dt = time.monotonic() - t0
    share = {name: used(before[name], ticks(pid)) / CLK / dt * 100 for name, pid in watched.items()}
    sway = used(sway_before, ticks(procs["sway"].pid)) / CLK / dt * 100
    per, _ = commits(trace, mark)
    rate = sum(per.values()) / dt
    busiest = max(per.values(), default=0) / dt
    where = ", ".join(f"{name} {pct:.1f}%" for name, pct in share.items())
    check(f"idle, {label}: the bar, agentd and the brain use under {CORE_LIMIT:g}% of a core",
          sum(share.values()) < CORE_LIMIT, f"{sum(share.values()):.1f}% ({where}; the compositor {sway:.1f}%)")
    check(f"idle, {label}: the bar draws a frame less than {COMMITS_PER_SECOND:g} times a second",
          busiest < COMMITS_PER_SECOND, f"{rate:.2f} a second in all, {busiest:.2f} at most for one window; {dict(per)}")


# -- start the stack: sway, agentd, the brain and the bar, and then nothing --
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
start("brain", [str(REPO / "bin" / "bombadil-brain")])
with open(trace, "w") as traced:
    procs["quickshell"] = subprocess.Popen([str(REPO / "bin" / "bombadil-shell")], env=dict(env, WAYLAND_DEBUG="client"),
                                           stdout=subprocess.DEVNULL, stderr=traced)
started = time.monotonic()
time.sleep(max(0, SETTLE - (time.monotonic() - started)))

# 1. nothing at all but the sign-in line, which is what a new machine shows until the person signs in
# (the bar's seconds counter once went on counting behind it)
ipc("desk", "inject", json.dumps({"type": "setup", "state": "signed_out", "line": "Sign in to Claude to start.",
                                  "tone": "step", "actions": [{"id": "signin", "label": "Sign in", "style": "primary"}]}))
time.sleep(14)   # (the stone knocks for under ten seconds to say it is waiting for the person, which is the next scene)
shown = json.loads(ipc("line", "state").stdout or "{}")
check("the sign-in line is up while the bar is measured", shown.get("line") == "Sign in to Claude to start.", shown.get("line"))
sample("with only the sign-in line up")

# 2. something needs the person: the stone knocks for a few beats and then rests
now = time.time()
ipc("desk", "inject", json.dumps({"type": "dev", "attention": ["bld"], "front": "", "line": "", "sessions": [
    {"key": "bld", "project": "bombadil", "projectTitle": "Bombadil", "role": "builder", "tool": "codex",
     "toolTitle": "Codex", "title": "builder", "state": "asked", "alive": True, "unseen": False, "yours": False,
     "copy": False, "since": now - 20, "last": "install qemu-full?", "lines": []}]}))
time.sleep(14)   # the knock plays for under ten seconds
sample("something waiting for the person")

# 3. a card and a job on the desk, and a window on the stage that the cards fold around
ipc("desk", "inject", json.dumps({"type": "jobs", "jobs": [
    {"id": "a1b2c3", "title": "Ubuntu 26.04 ISO", "kind": "job", "state": "running", "started": now - 240,
     "deadline": None, "ended": None, "pct": 43.0, "last": "12 MB/s", "unit": "bombadil-job-a1b2c3"}]}))
ipc("desk", "cover", json.dumps({"windows": [{"x": 300, "y": 150, "w": 640, "h": 420}]}))
time.sleep(4)
sample("a job and a window on the stage")

for p in procs.values():
    p.terminate()
for p in procs.values():
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()

passed = sum(results)
print(f"IDLE-DONE pass={passed} fail={len(results) - passed}", flush=True)
sys.exit(0 if passed == len(results) else 1)
