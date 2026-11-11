"""homefix: what a new Bombadil changes in the person's own home, as numbered steps.

The package owns /usr; the home is the person's. An update only adds what is missing there, with one
exception: an old hyprland.lua, which held Bombadil's part and the person's own lines together, is moved
aside once (hyprland.lua.before-split, never overwritten) and replaced by a short file that loads Bombadil's
part from the system and then the person's lines. agentd runs the steps at start, before its socket opens,
and only on an installed system (should_run): a checkout run by a developer must never touch their home.

Why it is shaped this way:

- Records are advisory. home.json says what was done, but every step also looks at the files, so a lost or
  set-aside home.json makes a step run again harmlessly, and a start with nothing to do reads a few files.
  The file is in the state folder, which a restore point of the home does not roll back.
- Never raises, never blocks. A step that fails is tried again at the next start, three times, then left alone;
  a second homefix holding the lock means this one does nothing. agentd restarts for ever, so a step that
  could crash it would be a loop with no pill.
- The person's lines are told from Bombadil's by comparing the old file with every text of Bombadil's part
  that was ever shipped (share/hypr/old, and the current one), taking the nearest. Only whole top-level
  statements are carried over. A line changed inside a shipped statement (a number in hl.config) cannot be
  moved out of it, and a fragment at the top level is a syntax error, which would stop Bombadil's part loading
  too: such lines are written as comments, and the old file is kept whole.
- Lines the person deleted from a shipped version come back through Bombadil's part. The written file says so.
"""

import difflib
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import paths, version

STATE_VERSION = 1
INSTALLED_SHARE = Path("/usr/share/bombadil")
SKEL = Path("/etc/skel")
KILL_SWITCH = "BOMBADIL_NO_HOMEFIX"
MAX_ATTEMPTS = 3
RELOAD_TIMEOUT = 3.0
MAX_LINES = 5000   # a file longer than this is not one of ours to compare line by line
HYPR_LUA = ".config/hypr/hyprland.lua"
BEFORE_SPLIT = "hyprland.lua.before-split"
LOAD_LINE = 'pcall(require, "/usr/share/bombadil/share/hypr/bombadil")'
LEFT_OVER = f"-- lines I could not carry over; your old file is {BEFORE_SPLIT}"
SKILLS = ("bombadil-apps", "bombadil-connect", "bombadil-mail")

# What /etc/skel's file says. The package's own copy (read at run time) wins; this is for a system whose skel
# lost it. tests/test_homefix.py keeps the two the same.
STUB = """\
-- Bombadil's part comes from the system and follows its updates. Your own lines go below.
local ok = pcall(require, "/usr/share/bombadil/share/hypr/bombadil")
if not ok then
    -- Bombadil's part is not on this system (a restore point from before it existed): keep the old file working.
    local old = os.getenv("HOME") .. "/.config/hypr/hyprland.lua.before-split"
    local f = io.open(old, "r")
    if f then
        f:close()
        dofile(old)
    else
        -- Nothing to fall back to: still start the agent and the bar, so there is a pill to ask.
        hl.on("hyprland.start", function()
            hl.exec_cmd("systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell")
        end)
    end
end

-- Your own lines go below. They run after Bombadil's, so a monitor or a bind here wins.
"""

# The skel entries of this release, for a system whose /etc/skel lacks them. tests/test_homefix.py keeps these
# in step with the package's skel, so an entry dropped from one is dropped from the other.
FALLBACK_FILES = {
    ".config/chromium-flags.conf": """\
# Read by Arch's chromium launcher. The browser panel passes its own flags (browser.py); these
# keep a Chromium started any other way from opening on its terms and first-run pages.
--no-first-run
--no-default-browser-check
--password-store=basic
""",
    ".config/bombadil/wallpaper": """\
# The picture under the desk. To use your own, write the path of an image on a line of its own,
# for example:
#
#   ~/Pictures/mountains.jpg
#
# The path, not the picture itself. The desk notices the change at once; your picture is dimmed a
# little so the cards and the pill stay easy to read over it. With no path here (or no file) the
# standard picture is shown. Lines that start with # are ignored.
""",
}


@dataclass
class Report:
    """What a run did: the outcome of each step by number, and lines worth telling the person."""
    steps: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class Result:
    outcome: str                  # "already", "stub-written", "stub-only", "migrated", "added", "seen"
    changed: bool = False         # something in the home was written
    extra: dict = field(default_factory=dict)


@dataclass
class Ctx:
    home: Path
    share: Path
    skel: Path
    doc: dict
    say: Callable[[str], None]
    notes: list[str] = field(default_factory=list)
    reload: bool = False


def should_run() -> bool:
    """Is this a real installed system? Not when the tree is a checkout or BOMBADIL_SHARE points anywhere, not
    without the package's VERSION file, and not with the kill switch set."""
    if os.environ.get(KILL_SWITCH) or os.environ.get("BOMBADIL_SHARE"):
        return False
    return paths.share_dir() == INSTALLED_SHARE and (INSTALLED_SHARE / "VERSION").is_file()


def _stderr(msg: str) -> None:
    print(f"agentd: homefix: {msg}", file=sys.stderr)


def run(home: Path | None = None, share: Path | None = None, log: Callable[[str], None] | None = None,
        skel: Path | None = None) -> Report:
    """Run every step that has something to do. Never raises. `share` is Bombadil's tree (/usr/share/bombadil)."""
    report = Report()
    say = log or _stderr
    try:
        _run(Path(home) if home else paths.home(), Path(share) if share else paths.share_dir(),
             Path(skel) if skel else SKEL, say, report)
    except Exception as e:  # noqa: BLE001 - a home that cannot be fixed must never stop the pill
        say(f"{type(e).__name__}: {e}")
    return report


def _run(home: Path, share: Path, skel: Path, say: Callable[[str], None], report: Report) -> None:
    state_path = paths.home_file()
    with _locked(state_path) as got:
        if not got:
            say("another homefix is running; leaving the home to it")
            return
        doc = _load(state_path, say)
        before = json.dumps(doc, sort_keys=True)
        ctx = Ctx(home, share, skel, doc, say)
        for number, name, step in STEPS:
            key = str(number)
            failed = doc["failed"].get(key, {})
            if failed.get("attempts", 0) >= MAX_ATTEMPTS:
                continue
            try:
                result = step(ctx)
            except Exception as e:  # noqa: BLE001 - one step failing must not hold up the next
                attempts = failed.get("attempts", 0) + 1
                doc["failed"][key] = {"attempts": attempts, "at": _now(),
                                      "error": f"{type(e).__name__}: {e}"[:300]}
                say(f"step {number} ({name}) failed, attempt {attempts} of {MAX_ATTEMPTS}: {type(e).__name__}: {e}")
                continue
            doc["failed"].pop(key, None)
            report.steps[key] = result.outcome
            if result.changed or key not in doc["steps"]:
                doc["steps"][key] = {"at": _now(), "bombadil": version.read() or None, "result": result.outcome,
                                     **result.extra}
        report.notes.extend(ctx.notes)
        if json.dumps(doc, sort_keys=True) != before:
            try:
                _save(state_path, doc)
            except OSError as e:
                say(f"could not save {state_path}: {e}")
        if ctx.reload:
            _reload(say)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# -- the state file --

@contextmanager
def _locked(state_path: Path) -> Iterator[bool]:
    """Hold home.json.lock, or say False at once when someone else does: a start never waits for this."""
    lock = state_path.with_name(state_path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def _empty() -> dict:
    return {"version": STATE_VERSION, "steps": {}, "skel_seen": [], "failed": {}}


def _load(path: Path, say: Callable[[str], None]) -> dict:
    """home.json as it was saved. One that cannot be read, or is of a version this code does not know, is set
    aside beside itself and the table starts empty (as proposals.json does); the steps look at the files anyway."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except (OSError, UnicodeDecodeError) as e:
        say(f"{path} cannot be read ({e})")
        _set_aside(path, say)
        return _empty()
    try:
        doc = json.loads(text)
        if not isinstance(doc, dict) or doc.get("version") != STATE_VERSION:
            raise ValueError("not a table of this version")
        if (not isinstance(doc.get("steps"), dict) or not isinstance(doc.get("failed"), dict)
                or not isinstance(doc.get("skel_seen"), list) or not all(isinstance(s, str) for s in doc["skel_seen"])):
            raise ValueError("not a table of this version")
    except (ValueError, RecursionError):
        say(f"{path} is not a table this version knows")
        _set_aside(path, say)
        return _empty()
    # A failure record that was edited into nonsense is only forgotten: the step gets its attempts back.
    doc["failed"] = {k: v for k, v in doc["failed"].items() if isinstance(v, dict) and isinstance(v.get("attempts"), int)}
    return doc


def _set_aside(path: Path, say: Callable[[str], None]) -> None:
    try:
        path.replace(path.with_name(f"{path.name}.bad-{int(time.time())}"))
    except OSError as e:
        say(f"could not set {path} aside: {e}")


def _save(path: Path, doc: dict) -> None:
    _write(path, (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode(), 0o600)


# -- writing files --

def _beside(path: Path) -> Path:
    return path.with_name(f".{path.name}.homefix-{os.getpid()}")


def _stage(path: Path, data: bytes, mode: int) -> Path:
    """The bytes in a file beside `path`, flushed to disk, with exactly this mode (the umask does not narrow it)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _beside(path)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, mode)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), mode)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return tmp


def _write(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Replace `path` with `data`, all or nothing: written beside it, flushed to disk, then renamed over it. A
    crash leaves the old file or the new one, never half of either."""
    try:
        os.replace(_stage(path, data, mode), path)
    finally:
        _beside(path).unlink(missing_ok=True)
    _sync_dir(path.parent)


def _create_only(path: Path, data: bytes, mode: int = 0o644) -> bool:
    """Write `path` only when nothing is at that name (a symlink, even a dangling one, counts); True when it did.
    The bytes are written and flushed beside it and linked in, so the name never holds half a file and a name that
    appears meanwhile is not overwritten."""
    try:
        tmp = _stage(path, data, mode)
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        except OSError:
            # No hard links on this file system: a rename, but only if the name is still free.
            if os.path.lexists(path):
                return False
            os.rename(tmp, path)
        return True
    finally:
        _beside(path).unlink(missing_ok=True)
        _sync_dir(path.parent)


def _sync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _reload(say: Callable[[str], None]) -> None:
    """Ask the running Hyprland to read its config again: on the first sign-in after an update it loaded the old
    file before agentd started. Best effort; there is nothing to ask in a session without Hyprland."""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return
    hyprctl = shutil.which("hyprctl")
    if hyprctl is None:
        return
    try:
        subprocess.run([hyprctl, "reload"], capture_output=True, timeout=RELOAD_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        say(f"hyprctl reload: {type(e).__name__}: {e}")


# -- Lua, as much of it as telling top-level statements apart takes --

_OPENERS = frozenset({"function", "if", "do", "repeat"})
_CLOSERS = frozenset({"end", "until"})
_LONG_OPEN = re.compile(r"\[(=*)\[")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"[0-9][0-9A-Za-z_.]*")
_HL_CALL = re.compile(r"hl\.[A-Za-z_]")


@dataclass
class Row:
    code: str       # the line with comments taken out and each string folded to ""
    end: int        # how deep the line leaves us in brackets and blocks
    open: bool      # it ends inside a long string or comment
    bad: bool       # a string left open on it, or a closer with nothing to close


def scan(lines: list[str]) -> list[Row]:
    """Read lines of Lua for nesting: (), {}, [], and function/if/do/repeat ... end/until, skipping strings and
    comments. Not a parser; enough to know where a top-level statement starts and ends."""
    rows: list[Row] = []
    depth = 0
    closing = ""    # what ends the long string or comment we are inside
    for raw in lines:
        code: list[str] = []
        bad = False
        i, n = 0, len(raw)
        while i < n:
            if closing:
                j = raw.find(closing, i)
                if j < 0:
                    break
                i, closing = j + len(closing), ""
                continue
            c = raw[i]
            if raw.startswith("--", i):
                m = _LONG_OPEN.match(raw, i + 2)
                if not m:
                    break
                closing, i = "]" + m.group(1) + "]", m.end()
                continue
            if c in "\"'":
                j = i + 1
                while j < n and raw[j] != c:
                    j += 2 if raw[j] == "\\" else 1
                if j >= n:
                    bad = True
                    break
                code.append('""')
                i = j + 1
                continue
            if c == "[" and (m := _LONG_OPEN.match(raw, i)):
                closing, i = "]" + m.group(1) + "]", m.end()
                code.append('""')
                continue
            if m := _WORD.match(raw, i):
                word = m.group()
                depth += 1 if word in _OPENERS else -1 if word in _CLOSERS else 0
                code.append(word)
                i = m.end()
                continue
            if m := _NUMBER.match(raw, i):
                code.append(m.group())
                i = m.end()
                continue
            depth += 1 if c in "([{" else -1 if c in ")]}" else 0
            code.append(c)
            i += 1
        if depth < 0:
            bad, depth = True, 0
        rows.append(Row("".join(code).strip(), depth, bool(closing), bad))
    return rows


def statements(rows: list[Row]) -> list[tuple[int, int, bool]]:
    """The top-level statements as (first line, line after the last, fit): a statement runs until the nesting is
    back to nothing. Not fit: something was left open or closed too often in it."""
    out = []
    i = 0
    while i < len(rows):
        j, bad = i, False
        while True:
            bad = bad or rows[j].bad
            closed = rows[j].end == 0 and not rows[j].open
            j += 1
            if closed or j >= len(rows):
                break
        out.append((i, j, bad or not closed))
        i = j
    return out


def _carryable(rows: list[Row], first: int, last: int, bad: bool) -> bool:
    """Whole statements only: a comment or a blank, or a call of hl. that is not a table field's trailing comma."""
    if bad:
        return False
    code = " ".join(r.code for r in rows[first:last] if r.code)
    return not code or (bool(_HL_CALL.match(code)) and not code.endswith(","))


def _lua_parses(text: str) -> bool | None:
    """Does luac accept it? None when there is no luac to ask (the image may not have one)."""
    for name in ("luac", "luac5.4", "luac5.3", "luac5.1"):
        luac = shutil.which(name)
        if luac is None:
            continue
        try:
            done = subprocess.run([luac, "-p", "-"], input=text.encode("utf-8", "surrogateescape"),
                                  capture_output=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.returncode == 0
    return None


# -- step 1: the config split --

def _stub(skel: Path) -> str:
    """The stub as the package's skel has it, else the built-in one."""
    try:
        text = (skel / HYPR_LUA).read_text(encoding="utf-8")
        if LOAD_LINE in text:
            return text if text.endswith("\n") else text + "\n"
    except (OSError, UnicodeDecodeError):
        pass
    return STUB


def _shipped(share: Path) -> list[tuple[str, list[str]]]:
    """Every text of Bombadil's part there has been: share/hypr/old/*.lua, then the current one."""
    hypr = share / "share" / "hypr"
    found = []
    for path in sorted((hypr / "old").glob("*.lua")) + [hypr / "bombadil.lua"]:
        try:
            found.append((path.name, path.read_text(encoding="utf-8", errors="surrogateescape").splitlines()))
        except OSError:
            continue
    return found


def _nearest(shipped: list[tuple[str, list[str]]], user: list[str]) -> list[str]:
    """The shipped text with the most lines in common with the file (then the closest, then the newest)."""
    best, best_key = [], None
    for index, (_, lines) in enumerate(shipped):
        sm = difflib.SequenceMatcher(None, [s.strip() for s in lines], user, autojunk=False)
        matched = sum(block.size for block in sm.get_matching_blocks())
        key = (matched, sm.ratio(), index)
        if best_key is None or key > best_key:
            best, best_key = lines, key
    return best


def split(text: str, share: Path, stub: str) -> tuple[str, int, int]:
    """The new hyprland.lua for an old one: (its text, statements carried over, lines of code left as comments)."""
    shipped = _shipped(share)
    if not any(name == "bombadil.lua" for name, _ in shipped):
        raise RuntimeError(f"Bombadil's part is not in {share / 'share' / 'hypr'}")
    mine_lines = text.splitlines()
    if len(mine_lines) > MAX_LINES:
        raise ValueError(f"hyprland.lua has {len(mine_lines)} lines")
    user = [s.strip() for s in mine_lines]
    theirs = _nearest(shipped, user)
    ops = difflib.SequenceMatcher(None, [s.strip() for s in theirs], user, autojunk=False).get_opcodes()
    mine = [False] * len(user)
    removed = False
    for tag, i1, i2, j1, j2 in ops:
        if tag in ("insert", "replace"):
            mine[j1:j2] = [True] * (j2 - j1)
        if tag in ("delete", "replace"):
            removed = removed or any(s.strip() and not s.strip().startswith("--") for s in theirs[i1:i2])
    # What Bombadil shipped at any time is Bombadil's, wherever the file has it (a file made of two versions).
    ever = set()
    for _, lines in shipped:
        rows = scan(lines)
        ever.update("\n".join(s.strip() for s in lines[a:b]) for a, b, _ in statements(rows))
    rows = scan(mine_lines)
    carried: list[tuple[int, int]] = []
    left: list[str] = []
    for first, last, bad in statements(rows):
        if not any(mine[first:last]):
            continue
        if all(mine[first:last]):
            if not any(user[first:last]) or "\n".join(user[first:last]) in ever:
                continue
            if _carryable(rows, first, last, bad):
                carried.append((first, last))
                continue
        left += [user[k] for k in range(first, last) if mine[k] and user[k]]
    text_out = _compose(stub, mine_lines, carried, left, removed)
    if carried and _lua_parses(text_out) is False:
        # luac does not like what we are about to write, and a syntax error stops Bombadil's part too.
        left = [user[k] for a, b in carried for k in range(a, b) if user[k]] + left
        carried = []
        text_out = _compose(stub, mine_lines, carried, left, removed)
    kept = sum(1 for a, b in carried if any(rows[k].code for k in range(a, b)))
    # A comment the person changed inside one of Bombadil's statements is kept in the file, but is nothing to tell them.
    return text_out, kept, sum(1 for line in left if not line.startswith("--"))


def _compose(stub: str, lines: list[str], carried: list[tuple[int, int]], left: list[str], removed: bool) -> str:
    """The stub, then what is kept of the old file (with a note or two on it), then what could not be."""
    sections = [stub if stub.endswith("\n") else stub + "\n"]
    mine: list[str] = []
    if carried:
        mine.append(f"-- Kept from your old file; all of it is in {BEFORE_SPLIT}.")
    if removed:
        mine.append("-- A line you changed or took out in Bombadil's part is back: that part comes from the system")
        mine.append("-- as a whole. To change one, write your own line below.")
    prev_end = None
    block: list[str] = []
    for first, end in carried:
        if prev_end is not None and first != prev_end:
            block.append("")
        block += [lines[k].rstrip() for k in range(first, end)]
        prev_end = end
    while block and not block[0]:
        block.pop(0)
    while block and not block[-1]:
        block.pop()
    mine += block
    if mine:
        sections.append("\n".join(mine) + "\n")
    if left:
        sections.append(LEFT_OVER + "\n" + "".join(f"-- {line}\n" for line in left))
    return "\n".join(sections)


def _keep_old(path: Path, raw: bytes) -> None:
    """hyprland.lua.before-split: the old file as it was, and only if that name is free."""
    mode = stat.S_IMODE(path.stat().st_mode)
    _create_only(path.with_name(BEFORE_SPLIT), raw, mode)


def step_config_split(ctx: Ctx) -> Result:
    target = ctx.home / HYPR_LUA
    stub = _stub(ctx.skel)
    if not os.path.lexists(target):
        _write(target, stub.encode())
        ctx.reload = True
        return Result("stub-written", True)
    raw = target.read_bytes()
    text = raw.decode("utf-8", "surrogateescape")
    if LOAD_LINE in text:
        return Result("already")
    new, kept, left = split(text, ctx.share, stub)
    _keep_old(target, raw)
    _write(target, new.encode("utf-8", "surrogateescape"), stat.S_IMODE(target.stat().st_mode))
    ctx.reload = True
    if left:
        lines = f"{left} line{'s' if left != 1 else ''}"
        ctx.notes.append(f"I could not carry over {lines} of your old Hyprland settings. "
                         f"They are commented out in hyprland.lua, and your old file is {BEFORE_SPLIT}.")
    return Result("stub-only" if new == stub else "migrated", True, {"kept": kept, "left": left})


# -- step 2: skel entries that are missing --

@dataclass
class Entry:
    rel: str
    link: str | None = None            # a symlink's target
    source: Path | None = None         # a file's source in skel
    data: bytes = b""                  # or its text, from the built-in list
    mode: int = 0o644


def _may_offer(rel: str) -> bool:
    """Which skel entries may be added. Sign-in and the person's settings are never offered: the skill links are
    all that comes into .claude, and the wallpaper's note all that comes into .config/bombadil."""
    parts = PurePosixPath(rel).parts
    if rel == HYPR_LUA:
        return False
    if parts[0] in (".claude", ".agents"):
        return len(parts) == 3 and parts[1] == "skills"
    if parts[:2] == (".config", "bombadil"):
        return parts == (".config", "bombadil", "wallpaper")
    # The skel also holds other packages' dotfiles (.bashrc and the like): a home that lacks one on purpose
    # keeps it gone. Only the settings under .config are Bombadil's to bring.
    return parts[0] == ".config"


def _skel_entries(skel: Path, share: Path) -> list[Entry]:
    found: list[Entry] = []

    def walk(directory: Path, prefix: str) -> None:
        try:
            listing = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for e in listing:
            rel = prefix + e.name
            if e.is_symlink():
                found.append(Entry(rel, link=os.readlink(e.path)))
            elif e.is_dir(follow_symlinks=False):
                walk(Path(e.path), rel + "/")
            elif e.is_file(follow_symlinks=False):
                mode = stat.S_IMODE(e.stat(follow_symlinks=False).st_mode)
                found.append(Entry(rel, source=Path(e.path), mode=mode))

    walk(skel, "")
    have = {e.rel for e in found}
    built_in = [Entry(f"{tool}/skills/{name}", link=str(share / "share" / "skills" / name))
                for tool in (".claude", ".agents") for name in SKILLS]
    built_in += [Entry(rel, data=text.encode()) for rel, text in FALLBACK_FILES.items()]
    return found + [e for e in built_in if e.rel not in have]


def _make_parents(home: Path, skel: Path, rel: str) -> None:
    """The folders above an entry, made as the skel has them (their modes too), where they are missing."""
    here, there = home, skel
    for part in PurePosixPath(rel).parent.parts:
        here, there = here / part, there / part
        if not os.path.lexists(here):
            here.mkdir()
            try:
                os.chmod(here, stat.S_IMODE(there.stat().st_mode))
            except OSError:
                pass


def step_skel_entries(ctx: Ctx) -> Result:
    """Add what /etc/skel has and the home lacks (new skill links, new settings files), as `cp -an` does: nothing
    that exists is opened, changed or removed. Each path is offered once, so one the person deleted stays gone."""
    seen = set(ctx.doc["skel_seen"])
    added: list[str] = []
    errors: list[str] = []
    for entry in _skel_entries(ctx.skel, ctx.share):
        if entry.rel in seen or not _may_offer(entry.rel):
            continue
        dest = ctx.home / entry.rel
        try:
            if not os.path.lexists(dest):
                _make_parents(ctx.home, ctx.skel, entry.rel)
                try:
                    if entry.link is not None:
                        os.symlink(entry.link, dest)
                    else:
                        _create_only(dest, entry.source.read_bytes() if entry.source else entry.data, entry.mode)
                    added.append(entry.rel)
                except FileExistsError:
                    pass       # it appeared meanwhile: it is the person's
        except OSError as e:
            errors.append(f"{entry.rel}: {e}")
            continue
        ctx.doc["skel_seen"].append(entry.rel)
        seen.add(entry.rel)
    if errors:
        raise OSError("; ".join(errors))
    return Result("added" if added else "seen", bool(added), {"added": added} if added else {})


STEPS = (
    (1, "config split", step_config_split),
    (2, "skel entries", step_skel_entries),
)
