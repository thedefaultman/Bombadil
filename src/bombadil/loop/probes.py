"""Bombadil's checks on itself: invariants as pure functions over what the machine already says.

A probe looks at an `Observation` (hyprctl's JSON, what the bar reports, agentd's liveness, the
ledger's last rows, the coredumps ...) and says what it finds as `Result`s. It only reads: nothing
here opens a socket, runs a program or touches a file (the one exception, `attach_words`, writes
into a finding's own evidence and says so). Collecting the data, running the probes on Hyprland's
events and once a minute while he is away, and keeping what they find (`findings.py`) are others' jobs.

  Observation    everything a probe may look at; every field optional
  Result         one probe's answer: ok is True, False, or None (not checked)
  PROBES         id -> Probe: what it checks in plain words, its kind, which fields it needs
  run_probe()    one probe over an Observation, never raising: a probe that raises or lacks
                 its data becomes a Result about that, not an exception
  run_all()      every probe (minus those left out)

A probe that has nothing to look at says so (`ok=None`, "not checked"), so a missing file or a
half-filled Observation is never read as a bug. Garbage (a failed hyprctl that printed prose, a
list of the wrong things) is "not checked" too.

Kinds say when a finding counts (see findings.py): an `invariant` is a state of the machine that is
checked again 500 ms later; `event`, `crash` and `drift` are facts that already happened and count
at once; `friction` is what he had to work around and counts when it repeats.
"""

import json
import math
import os
import re
import signal
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path

RETRY_AFTER = 0.5         # seconds: an invariant must be red twice this far apart before it counts
DRAWER_GRACE = 2.0        # bombadil-details must be the active window this soon after Details
SUMMON_GRACE = 1.5        # every summon gets its focus_ack within this
SUMMON_TICK = 3.0         # agentd writes a timeout this long after the grace at most: wait it out
MIN_WIDTH = 1024          # logical pixels; narrower is QEMU's 640x480 or a mistake
BAR_FRESH = 15.0          # seconds the bar's alive may be old
BAR_STALE = 30.0          # past this the bar's rectangles say nothing about where the bar is now
AGENTD_SETTLE = 30.0      # seconds after agentd starts that the bar has to say hello again
RESTARTS = 3              # this many bar restarts ...
RESTART_WINDOW = 600.0    # ... within this many seconds is "keeps restarting"
COREDUMP_DAYS = 7         # older dumps are history, not news
UNDO_SOON = 60.0          # seconds after a turn ended
STOP_SOON = 5.0           # seconds after a turn began
REPHRASE_WITHIN = 120.0   # seconds after a failed, stopped or undone turn
SLOW_FACTOR = 3.0         # times its group's median
TOOL_MIN_CALLS = 5        # an error rate needs this many calls of one tool ...
TOOL_ERROR_RATE = 0.4     # ... and at least this share of them failing
TOOL_DAYS = 3             # ... within this many days
WORDS_WITHIN = 300.0      # trouble words join a finding when a probe fired this close (seconds)
ESC_TAPS, ESC_SECONDS = 3, 10

KINDS = ("invariant", "event", "friction", "crash", "drift")


# -- what a probe looks at, and what it says --

@dataclass
class Observation:
    """Everything a probe may look at. A field the caller could not get stays None."""

    clients: list | None = None        # hyprctl -j clients
    monitors: list | None = None       # hyprctl -j monitors
    layers: dict | None = None         # hyprctl -j layers
    activewindow: dict | None = None   # hyprctl -j activewindow ({} when none)
    configerrors: list | None = None   # hyprctl -j configerrors
    bar: dict | None = None            # bar.json: pid, connected_at, alive_at, build, screens
    events: list | None = None         # signals.jsonl rows: hello, summon, focus_ack, focus_timeout, ...
    agentd: dict | None = None         # liveness: {"connected", "ponged": bool, "latency": s, "error": str}
    coredumps: list | None = None      # coredumpctl list --json=short, plus "comm"/"cmdline" when known
    ledger: list | None = None         # turns.jsonl rows, newest last
    tool_results: list | None = None   # [{"turn": id, "tool": name, "ok": bool, "t": secs, "text": ...}]
    turn_errors: list | None = None    # [{"turn": id, "t": secs, "text": ...}]: error events of per-turn logs
    groups: dict | None = None         # turn id -> the group (same kind of ask) it belongs to
    medians: dict | None = None        # group -> median seconds of its turns
    apps: list | None = None           # bombadil-app status: {"ok", "running", "log": [lines]} per app
    now: float | None = None           # epoch seconds
    details_at: float | None = None    # when the drawer last opened (Hyprland's activespecial), when seen
    agentd_started: float | None = None  # agentd.json's start: a bar that has not come back yet is no fault

    def missing(self, needs: Iterable[str]) -> list[str]:
        return [n for n in needs if getattr(self, n, None) is None]


@dataclass
class Result:
    """What one probe found. `ok` None means not checked (no data, or nothing readable in it).

    `kind`, `title` and `at` are the probe's own (filled in for it): `at` is when the thing
    happened, for facts that are already past (a crash, a turn), so the same fact seen on every
    run is one sighting; None for the state of the machine now."""

    ok: bool | None
    id: str
    component: str
    rule: str
    expected: str = ""
    observed: str = ""
    evidence: dict = field(default_factory=dict)
    retry_after: float | None = None
    kind: str = "invariant"
    title: str = ""
    at: float | None = None


@dataclass(frozen=True)
class Probe:
    id: str
    component: str
    kind: str
    title: str      # the finding's sentence, for him
    what: str       # what it checks, in plain words
    needs: tuple    # Observation fields it cannot do without
    run: Callable   # run(obs) -> Result | list[Result] | None; use run_probe(), which never raises


PROBES: dict[str, Probe] = {}


def ids() -> list[str]:
    return list(PROBES)


def get(probe_id: str) -> Probe | None:
    return PROBES.get(probe_id)


def red(expected: str, observed: str, *, title: str = "", component: str = "", rule: str = "",
        at: float | None = None, **evidence) -> Result:
    return Result(False, "", component, rule, expected, observed, evidence, title=title, at=at)


def green(**evidence) -> Result:
    return Result(True, "", "", "", evidence=evidence)


def unchecked(why: str, **evidence) -> Result:
    return Result(None, "", "", "", observed=f"not checked: {why}", evidence=evidence)


def probe(id: str, component: str, kind: str, title: str, what: str, needs: tuple = ()):
    """Register a probe. The function returns red()/green()/unchecked() (or a list of reds); the
    registry stamps its id, component, kind and title on them, and asks an invariant's red to be
    looked at again in RETRY_AFTER seconds."""
    assert kind in KINDS, kind

    def register(fn):
        def run(obs):
            out = fn(obs)
            if out is None:
                return None
            for r in [out] if isinstance(out, Result) else out:
                r.id = id
                r.component = r.component or component
                r.rule = r.rule or id
                r.kind = kind
                r.title = r.title or title
                if r.ok is False and kind == "invariant":
                    r.retry_after = RETRY_AFTER
            return out

        PROBES[id] = Probe(id, component, kind, title, what, tuple(needs), run)
        return fn
    return register


def run_probe(which: str | Probe, obs: Observation, retried: bool = False) -> list[Result]:
    """One probe over an Observation: a list of Results, never an exception. A probe that has no
    data gives one "not checked"; one that raises gives a red Result about the probe itself
    (component "loop", rule "probe-raised"), which findings.py keeps as a finding about the loop.
    `retried=True` is the second look at a red invariant: it asks for no further retry."""
    p = PROBES.get(which) if isinstance(which, str) else which
    if p is None:
        return [Result(None, str(which), "loop", "unknown-probe",
                       observed="not checked: there is no such probe")]
    if not isinstance(obs, Observation):
        return [Result(None, p.id, p.component, p.id, observed="not checked: nothing was observed",
                       kind=p.kind, title=p.title)]
    try:
        missing = obs.missing(p.needs)
        if missing:
            return [Result(None, p.id, p.component, p.id, observed="not checked: no " + ", ".join(missing),
                           kind=p.kind, title=p.title)]
        out = p.run(obs)
        results = [] if out is None else [out] if isinstance(out, Result) else list(out)
        if not all(isinstance(r, Result) for r in results):
            raise TypeError("a probe must return Results")
    except Exception as e:  # noqa: BLE001 - a broken check is a finding about the check, never a crash
        return [Result(False, p.id, "loop", "probe-raised", "the check runs to the end",
                       f"{p.id} raised {type(e).__name__}: {_one_line(str(e))}",
                       {"probe": p.id, "error": type(e).__name__}, kind="event",
                       title=f"One of Bombadil's own checks ({p.id}) broke, so it found nothing this time.")]
    if not results:
        results = [Result(True, p.id, p.component, p.id, kind=p.kind, title=p.title)]
    if retried:
        for r in results:
            r.retry_after = None
    return results


def run_all(obs: Observation, only: Iterable[str] | None = None, skip: Iterable[str] = (),
            retried: bool = False) -> list[Result]:
    """Every probe (or `only` those), minus `skip` (the quarantined ones), each in turn."""
    want = set(only) if only is not None else None
    left_out = set(skip)
    out: list[Result] = []
    for p in list(PROBES.values()):
        if p.id in left_out or (want is not None and p.id not in want):
            continue
        out += run_probe(p, obs, retried=retried)
    return out


def loads(text) -> object | None:
    """hyprctl's JSON, or None when it is not JSON (a compositor that answered in prose)."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# -- reading what the machine says, without trusting it --

def _num(v, default=None):
    """A finite number, else `default` (a bool is not one)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return v


def _pair(v) -> tuple | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) is not None for x in v):
        return v[0], v[1]
    return None


def _dicts(v) -> list[dict] | None:
    """The dicts of a list; None when `v` is not a list at all."""
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else None


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _key(v):
    """An id that can be a dict key: what a file or hyprctl gave, or (for garbage) a stand-in for it."""
    return v if v is None or isinstance(v, (str, int, float)) else repr(v)[:40]


def _one_line(text: str, limit: int = 160) -> str:
    for line in str(text).splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def _class(c: dict) -> str:
    return _str(c.get("class"))


def _ws(c: dict) -> dict:
    return c["workspace"] if isinstance(c.get("workspace"), dict) else {}


def _is_app(c: dict) -> bool:
    return _class(c).startswith("bombadil-app-")


def _shown(c: dict) -> bool:
    return c.get("mapped") is not False and not c.get("hidden")


def _ours(c: dict) -> bool:
    """A window Bombadil is answerable for: its own apps and panels (the drawer, the browser, the
    terminal, the files panel all open in a special workspace). His other windows are his."""
    return _class(c).startswith("bombadil-") or _str(_ws(c).get("name")).startswith("special:")


def window_kind(c: dict) -> str:
    """A class safe to write down: an app's name is his words, another program's class is his business."""
    klass = _class(c)
    if _is_app(c):
        return "bombadil-app"
    if klass.startswith("bombadil-"):
        return klass
    return "panel" if _str(_ws(c).get("name")).startswith("special:") else "other"


def _rect(c: dict) -> tuple | None:
    at, size = _pair(c.get("at")), _pair(c.get("size"))
    return (at[0], at[1], size[0], size[1]) if at and size else None


def _logical(m: dict) -> tuple | None:
    """A monitor's size in logical pixels: what a window is laid out in."""
    w, h = _num(m.get("width")), _num(m.get("height"))
    if w is None or h is None:
        return None
    scale = _num(m.get("scale"), 1.0)
    scale = scale if scale and scale > 0 else 1.0
    if m.get("transform") in (1, 3, 5, 7):   # turned on its side
        w, h = h, w
    return w / scale, h / scale


def _special(m: dict) -> str:
    sp = m.get("specialWorkspace")
    return _str(sp.get("name")) if isinstance(sp, dict) else ""


def _rows(v) -> list[dict] | None:
    """Rows that carry a time, oldest first; None when `v` is not a list."""
    rows = _dicts(v)
    if rows is None:
        return None
    return sorted((r for r in rows if _num(r.get("t")) is not None), key=lambda r: r["t"])


def _now(obs: Observation) -> float | None:
    return _num(obs.now)


# What goes in a fingerprint or a report must not carry where he keeps things or machine noise.
_PATH = re.compile(r"(?<![\w/:.])(?:~|/(?=[\w.]))[\w.@+%:=,-]*(?:/[\w.@+%:=,-]*)+")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{6,}\b", re.IGNORECASE)
_OPAQUE = re.compile(r"\b(?=[\w-]*\d)[\w-]{16,}\b")
_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b"     # a date, maybe with a time
                   r"|\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b")                  # or a time alone
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def scrub_text(text) -> str:
    """Where he keeps things removed: a path (absolute, or under ~) becomes <path>."""
    return _PATH.sub("<path>", str(text))


def strip_line(text) -> str:
    """A line as the same problem always reads: no paths, pids, times, numbers, hex ids, uuids or
    request ids, one space between words. What a fingerprint is made of."""
    s = _PATH.sub("", str(text))
    s = _UUID.sub("", s)
    s = _HEX.sub("", s)
    s = _OPAQUE.sub("", s)
    s = _TIME.sub("", s)
    s = _NUMBER.sub("", s)
    return " ".join(s.lower().split())


def write_json_atomic(path: Path, obj) -> None:
    """Whole file or none: write beside it, then rename over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -- the compositor --

@probe("drawer-focus", "hypr", "invariant", "The details drawer takes no keyboard",
       "after his own Details, bombadil-details is the active window within 2 seconds",
       needs=("clients", "monitors", "activewindow"))
def _drawer_focus(obs):
    clients, monitors = _dicts(obs.clients), _dicts(obs.monitors)
    if clients is None or monitors is None or not isinstance(obs.activewindow, dict):
        return unchecked("hyprctl did not answer with lists")
    open_now = any(_special(m) == "special:details" for m in monitors) and any(
        _class(c) == "bombadil-details" and _shown(c) for c in clients)
    if not open_now:
        return green(drawer="closed")
    now, since = _now(obs), _num(obs.details_at)
    if now is not None and since is not None and now - since < DRAWER_GRACE:
        return unchecked("the drawer opened less than 2 seconds ago",
                         wait=round(DRAWER_GRACE - (now - since), 2))
    # A summoned pill holds the keyboard on purpose; its own probe (summon-focus) judges that.
    summons = [r["t"] for r in _rows(obs.events) or [] if r.get("kind") == "summon"]
    floor = since if since is not None else (now - 10 if now is not None else None)
    if floor is not None and any(t >= floor for t in summons):
        return unchecked("the pill was summoned after the drawer opened")
    active = _class(obs.activewindow)
    if active == "bombadil-details":
        return green(drawer="open", active="bombadil-details")
    shown = window_kind(obs.activewindow) if active else "none"
    return red("bombadil-details is the active window within 2 s of Details opening",
               f"the drawer is open and the active window is {shown}",
               active=shown, focus_history=_num(obs.activewindow.get("focusHistoryID")))


@probe("apps-stacked", "hypr", "invariant", "New apps open on top of each other",
       "no two floating bombadil-app windows share one position and size on a workspace",
       needs=("clients",))
def _apps_stacked(obs):
    clients = _dicts(obs.clients)
    if clients is None:
        return unchecked("hyprctl did not answer with a list")
    spots: dict[tuple, int] = {}
    for c in clients:
        rect = _rect(c)
        if rect and _is_app(c) and _shown(c) and c.get("floating") is True:
            key = (_key(_ws(c).get("id", _ws(c).get("name"))), rect)
            spots[key] = spots.get(key, 0) + 1
    stacked = {k: n for k, n in spots.items() if n >= 2}
    if not stacked:
        return green(apps=sum(spots.values()))
    (ws, rect), n = max(stacked.items(), key=lambda kv: kv[1])
    return red("every floating app window has a position of its own on its workspace",
               f"{n} floating apps share one position and size on a workspace",
               apps=n, workspace=ws, position=list(rect[:2]), size=list(rect[2:]))


@probe("window-oversize", "hypr", "invariant", "A Bombadil window is bigger than the screen",
       "none of Bombadil's windows is larger than the monitor it is on",
       needs=("clients", "monitors"))
def _window_oversize(obs):
    clients, monitors = _dicts(obs.clients), _dicts(obs.monitors)
    if clients is None or monitors is None:
        return unchecked("hyprctl did not answer with lists")
    sizes = {_key(m.get("id")): _logical(m) for m in monitors if m.get("disabled") is not True}
    too_big = []
    for c in clients:
        size, screen = _pair(c.get("size")), sizes.get(_key(c.get("monitor")))
        if not (size and screen and _ours(c) and _shown(c)):
            continue
        if size[0] > screen[0] + 2 or size[1] > screen[1] + 2:   # two pixels of rounding are not "bigger"
            too_big.append({"class": window_kind(c), "size": list(size),
                            "screen": [round(screen[0]), round(screen[1])]})
    if not too_big:
        return green()
    return red("every window of Bombadil's fits on its monitor",
               "a Bombadil window is bigger than its screen", windows=too_big[:5])


def _bar_rects(screen) -> list[dict]:
    """What the bar says it shows on one screen, in that screen's own coordinates."""
    rects = _dicts(screen.get("rects")) if isinstance(screen, dict) else None
    return [r for r in rects or [] if all(_num(r.get(k)) is not None for k in "xywh")]


@probe("window-under-bar", "hypr", "invariant", "The line above the pill covers an app",
       "no floating app window sits under a rectangle the bar shows (the pill, the line, the chips)",
       needs=("clients", "monitors", "bar"))
def _window_under_bar(obs):
    clients, monitors = _dicts(obs.clients), _dicts(obs.monitors)
    screens = obs.bar.get("screens") if isinstance(obs.bar, dict) else None
    if clients is None or monitors is None or not isinstance(screens, dict):
        return unchecked("hyprctl or the bar did not answer with what a check needs")
    now, alive = _now(obs), _num(obs.bar.get("alive_at"))
    if now is not None and alive is not None and now - alive > BAR_STALE:
        return unchecked("the bar's rectangles are old (the bar is not reporting)")
    covered, looked = [], 0
    for m in monitors:
        rects = _bar_rects(screens.get(_key(m.get("name"))))
        if not rects:
            continue
        looked += 1
        ox, oy = _num(m.get("x"), 0), _num(m.get("y"), 0)
        active = _ws({"workspace": m.get("activeWorkspace")}).get("id")
        special = _special(m)
        for c in clients:
            ws, rect = _ws(c), _rect(c)
            if not (rect and _is_app(c) and _shown(c) and c.get("floating") is True):
                continue
            if c.get("monitor") != m.get("id"):
                continue
            if ws.get("id") != active and not (special and ws.get("name") == special):
                continue     # on a workspace nobody is looking at
            x, y, w, h = rect[0] - ox, rect[1] - oy, rect[2], rect[3]
            for r in rects:
                across = min(x + w, r["x"] + r["w"]) - max(x, r["x"])
                down = min(y + h, r["y"] + r["h"]) - max(y, r["y"])
                if across >= 4 and down >= 4:    # a few pixels of border or shadow are not "under"
                    covered.append({"app": [x, y, w, h], "bar": _str(r.get("name"))[:40],
                                    "rect": [r["x"], r["y"], r["w"], r["h"]]})
    if not looked:
        return unchecked("the bar has reported no rectangles for any screen")
    if not covered:
        return green()
    return red("no app window is under what the bar shows", "an app window is under the bar's pill or line",
               overlaps=covered[:5])


@probe("monitor-narrow", "hypr", "invariant", "The screen is narrower than Bombadil is laid out for",
       "every monitor is at least 1024 logical pixels wide (QEMU's 640x480 is the known case)",
       needs=("monitors",))
def _monitor_narrow(obs):
    monitors = _dicts(obs.monitors)
    if monitors is None:
        return unchecked("hyprctl did not answer with a list")
    narrow = []
    for m in monitors:
        size = _logical(m)
        if size and m.get("disabled") is not True and size[0] < MIN_WIDTH:
            narrow.append({"name": _str(m.get("name"))[:40], "logical": [round(size[0]), round(size[1])]})
    if not narrow:
        return green()
    first = narrow[0]
    return red("every monitor is at least 1024 logical pixels wide",
               f"monitor {first['name']} is {first['logical'][0]} logical pixels wide, under {MIN_WIDTH}",
               monitors=narrow[:4])


@probe("hypr-config", "hypr", "invariant", "Hyprland reports errors in its config",
       "hyprctl configerrors is empty", needs=("configerrors",))
def _hypr_config(obs):
    if not isinstance(obs.configerrors, list):
        return unchecked("hyprctl did not answer with a list")
    errors = [scrub_text(_one_line(str(e))) for e in obs.configerrors if e and str(e).strip()]
    if not errors:
        return green()
    return red("Hyprland loads the config without errors", errors[0], errors=errors[:5])


@probe("bar-layer", "hypr", "invariant", "The bar is not on the screen",
       "hyprctl layers lists the bombadil-bar layer", needs=("layers",))
def _bar_layer(obs):
    if not isinstance(obs.layers, dict):
        return unchecked("hyprctl did not answer with an object")
    if not obs.layers:
        return unchecked("hyprctl lists no monitors")
    seen = set()
    for mon in obs.layers.values():
        levels = mon.get("levels") if isinstance(mon, dict) else None
        for layer in levels.values() if isinstance(levels, dict) else []:
            for entry in layer if isinstance(layer, list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("namespace"), str):
                    seen.add(entry["namespace"][:60])
    if "bombadil-bar" in seen:
        return green()
    return red("the bombadil-bar layer is on a screen", "the bar layer (bombadil-bar) is on no screen",
               layers=sorted(seen)[:12])


# -- the bar and agentd, from what they say about themselves --

@probe("summon-focus", "bar", "event", "Tapping Super did not give the pill the keyboard",
       "every summon got a focus_ack within 1.5 seconds", needs=("events",))
def _summon_focus(obs):
    rows = _rows(obs.events)
    if rows is None:
        return unchecked("the bar's events were not a list")
    now = _now(obs)
    waiting: dict[object, dict] = {}     # ids restart at 1 with agentd: one open summon per id, the newest
    timed_out: dict[object, dict] = {}
    bad: list[Result] = []
    summons = 0

    def unanswered(s, late=None, waited=None):
        bad.append(red("every summon got a focus_ack within 1.5 s", "a summon got no focus_ack within 1.5 s",
                       at=s["t"], summon=s.get("id"), waited=waited, late=late,
                       screen=_str(s.get("screen"))[:40]))

    for r in rows:
        kind, sid = r.get("kind"), _key(r.get("id"))
        if kind == "summon":
            summons += 1
            if sid in waiting:    # agentd restarted and gave the id again: the old one never got its ack
                unanswered(waiting[sid], late=False)
            waiting[sid] = r
        elif kind == "focus_timeout":
            timed_out[sid] = r
        elif kind == "focus_ack" and sid in waiting:
            s = waiting.pop(sid)
            ms = _num(r.get("ms"))
            delay = ms / 1000 if ms is not None else r["t"] - s["t"]
            if delay > SUMMON_GRACE:
                unanswered(s, late=True, waited=round(delay, 2))
    for sid, s in waiting.items():
        t = timed_out.get(sid)
        if t is not None and t.get("bar") is False:
            continue   # nobody was listening: that is the bar's absence, not a keyboard that would not come
        if t is not None or (now is not None and now - s["t"] > SUMMON_GRACE + SUMMON_TICK):
            unanswered(s, late=False, waited=_num(t.get("waited")) if t else round(now - s["t"], 2))
    return bad or green(summons=summons)


@probe("agentd-ping", "agentd", "invariant", "Bombadil's agent does not answer",
       "a real connection to agentd's socket gets a pong", needs=("agentd",))
def _agentd_ping(obs):
    live = obs.agentd
    if not isinstance(live, dict):
        return unchecked("no liveness result")
    if live.get("connected") is not True:
        return red("agentd answers ping", "agentd did not accept a connection on its socket",
                   error=scrub_text(_one_line(_str(live.get("error")), 120)))
    if live.get("ponged") is not True:
        return red("agentd answers ping", "agentd accepted a connection but did not answer ping",
                   error=scrub_text(_one_line(_str(live.get("error")), 120)))
    return green(latency=_num(live.get("latency")))


@probe("bar-alive", "bar", "invariant", "The bar has stopped answering",
       "the bar's last alive is less than 15 seconds old", needs=("bar", "now"))
def _bar_alive(obs):
    if not isinstance(obs.bar, dict):
        return unchecked("the bar's report was not readable")
    alive, now = _num(obs.bar.get("alive_at")), _now(obs)
    if alive is None or now is None:
        return unchecked("the bar's report has no alive time, or there is no clock")
    started = _num(obs.agentd_started)
    if started is not None and alive < started and now - started < AGENTD_SETTLE:
        return unchecked("agentd has only just started and the bar has not said hello to it yet")
    age = now - alive
    if age <= BAR_FRESH:
        return green(age=round(age, 1))
    return red("the bar says it is alive at least every 15 s", "the bar's last alive is more than 15 s old",
               age=round(age, 1))


@probe("bar-restarts", "bar", "event", "The bar keeps restarting",
       "the bar did not restart three times in ten minutes", needs=("events",))
def _bar_restarts(obs):
    rows = _rows(obs.events)
    if rows is None:
        return unchecked("the bar's events were not a list")
    times = [r["t"] for r in rows if r.get("kind") == "restart"]
    bursts = []     # the last restart of each run of three within ten minutes
    for i, t in enumerate(times):
        run = [u for u in times[: i + 1] if t - u <= RESTART_WINDOW]
        if len(run) >= RESTARTS:
            if bursts and t - bursts[-1][0] <= RESTART_WINDOW:
                bursts.pop()
            bursts.append((t, len(run), t - run[0]))
    bad = [red("the bar stays up", "the bar restarted again and again within ten minutes", at=t, restarts=n,
               seconds=round(span)) for t, n, span in bursts]
    return bad or green(restarts=len(times))


# -- crashes --

_PROGRAMS = (
    ("agentd", "agentd", ("agentd",)),
    ("bar", "quickshell", ("quickshell", "qs")),
    ("hypr", "Hyprland", ("hyprland", ".hyprland-wrapped", "hyprland-wrapped")),
    ("apps", "bombadil-app", ("bombadil-app",)),
    ("os-mcp", "bombadil-os-mcp", ("bombadil-os-mcp", "os-mcp")),
)


def _program(dump: dict) -> tuple | None:
    """(component, program) of a coredump when it is one of Bombadil's. A Python program shows up
    as python: its comm or command line (which the runner adds) says which."""
    names = set()
    for key in ("exe", "comm"):
        if isinstance(dump.get(key), str):
            names.add(os.path.basename(dump[key]).lower())
    cmd = dump.get("cmdline")
    tokens = cmd.split() if isinstance(cmd, str) else cmd if isinstance(cmd, list) else []
    tokens = [t for t in tokens if isinstance(t, str)]
    names |= {os.path.basename(t).lower() for t in tokens}
    for component, label, aliases in _PROGRAMS:
        if names & set(aliases):
            return component, label
    if names & {"chromium", "chrome"} and (not tokens or any("bombadil-browser" in t for t in tokens)):
        return "browser", "the browser panel"
    return None


def _when(v) -> float | None:
    """A coredump's time: coredumpctl --json gives microseconds."""
    n = _num(v)
    if n is None or n <= 0:
        return None
    return n / 1e6 if n > 1e14 else n / 1e3 if n > 1e11 else float(n)


def _signal_name(v) -> str:
    n = _num(v)
    if n is not None:
        try:
            return signal.Signals(int(n)).name
        except ValueError:
            return f"signal {int(n)}"
    return re.sub(r"[^\w ]", "", v)[:20] if isinstance(v, str) and v else "a signal"


@probe("coredump", "agentd", "crash", "A part of Bombadil crashed",
       "coredumpctl lists no dump of agentd, quickshell, Hyprland, bombadil-app, os-mcp or the browser panel",
       needs=("coredumps",))
def _coredump(obs):
    dumps = _dicts(obs.coredumps)
    if dumps is None:
        return unchecked("coredumpctl did not answer with a list")
    now = _now(obs)
    bad = []
    for d in dumps:
        found, when = _program(d), _when(d.get("time"))
        if found is None or (now is not None and when is not None and now - when > COREDUMP_DAYS * 86400):
            continue
        component, label = found
        sig = _signal_name(d.get("sig", d.get("signal")))
        bad.append(red("the program keeps running", f"{label} crashed ({sig})", at=when, component=component,
                       title=f"{label} crashed.", program=label, signal=sig,
                       corefile=_str(d.get("corefile"))[:20], size=_num(d.get("size"))))
    return bad or green(dumps=len(dumps))


# -- turns, from the ledger --

_NOT_OURS = (
    ("signed out", re.compile(
        r"not (?:logged|signed) in|/login|(?:log|sign) in again|please (?:log|sign) in|invalid api key|"
        r"authentication|unauthori[sz]ed|oauth token|token (?:has )?expired|credentials", re.IGNORECASE)),
    ("limit", re.compile(
        r"usage limit|rate.?limit|limit reached|quota|credit balance|too many requests|\b429\b|"
        r"out of (?:credits|usage)|hit (?:your|the) (?:\w+ )?limit", re.IGNORECASE)),
    ("offline", re.compile(
        r"offline|no (?:internet|network)|network (?:is )?(?:error|unreachable|down)|could not resolve|"
        r"name or service not known|getaddrinfo|enotfound|econn(?:refused|reset|aborted)|"
        r"connection (?:refused|reset|error|closed)|(?:request|connection) timed out|unable to connect|"
        r"service unavailable|overloaded|\b50[234]\b", re.IGNORECASE)),
)


def not_ours(text, row: dict | None = None) -> str | None:
    """Why a failed turn is not Bombadil's bug: "signed out", "limit" or "offline", read from the
    provider's own words (and its rate-limit notice); None when it may be ours."""
    limit = row.get("rate_limit") if isinstance(row, dict) else None
    if isinstance(limit, dict) and str(limit.get("status", "")).lower() in ("rejected", "blocked", "limited"):
        return "limit"
    for why, rx in _NOT_OURS:
        if rx.search(str(text or "")):
            return why
    return None


def _model_rows(ledger) -> list[dict] | None:
    """The turns that went to a model, oldest first. A `!command` is his shell, not a turn of ours."""
    rows = _rows(ledger)
    if rows is None:
        return None
    return [r for r in rows if r.get("kind") not in ("local", "improve") and r.get("provider") != "shell"
            and not _str(r.get("prompt")).startswith("!")]


def _errors_by_turn(obs: Observation) -> dict[object, list[str]]:
    out: dict[object, list[str]] = {}
    for e in _dicts(obs.turn_errors) or []:
        out.setdefault(_key(e.get("turn")), []).append(_str(e.get("text")))
    return out


def _prompts(obs: Observation) -> list[str]:
    return [p for r in _dicts(obs.ledger) or [] for p in (_str(r.get("prompt")),) if len(p) >= 4]


def _redacted(text: str, prompts: list[str], limit: int = 120) -> str:
    """A provider's error line, without his words (a CLI that echoes the prompt) or paths."""
    line = _one_line(text, 400)
    for p in prompts:
        line = line.replace(p, "…")
    return scrub_text(line)[:limit]



@probe("turn-failed", "turns", "friction", "A turn ended in an error",
       "no turn ended with ok false or null, except signed out, a limit or offline (those are not ours)",
       needs=("ledger",))
def _turn_failed(obs):
    rows = _model_rows(obs.ledger)
    if rows is None:
        return unchecked("the ledger was not a list")
    errors, prompts = _errors_by_turn(obs), _prompts(obs)
    bad = []
    for r in rows:
        if r.get("ok") is True or r.get("stopped") is True or "ok" not in r:
            continue
        said = "\n".join([*errors.get(_key(r.get("id")), []), _str(r.get("result"))]).strip()
        if not_ours(said, r):
            continue
        line = _redacted(said, prompts) or "no result"
        bad.append(red("a turn ends with a result", f"a turn ended with an error: {line}", at=r["t"],
                       ok=r.get("ok"), provider=_str(r.get("provider"))[:20],
                       origin=_str(r.get("origin"))[:20], seconds=_num(r.get("seconds"))))
    return bad or green(turns=len(rows))


@probe("os-tools", "os-mcp", "event", "The OS tools did not start for a turn",
       "no turn says the OS tools did not start", needs=("ledger",))
def _os_tools(obs):
    rows = _model_rows(obs.ledger)
    if rows is None:
        return unchecked("the ledger was not a list")
    said = re.compile(r"the OS tools did not start(?: \(bombadil-os: (\w+)\))?")
    hits: dict[object, tuple] = {}     # turn -> (when, why): the error event first, then the turn's own row
    for e in _dicts(obs.turn_errors) or []:
        m = said.search(_str(e.get("text")))
        if m and _num(e.get("t")) is not None:
            hits[_key(e.get("turn"))] = (e["t"], m.group(1) or "")
    for r in rows:
        m = said.search(_str(r.get("result")))
        tid = _key(r.get("id"))
        if m or tid in hits:
            why = (m.group(1) if m else "") or hits.get(tid, (0, ""))[1]
            hits[tid or r["t"]] = (r["t"], why)    # the row's time is when the turn ended
    bad = [red("the OS tools start with every turn",
               f"the OS tools did not start ({why or 'no reason given'})", at=t, status=why)
           for t, why in hits.values()]
    return bad or green(turns=len(rows))


def _tool(name) -> str:
    """mcp__bombadil-os__create_app -> create_app."""
    return _str(name).rsplit("__", 1)[-1]


@probe("tool-errors", "os-mcp", "friction", "An OS tool fails a lot",
       "no os-mcp tool fails in 40% or more of at least five calls in three days", needs=("tool_results",))
def _tool_errors(obs):
    results = _dicts(obs.tool_results)
    if results is None:
        return unchecked("the tool results were not a list")
    now = _now(obs)
    calls: dict[str, list[dict]] = {}
    for r in results:
        name, t = _tool(r.get("tool")), _num(r.get("t"))
        if name and (now is None or t is None or now - t <= TOOL_DAYS * 86400):
            calls.setdefault(name, []).append(r)
    bad = []
    for name, rs in sorted(calls.items()):
        failed = [r for r in rs if r.get("ok") is False]
        if len(rs) >= TOOL_MIN_CALLS and len(failed) / len(rs) >= TOOL_ERROR_RATE:
            newest = max((_num(r.get("t"), 0) for r in failed), default=0) or None
            bad.append(red("an OS tool mostly works",
                           f"the OS tool {name} fails in a large share of its calls",
                           at=newest, tool=name, calls=len(rs), failed=len(failed)))
    return bad or green(tools=len(calls))


_CHECK_FAILED = re.compile(r"check FAILED")
_FIRST_ERROR = re.compile(r'"errors":\s*\[\s*"((?:[^"\\]|\\.)*)"')
_WHERE = re.compile(r"\S+\.qml:\d+(?::\d+)?:?\s*")


def _missing_piece(text: str) -> str:
    """The first error a failed create_app check lists, without where in which file it was."""
    m = _FIRST_ERROR.search(text)
    piece = loads(f'"{m.group(1)}"') if m else ""
    return _WHERE.sub("", scrub_text(piece if isinstance(piece, str) else "")).strip()


@probe("app-check", "apps", "friction", "Making an app keeps failing its own check",
       "create_app does not fail its check twice for the same missing piece", needs=("tool_results",))
def _app_check(obs):
    results = _dicts(obs.tool_results)
    if results is None:
        return unchecked("the tool results were not a list")
    pieces: dict[str, list[tuple]] = {}
    for r in results:
        text = _str(r.get("text"))
        if _tool(r.get("tool")) != "create_app" or not _CHECK_FAILED.search(text):
            continue    # a failed check comes back as an ordinary result, not as a tool error
        piece = _missing_piece(text)
        pieces.setdefault(strip_line(piece), []).append((r, piece))
    bad = []
    for seen in pieces.values():
        if len(seen) >= 2:
            newest = max((_num(r.get("t"), 0) for r, _ in seen), default=0) or None
            piece = _one_line(seen[-1][1], 100) or "no error line"
            bad.append(red("create_app's check passes once the app is written",
                           f"create_app failed its check again for the same piece: {piece}",
                           at=newest, times=len(seen), turns=len({_key(r.get("turn")) for r, _ in seen})))
    return bad or green(failed=sum(len(v) for v in pieces.values()))


def _verb(row: dict) -> str:
    return _str(row.get("verb") or row.get("action")) if row.get("kind") == "local" else ""


def _undone(rows: list[dict], turn: dict) -> dict | None:
    """The undo row that went back to this turn's restore point (or named it), if any."""
    for u in rows:
        if _verb(u) != "undo" or u.get("ok") is False:
            continue
        if (u.get("of") is not None and u.get("of") == turn.get("id")) or (
                u.get("of_snapshot") is not None and u.get("of_snapshot") == turn.get("snapshot")):
            return u
    return None


@probe("undo-soon", "turns", "friction", "You undid a change within a minute",
       "no turn was undone within 60 seconds of finishing", needs=("ledger",))
def _undo_soon(obs):
    rows = _rows(obs.ledger)
    if rows is None:
        return unchecked("the ledger was not a list")
    bad = []
    for turn in _model_rows(rows) or []:
        u = _undone(rows, turn)
        if u is not None and 0 <= u["t"] - turn["t"] <= UNDO_SOON:
            bad.append(red("a turn's result is kept", "a turn was undone within 60 seconds of finishing",
                           at=u["t"], after=round(u["t"] - turn["t"], 1),
                           provider=_str(turn.get("provider"))[:20]))
    return bad or green()


@probe("stop-soon", "turns", "friction", "You stopped a turn within seconds",
       "no turn was stopped within 5 seconds of starting", needs=("ledger",))
def _stop_soon(obs):
    rows = _rows(obs.ledger)
    if rows is None:
        return unchecked("the ledger was not a list")
    by_id = {_key(r.get("id")): r for r in _model_rows(rows) or [] if r.get("id")}
    bad = []
    for s in rows:
        # A stop with nothing running names no turn.
        turn = by_id.get(_key(s.get("of"))) if _verb(s) == "stop" else None
        if turn is None:
            continue
        ran = _num(turn.get("seconds"))
        if ran is None and _num(turn.get("started")) is not None:
            ran = s["t"] - turn["started"]
        if ran is not None and ran <= STOP_SOON:
            bad.append(red("a turn runs until it is done", "a turn was stopped within 5 seconds of starting",
                           at=s["t"], ran=round(ran, 1), origin=_str(turn.get("origin"))[:20]))
    return bad or green()


_SMALL_WORDS = frozenset({"a", "an", "the", "to", "of", "in", "on", "it", "is", "me", "my", "i", "you",
                          "and", "or", "for", "with", "that", "this", "please", "can", "could", "would",
                          "what", "how", "do", "does", "did", "be", "are", "was"})


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _SMALL_WORDS and len(w) > 1}


def _alike(a: str, b: str) -> bool:
    """Is `b` the same ask as `a`, said again? Shared words, or nearly the same letters."""
    wa, wb = _words(a), _words(b)
    if wa and wb and len(wa & wb) / min(len(wa), len(wb)) >= 0.5:
        return True
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.6


@probe("rephrase", "turns", "friction", "You had to ask again after a turn went wrong",
       "no typed ask is repeated within two minutes of a failed, stopped or undone turn", needs=("ledger",))
def _rephrase(obs):
    rows = _rows(obs.ledger)
    if rows is None:
        return unchecked("the ledger was not a list")
    typed = [r for r in _model_rows(rows) or []
             if r.get("origin", "typed") == "typed" and _str(r.get("prompt"))]
    errors, bad = _errors_by_turn(obs), []
    for a, b in pairwise(typed):
        began = _num(b.get("started"), b["t"] - (_num(b.get("seconds"), 0) or 0))
        undo = _undone(rows, a)
        said = "\n".join([*errors.get(_key(a.get("id")), []), _str(a.get("result"))])
        if a.get("stopped") is True:
            how, when = "stopped", a["t"]
        elif undo is not None:
            how, when = "undone", undo["t"]
        elif a.get("ok") is not True and not not_ours(said, a):
            how, when = "failed", a["t"]
        else:
            continue
        if 0 <= began - when <= REPHRASE_WITHIN and _alike(_str(a["prompt"]), _str(b["prompt"])):
            bad.append(red("an ask is answered the first time",
                           f"an ask was made again after a turn was {how}", at=b["t"],
                           after=round(began - when, 1), how=how))
    return bad or green()


@probe("slow-turn", "turns", "friction", "A turn took three times as long as it usually does",
       "no turn took three times its group's median", needs=("ledger", "groups", "medians"))
def _slow_turn(obs):
    rows = _model_rows(obs.ledger)
    if rows is None or not isinstance(obs.groups, dict) or not isinstance(obs.medians, dict):
        return unchecked("the ledger, groups or medians were not readable")
    bad = []
    for r in rows:
        usual = _num(obs.medians.get(_key(obs.groups.get(_key(r.get("id"))))))
        seconds = _num(r.get("seconds"))
        if r.get("ok") is not True or r.get("stopped") is True or seconds is None or not usual or usual <= 0:
            continue
        if seconds >= SLOW_FACTOR * usual:
            bad.append(red("a turn takes about as long as the others like it",
                           "a turn took three times its group's median", at=r["t"], seconds=round(seconds, 1),
                           median=round(usual, 1), times=round(seconds / usual, 1)))
    return bad or green()


@probe("provider-drift", "providers", "drift", "The AI tool sends messages Bombadil does not know",
       "no turn's stream had a message type the provider adapter does not know", needs=("ledger",))
def _provider_drift(obs):
    rows = _rows(obs.ledger)
    if rows is None:
        return unchecked("the ledger was not a list")
    bad = []
    for r in rows:
        drift = r.get("drift")
        if isinstance(drift, dict) and drift:
            names = sorted({re.sub(r"[^\w.-]", "", str(k))[:40] for k in drift})[:3]
            who = _str(r.get("provider"))[:20] or "the provider"
            bad.append(red("every message in the stream is one the adapter knows",
                           f"{who} sent message types Bombadil does not know: {', '.join(names)}",
                           at=r["t"], types=names, provider=who))
    return bad or green()


# -- the bar's own friction --

@probe("esc-friction", "bar", "friction", "The Esc key had to be pressed again and again",
       "Esc was not pressed three times in ten seconds with the drawer or a card up", needs=("events",))
def _esc_friction(obs):
    rows = _rows(obs.events)
    if rows is None:
        return unchecked("the bar's events were not a list")
    bad = []
    for r in rows:
        if r.get("kind") != "friction" or r.get("what") != "esc":
            continue
        count, seconds = _num(r.get("count"), ESC_TAPS), _num(r.get("seconds"), ESC_SECONDS)
        if count >= ESC_TAPS and seconds <= ESC_SECONDS:
            up = "the drawer" if r.get("drawer") else "a card" if r.get("card") else "nothing"
            bad.append(red("Esc closes what it is pressed on, once",
                           f"Esc was pressed again and again with {up} up", at=r["t"], count=count,
                           seconds=seconds))
    return bad or green()


# -- apps --

_FATAL = re.compile(r"\bfatal\b|segmentation fault|core dumped", re.IGNORECASE)


@probe("app-health", "apps", "invariant", "An app stopped working",
       "no app's status says not ok with its process gone, and no app's log has a FATAL line",
       needs=("apps",))
def _app_health(obs):
    apps = _dicts(obs.apps)
    if apps is None:
        return unchecked("the app statuses were not a list")
    gone = sum(1 for a in apps if a.get("ok") is False and a.get("running") is False)
    if gone:
        return red("an app that is not ok is still running, or is fixed",
                   "an app's status says it is not ok and its process is gone", apps=gone)
    for a in apps:
        log = [_str(x) for x in a["log"]] if isinstance(a.get("log"), list) else []
        # Only this run's lines: the runtime writes a "---" line each time the app starts.
        start = max((i for i, x in enumerate(log) if x.startswith("---")), default=-1)
        for line in log[start + 1:]:
            if _FATAL.search(line):
                return red("an app's log has no FATAL line",
                           f"an app's log has a fatal line: {strip_line(line)[:100]}",
                           line=scrub_text(_one_line(line, 160)))
    return green(apps=len(apps))


# -- his words, only beside a probe that fired --

_TROUBLE = re.compile(r"\b(?:still|won['’]t|wont|stuck|broken|frozen|again|not working|doesn['’]t work|"
                      r"nothing happens)\b", re.IGNORECASE)


def attach_words(finding, prompts, fired: Iterable[float] | None = None) -> list[str]:
    """The words of trouble ("still", "won't", "stuck" ...) in his prompts that came within five
    minutes of a probe firing for `finding`, and none otherwise: such a word alone never makes a
    finding. `prompts` is ledger rows (`t`, `prompt`) or (t, text) pairs; `fired` is the times the
    probe fired (default: the finding's first and last). Only the words are kept, never the sentence.

    The one function here that writes: when there are words and the finding has an evidence
    directory, they go into its evidence.json as "words"."""
    if fired is None:
        fired = (getattr(finding, "first", None), getattr(finding, "last", None))
    times = [t for t in fired if _num(t) is not None]
    words: set[str] = set()
    for p in prompts or []:
        if isinstance(p, dict):
            t, text = p.get("t"), p.get("prompt")
        else:
            t, text = p if isinstance(p, (list, tuple)) and len(p) == 2 else (None, None)
        if _num(t) is None or not isinstance(text, str) or not any(abs(t - f) <= WORDS_WITHIN for f in times):
            continue
        words |= {m.group(0).lower().replace("’", "'") for m in _TROUBLE.finditer(text)}
    found = sorted(words)
    folder = getattr(finding, "evidence", "")
    if found and isinstance(folder, str) and folder:
        path = Path(folder) / "evidence.json"
        try:
            bundle = json.loads(path.read_text())
            if isinstance(bundle, dict):
                write_json_atomic(path, {**bundle, "words": found})
        except (OSError, ValueError):
            pass   # no bundle to add to (yet): the caller still has the words
    return found
