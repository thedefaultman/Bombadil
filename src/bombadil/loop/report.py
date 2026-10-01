"""The report: what a finding Bombadil cannot fix becomes when he decides to send it to the project.

A `Report` is made from typed fields: the finding's counts and sentences, the build and versions, a
picture drawn from the windows' rectangles, and at most three log lines. Nothing else can get in,
because nothing else is read: no prompt, no window title, no path, no screenshot, no name. The few
free-text fields (the title, what was expected and observed, the log lines) are cut to one line, and
as a second lock every path in them becomes "~" or "<path>" and any private string the evidence
holds (a title, a prompt) is taken out. The lock is not what keeps the report clean; leaving things
out is.

  build(finding)            the Report, from the finding and its evidence.json
  preview(report)           the two lists the Send card shows, and the exact text
  hold(report)              writes reports/<fp>.md, the copy held on the machine
  already_reported(fp)      the number of the issue that has this fingerprint, or None
  issue_url(report)         the new-issue link, prefilled; too long a body goes to the clipboard
  open_issue_page(url)      shows it in the browser panel (or xdg-open); he presses Submit himself

No token is stored and `gh` is not used. `already_reported` and `open_issue_page` are for after he
pressed Send, and they wait on the network and the browser: call them from a thread, never on a
turn's path.
"""

import json
import math
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .. import hypr, paths
from . import db
from .findings import evidence_dir

DEFAULT_REPO = "thedefaultman/Bombadil"
LABELS = "found-by-bombadil"
MAX_URL = 7000          # a link longer than this (after quoting) opens the page empty and the text is pasted
MAX_LOG = 3             # log lines in a report
MAX_PATCH = 4000        # characters of a suggested patch that fit in a report
PICTURE_COLS = 64
TRIED = "nothing has fixed it on this machine yet"
BROWSER_DEBUG_PORT = 9222

GOES = ("What happened", "The build", "Versions", "The failing check and its output",
        "A picture of the windows as boxes, without titles")
STAYS = ("What you typed or said", "Your files and their paths", "The titles of your windows and pages",
         "Screenshots", "Your name")

# Whose version belongs in the title: the program the finding is about.
_TITLE_VERSION = {"hypr": "Hyprland", "bar": "Quickshell"}
_VERSION_ORDER = ("Hyprland", "Quickshell", "Claude Code", "codex-cli", "kit")
# Window classes that are Bombadil's own, as the picture names them (long and short).
_LABELS = {"bombadil-app": ("app", "a"), "bombadil-details": ("details", "d"),
           "bombadil-browser": ("browser", "b"), "bombadil-terminal": ("terminal", "t"),
           "bombadil-bar": ("bar", "b"), "panel": ("panel", "p")}
_OTHER = ("other window", "other", "o")
# What evidence may hold that is his: taken out of any free-text line, wherever it turns up.
_PRIVATE_KEYS = frozenset({"title", "initialTitle", "prompt", "result", "summary", "details", "word",
                           "target", "text", "session", "cmdline", "address", "screenshot", "file", "path",
                           "line"})
_SYSTEM = ("/usr/", "/opt/", "/etc/", "/lib/", "/lib64/", "/bin/", "/sbin/", "/dev/", "/proc/", "/sys/",
           "/run/")
_GENERIC_NAMES = frozenset({"user", "root", "arch", "admin", "bombadil", "localhost", "archiso"})
_HOME = re.compile(r"(?:/home/[^/\s]+|/Users/[^/\s]+|/root(?![\w.-])|~(?=/|\s|$))(?:/[^\s\"'<>)\]]*)?")
_PATH = re.compile(r"(?<![\w/:.])/(?:[\w.@+%=,-]+/)*[\w.@+%=,-]+")


# -- the report --

@dataclass(frozen=True)
class Report:
    fp: str
    title: str                          # "Details drawer takes no keyboard (Hyprland 0.56.2) [fp 3c91a0]"
    n: int = 1
    days: int = 1
    build: str = ""
    machine: str = ""
    expected: str = ""
    observed: str = ""                  # with the probe and its retry: "... (probe drawer-focus, 1 retry)"
    picture: str = ""                   # the windows as boxes; "" when the finding has none
    log: tuple = ()                     # at most three lines
    versions: tuple = ()                # (("Quickshell", "0.3.1"), ...) without the one the title names
    tried: tuple = (TRIED,)
    patch: str = ""                     # a suggested patch, attached and never applied
    what: str = ""                      # the finding's sentence, without the fingerprint
    check: str = ""                     # probe id and its observed line, for the preview

    @property
    def times(self) -> str:
        return f"{self.n} time{'' if self.n == 1 else 's'} on {self.days} day{'' if self.days == 1 else 's'}"

    @property
    def seen(self) -> str:
        build = f"build {self.build}" if self.build else ""
        return ", ".join(x for x in (self.times, build, self.machine) if x)

    def body(self) -> str:
        """Everything after the title: what the issue page's body holds."""
        out = [f"Seen: {self.seen}", f"Expected: {self.expected}", f"Observed: {self.observed}"]
        if self.picture:
            out += ["Picture: the windows as boxes, drawn from hyprctl rectangles", _fence(self.picture)]
        if self.log:
            out += ["Log: the last lines, with folders removed", _fence("\n".join(self.log))]
        if self.versions:
            out.append("Versions: " + ", ".join(f"{k} {v}" for k, v in self.versions))
        out.append("Tried: " + "; ".join(self.tried))
        if self.patch:
            out += ["Suggested patch: attached here and never applied on his machine",
                    _fence(self.patch, "diff")]
        return "\n".join(out)

    def render(self) -> str:
        """The whole report in the shape of the brief's example: a Title line, then the fields."""
        return f"Title: {self.title}\n{self.body()}\n"

    def to_dict(self) -> dict:
        return {"fp": self.fp, "title": self.title, "seen": self.seen, "expected": self.expected,
                "observed": self.observed, "picture": self.picture, "log": list(self.log),
                "versions": [list(v) for v in self.versions], "tried": list(self.tried),
                "patch": self.patch, "text": self.render()}


def _fence(text: str, lang: str = "text") -> str:
    """A code block the text cannot close early."""
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}{lang}\n{text}\n{ticks}"


def short_fp(fp: str) -> str:
    """The hash at the end of a fingerprint: hypr:drawer-focus:3c91a0 is 3c91a0."""
    return str(fp).rsplit(":", 1)[-1]


# -- free text, made safe --

def _homes() -> list[str]:
    homes = {str(Path.home()), os.environ.get("HOME", "")}
    return sorted((h for h in homes if len(h) > 1 and h != "/"), key=len, reverse=True)


def _names() -> list[str]:
    """What names him or his machine, and so stays out of a report."""
    found = {os.environ.get("USER", ""), os.environ.get("LOGNAME", ""), Path.home().name}
    try:
        found.add(socket.gethostname())
    except OSError:
        pass
    names = (n for n in found if len(n) >= 3 and n.lower() not in _GENERIC_NAMES)
    return sorted(names, key=len, reverse=True)


def _path_label(m: re.Match) -> str:
    return m.group(0) if m.group(0).startswith(_SYSTEM) else "<path>"


def _first_line(text) -> str:
    for line in str(text).splitlines():
        if line.strip():
            return line.strip()
    return ""


def _line(text, private: Iterable[str] = (), limit: int = 160) -> str:
    """One line of free text: its first line, what is private taken out, every path collapsed (under
    home to "~", the rest to "<path>" except the system's own), his name and his machine's taken out,
    and cut."""
    s = _first_line(text)
    for secret in private:
        s = s.replace(secret, "…")
    for home in _homes():
        s = s.replace(home, "~")
    s = _HOME.sub("~", s)
    s = _PATH.sub(_path_label, s)
    for name in _names():
        s = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "…", s, flags=re.IGNORECASE)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _private_strings(value, depth: int = 0) -> list[str]:
    """Every string under a private key anywhere in the evidence: what he typed or read."""
    found: list[str] = []
    if depth > 6:
        return found
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _PRIVATE_KEYS and isinstance(v, str) and len(v) >= 4:
                found.append(v)
            else:
                found += _private_strings(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value[:200]:
            found += _private_strings(v, depth + 1)
    return sorted(set(found), key=len, reverse=True)


def _headline(title: str) -> str:
    s = re.sub(r"^The\s+", "", title.strip()).rstrip(".!")
    return s[:1].upper() + s[1:]


# -- the picture --

def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e7:
        return None
    return float(v)


def _pair(v) -> tuple | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) is not None for x in v):
        return float(v[0]), float(v[1])
    return None


def _logical(m: dict) -> tuple | None:
    """A monitor's size in logical pixels, which is what windows are laid out in."""
    w, h, scale = _num(m.get("width")), _num(m.get("height")), _num(m.get("scale")) or 1.0
    if not w or not h or w <= 0 or h <= 0 or scale <= 0:
        return None
    if m.get("transform") in (1, 3, 5, 7):
        w, h = h, w
    return w / scale, h / scale


def _fit(choices, room: int) -> str:
    return next((c for c in choices if len(c) <= room), "")


def _windows(bundle: dict) -> list[dict]:
    """The windows that are on screen: class kind, position, size, workspace. Nothing else is read."""
    out = []
    for w in bundle.get("windows") if isinstance(bundle.get("windows"), list) else []:
        if not isinstance(w, dict):
            continue
        at, size = _pair(w.get("at")), _pair(w.get("size"))
        if not (at and size and size[0] > 0 and size[1] > 0) or w.get("mapped") is False or w.get("hidden"):
            continue
        workspace = w.get("workspace")
        out.append({"kind": w["class"] if isinstance(w.get("class"), str) else "other", "at": at,
                    "size": size, "workspace": workspace if isinstance(workspace, (int, str)) else None})
    return out


def _boxes(wins: list[dict], active: dict | None, ox: float, oy: float) -> list[dict]:
    """One box per distinct window rectangle, with how many windows share it (three apps at one spot)
    and whether it is the active one. `layer` orders the drawing: other windows, Bombadil's, the active."""
    boxes: dict[tuple, dict] = {}
    marked = False
    for w in wins:
        is_active = (not marked and active is not None and w["kind"] == active.get("class")
                     and _pair(active.get("at")) == w["at"] and _pair(active.get("size")) == w["size"])
        marked = marked or is_active
        names = _LABELS.get(w["kind"], _OTHER)
        layer = 3 if is_active else 2 if w["kind"] in _LABELS else 1
        rect = (w["at"][0] - ox, w["at"][1] - oy, w["size"][0], w["size"][1])
        box = boxes.setdefault((layer, names[0], *(round(v) for v in rect)),
                               {"layer": layer, "rect": rect, "names": names, "n": 0})
        box["n"] += 1
    return list(boxes.values())


def _bar_boxes(bundle: dict, screen_name) -> list[dict]:
    """What the bar says it shows on this screen, named only "bar"."""
    screens = bundle["bar"].get("screens") if isinstance(bundle.get("bar"), dict) else None
    mine = screens.get(screen_name) if isinstance(screens, dict) else None
    rects = mine.get("rects") if isinstance(mine, dict) and isinstance(mine.get("rects"), list) else []
    return [{"layer": 4, "rect": (r["x"], r["y"], r["w"], r["h"]), "names": ("bar", "b"), "n": 1}
            for r in rects[:40] if isinstance(r, dict) and all(_num(r.get(k)) is not None for k in "xywh")
            and r["w"] > 0 and r["h"] > 0]


def _picture(bundle: dict) -> str:
    """The windows of the screen that has most of them, as boxes: to scale, no titles, Bombadil's own
    windows by kind and everything else as "other window". The screen is a dotted frame, so a window
    bigger than it shows; the active window is drawn with #, the bar with :. "" when there is nothing
    to draw."""
    wins = _windows(bundle)
    if not wins:
        return ""
    monitors = [m for m in bundle["monitors"] if isinstance(m, dict) and m.get("disabled") is not True
                and _logical(m)] if isinstance(bundle.get("monitors"), list) else []
    name = None
    if monitors:
        def holds(m):
            lw, lh = _logical(m)
            mx, my = _num(m.get("x")) or 0.0, _num(m.get("y")) or 0.0
            return sum(1 for w in wins if mx <= w["at"][0] + w["size"][0] / 2 < mx + lw
                       and my <= w["at"][1] + w["size"][1] / 2 < my + lh)
        screen = max(monitors, key=holds)
        name = screen.get("name")
        lw, lh = _logical(screen)
        ox, oy = _num(screen.get("x")) or 0.0, _num(screen.get("y")) or 0.0
        seen = {screen.get("workspace"), screen.get("special")} - {None, ""}
        visible = [w for w in wins if not seen or w["workspace"] in seen or w["workspace"] is None]
        wins = [w for w in visible or wins if ox - lw <= w["at"][0] < ox + 2 * lw
                and oy - lh <= w["at"][1] < oy + 2 * lh]
    else:
        lw = max(w["at"][0] + w["size"][0] for w in wins)
        lh = max(w["at"][1] + w["size"][1] for w in wins)
        ox = oy = 0.0
    if not wins:
        return ""
    active = bundle.get("activewindow") if isinstance(bundle.get("activewindow"), dict) else None
    bar = _bar_boxes(bundle, name)
    items = [*_boxes(wins, active, ox, oy), *bar][:60]

    # The canvas holds the screen and whatever sticks out of it, scaled to a fixed width; a character
    # cell is about twice as tall as it is wide.
    x0 = max(min([0.0] + [i["rect"][0] for i in items]), -lw)
    y0 = max(min([0.0] + [i["rect"][1] for i in items]), -lh)
    x1 = min(max([lw] + [i["rect"][0] + i["rect"][2] for i in items]), 2 * lw)
    y1 = min(max([lh] + [i["rect"][1] + i["rect"][3] for i in items]), 2 * lh)
    cols = PICTURE_COLS
    rows = max(6, min(26, round((y1 - y0) * cols / (x1 - x0) / 2)))
    sx, sy = cols / (x1 - x0), rows / (y1 - y0)
    grid = [[" "] * cols for _ in range(rows)]

    def cells(rect):
        c0 = min(max(round((rect[0] - x0) * sx), 0), cols - 2)
        r0 = min(max(round((rect[1] - y0) * sy), 0), rows - 2)
        c1 = min(max(round((rect[0] + rect[2] - x0) * sx) - 1, c0 + 1), cols - 1)
        r1 = min(max(round((rect[1] + rect[3] - y0) * sy) - 1, r0 + 1), rows - 1)
        return c0, r0, c1, r1

    def frame(rect, horizontal, vertical, corner, names=()):
        c0, r0, c1, r1 = cells(rect)
        for c in range(c0, c1 + 1):
            grid[r0][c] = grid[r1][c] = horizontal
        for r in range(r0, r1 + 1):
            grid[r][c0] = grid[r][c1] = vertical
        for r, c in ((r0, c0), (r0, c1), (r1, c0), (r1, c1)):
            grid[r][c] = corner
        label = _fit(names, c1 - c0 - 1) if r1 - r0 >= 2 else ""
        for i, ch in enumerate(label):
            grid[r0 + 1][c0 + 1 + i] = ch

    frame((0.0, 0.0, lw, lh), ".", ".", ".")
    for item in sorted(items, key=lambda i: (i["layer"], -i["rect"][2] * i["rect"][3])):
        names, n = item["names"], item["n"]
        if n > 1:
            names = (f"{names[0]} x{n}", f"{names[-1]}{n}", *names)
        style = {3: ("#", "#", "#"), 4: (":", ":", ":")}.get(item["layer"], ("-", "|", "+"))
        frame(item["rect"], *style, names)
    notes = [f"screen {round(lw)}x{round(lh)} (dots)"]
    if any(i["layer"] == 3 for i in items):
        notes.append("# is the active window")
    if bar:
        notes.append(": is the bar")
    return "\n".join("".join(row).rstrip() for row in grid) + "\n" + "; ".join(notes)


# -- building it --

def _get(finding, name: str, default=None):
    if isinstance(finding, dict):
        return finding.get(name, default)
    return getattr(finding, name, default)


def _read_bundle(folder) -> dict | None:
    try:
        bundle = json.loads((Path(folder) / "evidence.json").read_text())
    except (OSError, ValueError, TypeError):
        return None
    return bundle if isinstance(bundle, dict) else None


def _plain(v, limit: int = 40) -> str:
    """A version or a build: what a program said, cut to what such a word can be."""
    return re.sub(r"[^\w.+~ (),-]", "", str(v))[:limit].strip()


def _log_lines(bundle: dict, private: list[str]) -> tuple:
    """At most three lines of the evidence's log: the last ones that say something went wrong, else the
    last ones."""
    raw = [ln for ln in bundle.get("log", []) if isinstance(ln, str) and ln.strip()] \
        if isinstance(bundle.get("log"), list) else []
    bad = [ln for ln in raw if re.search(r"error|fail|fatal|warn|critical|crash|denied", ln, re.IGNORECASE)]
    return tuple(ln for ln in (_line(x, private) for x in (bad or raw)[-MAX_LOG:]) if ln)


def build(finding, bundle: dict | None = None, *, versions: dict | None = None, machine: str | None = None,
          patch: str | None = None, tried: Iterable[str] | None = None) -> Report:
    """The report for one finding (a `Finding`, or its `to_dict()`). The evidence is read from the
    finding's folder unless `bundle` is given; `versions` from the bundle unless given (a dict like
    `versions.collect()`); a suggested patch from `patch.diff` beside the evidence unless given. A
    finding with no evidence folder still gets a report, without the picture and the log."""
    folder = _get(finding, "evidence", "") or ""
    if bundle is None:
        bundle = _read_bundle(folder) if folder else None
    bundle = bundle if isinstance(bundle, dict) else {}
    private = _private_strings(bundle)
    if versions is None:
        versions = bundle.get("versions") if isinstance(bundle.get("versions"), dict) else {}
    if patch is None and folder:
        try:
            patch = (Path(folder) / "patch.diff").read_text()
        except (OSError, UnicodeDecodeError):
            patch = ""
    patch = patch or ""
    if len(patch) > MAX_PATCH:
        patch = ""       # too big for a link; it is held with the evidence

    component, fp = str(_get(finding, "component", "")), str(_get(finding, "fp", ""))
    lead = _TITLE_VERSION.get(component, "")
    lead_version = _plain(versions.get(lead, "")) if lead else ""
    lead_version = "" if lead_version == "unknown" else lead_version
    what = _headline(_line(_get(finding, "title", "") or _get(finding, "rule", ""), private, 100)) \
        or "Bombadil found a problem"
    title = f"{what}" + (f" ({lead} {lead_version})" if lead_version else "") + f" [fp {short_fp(fp)}]"

    probe = str(_get(finding, "probe", "") or bundle.get("probe") or "")
    probe = probe if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", probe) else ""
    observed = _line(_get(finding, "observed", ""), private, 200)
    note = ", ".join(x for x in ((f"probe {probe}" if probe else ""),
                                 ("1 retry" if bundle.get("kind") == "invariant" else "")) if x)
    shown = tuple((k, _plain(versions[k])) for k in _VERSION_ORDER
                  if k != lead and _plain(versions.get(k, "")) not in ("", "unknown"))
    kind = _plain(machine if machine is not None else versions.get("machine", ""), 30)
    n, days = _get(finding, "n", 1), _get(finding, "days", 1)
    return Report(
        fp=fp, title=title, n=n if isinstance(n, int) and n > 0 else 1,
        days=days if isinstance(days, int) and days > 0 else 1, build=_plain(versions.get("build", ""), 40),
        machine=kind, expected=_line(_get(finding, "expected", ""), private, 200),
        observed=f"{observed} ({note})" if note and observed else observed,
        picture=_picture(bundle), log=_log_lines(bundle, private), versions=shown,
        tried=tuple(_line(t, private) for t in tried) if tried is not None else (TRIED,),
        patch=patch.strip("\n"), what=what, check=f"{probe}: {observed}" if probe else observed)


# -- what the Send card shows --

@dataclass(frozen=True)
class Preview:
    goes: tuple     # what goes, each with what it is
    stays: tuple    # what stays on the machine
    text: str       # exactly what would be sent

    def to_dict(self) -> dict:
        return {"goes": list(self.goes), "stays": list(self.stays), "text": self.text}


def preview(report: Report) -> Preview:
    """The two lists of the Send card and the exact text. What goes is worded from the report's own
    fields, so it cannot say less than the text does."""
    goes = [f"{GOES[0]}: {report.what}, {report.times}"]
    if report.build:
        goes.append(f"{GOES[1]}: {report.build}")
    if report.versions:
        goes.append(f"{GOES[2]}: " + ", ".join(f"{k} {v}" for k, v in report.versions))
    goes.append(f"{GOES[3]}: {report.check}")
    if report.picture:
        goes.append(GOES[4])
    if report.log:
        lines = len(report.log)
        goes.append(f"{lines} line{'' if lines == 1 else 's'} of the log, with your folders removed")
    if report.patch:
        goes.append("A suggested patch")
    return Preview(tuple(goes), STAYS, report.render())


def hold(report: Report) -> Path | None:
    """Write the report where it waits for him: reports/<fingerprint>.md. None when the disk says no."""
    path = paths.loop_dir() / "reports" / f"{evidence_dir(report.fp).name}.md"
    try:
        db.private_dir(path.parent.parent)
        db.private_dir(path.parent)
        tmp = path.with_suffix(".tmp")
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.write(report.render())
        os.replace(tmp, path)
    except OSError:
        return None
    return path


# -- the project's issues --

def _get_json(url: str, timeout: float):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "bombadil"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(1_000_000))


def already_reported(fp: str, repo: str = DEFAULT_REPO, fetch: Callable | None = None,
                     timeout: float = 4.0) -> int | None:
    """The number of an issue in `repo` whose title carries this fingerprint ("[fp 3c91a0]"), open ones
    first, or None: not there, offline, or anything else. Never raises. `fetch(url, timeout)` gives
    the search answer as parsed JSON (the default asks GitHub, without a token). For after he pressed
    Send only: nothing is asked before that."""
    tag = f"[fp {short_fp(fp)}]"
    if not re.fullmatch(r"[0-9a-f]{4,12}", short_fp(fp)) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return None
    query = urllib.parse.quote(f'repo:{repo} is:issue "fp {short_fp(fp)}" in:title', safe="")
    url = f"https://api.github.com/search/issues?q={query}&per_page=10"
    try:
        found = (fetch or _get_json)(url, timeout)
        items = found.get("items") if isinstance(found, dict) else None
        hits = [i for i in items or [] if isinstance(i, dict) and isinstance(i.get("number"), int)
                and tag in str(i.get("title", ""))]
    except Exception:  # noqa: BLE001 - offline, rate limited, a proxy that answers in HTML: not found
        return None
    hits.sort(key=lambda i: i.get("state") != "open")
    return hits[0]["number"] if hits else None


# -- the link --

@dataclass(frozen=True)
class Link:
    url: str
    paste: bool = False      # the text did not fit in a link: the page opens empty
    copied: bool = False     # ... and it is on the clipboard

    @property
    def note(self) -> str:
        return "Paste it here" if self.paste and self.copied else ""


def copy_text(text: str, timeout: float = 3.0) -> bool:
    """Put text on the Wayland clipboard with wl-copy; False when there is none or it fails."""
    exe = shutil.which("wl-copy")
    if exe is None:
        return False
    try:
        proc = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    try:
        proc.communicate(text.encode(), timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        proc.kill()
        return False
    return proc.returncode == 0


def issue_url(report: Report, repo: str = DEFAULT_REPO,
              copy: Callable[[str], bool] | None = copy_text) -> Link:
    """https://github.com/<repo>/issues/new?title=&body=&labels=, quoted. When that is longer than
    MAX_URL the link is the empty new-issue page (`paste` is True) and the whole report goes through
    `copy` (the clipboard) instead, for him to paste; pass copy=None to only build the link."""
    base = f"https://github.com/{repo}/issues/new"
    query = urllib.parse.urlencode({"title": report.title, "body": report.body(), "labels": LABELS},
                                   quote_via=urllib.parse.quote)
    url = f"{base}?{query}"
    if len(url) <= MAX_URL:
        return Link(url)
    copied = False
    if copy is not None:
        try:
            copied = bool(copy(report.render()))
        except Exception:  # noqa: BLE001 - no clipboard is a card that says so, not a failure
            copied = False
    return Link(base, paste=True, copied=copied)


def _put_new_tab(url: str) -> bool:
    """Ask Chromium, through its debugging port, to open `url` in a new tab."""
    req = urllib.request.Request(f"http://127.0.0.1:{BROWSER_DEBUG_PORT}/json/new?{url}", method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return 200 <= resp.status < 300
    except (OSError, ValueError):
        return False


def _xdg_open(url: str) -> bool:
    exe = shutil.which("xdg-open")
    if exe is None:
        return False
    try:
        subprocess.Popen([exe, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True


def open_issue_page(url: str, hyprland=None, *, put: Callable[[str], bool] | None = None,
                    xdg: Callable[[str], bool] | None = None,
                    sleep: Callable[[float], None] = time.sleep) -> str:
    """Show the browser panel and open `url` in it; he presses Submit himself. "panel" when the page
    is open in the panel, "xdg-open" when it went to whatever opens links, "" when neither worked.
    The panel may have to start Chromium first, so this can take tens of seconds."""
    if not url.startswith("https://github.com/"):
        return ""
    put, xdg = put or _put_new_tab, xdg or _xdg_open
    try:
        h = hyprland if hyprland is not None else hypr.Hyprland()
        shown = bool(getattr(h, "available", True))
        if shown:
            h.panel("browser")
    except Exception:  # noqa: BLE001 - without the panel the page can still open
        shown = False
    for attempt in range(5 if shown else 1):
        if put(url):
            return "panel"
        if attempt < 4 and shown:
            sleep(0.5)       # Chromium was just started and its port is not open yet
    return "xdg-open" if xdg(url) else ""
