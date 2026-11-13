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
- Never raises, never blocks. A step that fails is tried again at the next start, but a failure counts at most
  once an hour, and the third count leaves the step alone until a newer Bombadil is installed (and says so once);
  a second homefix holding the lock means this one does nothing. agentd restarts for ever, so a step that
  could crash it would be a loop with no pill.
- The person's lines are told from Bombadil's by comparing the old file with every text of Bombadil's part
  that was ever shipped (share/hypr/old, and the current one), taking the nearest, statement by statement
  (and line by line only inside statements that differ). Whole top-level statements are carried over. A value
  changed inside hl.config is carried as an hl.config call of its own, which sets just that value after
  Bombadil's. A fragment at the top level is a syntax error, which would stop Bombadil's part loading too:
  what cannot be carried whole is written as comments, and the old file is kept whole.
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
RETRY_AFTER_SECS = 3600   # a failure counts once in this long, so a crash loop does not use the attempts up
RELOAD_TIMEOUT = 3.0
MAX_LINES = 5000   # a file longer than this is not one of ours to compare line by line
HYPR_LUA = ".config/hypr/hyprland.lua"
BEFORE_SPLIT = "hyprland.lua.before-split"
LOAD_LINE = 'pcall(require, "/usr/share/bombadil/share/hypr/bombadil")'
LEFT_OVER = "-- lines I could not carry over; your old file is"
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
        -- That file holds your own lines too: stop here, so they do not run twice.
        return
    else
        -- Nothing to fall back to: still start the agent and the bar, so there is a pill to ask.
        hl.on("hyprland.start", function()
            hl.exec_cmd("systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell")
        end)
    end
end

-- Your own lines go below. They run after Bombadil's.
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
        if doc is None:
            say(f"{state_path} was written by a newer Bombadil; leaving the home as it is")
            return
        before = json.dumps(doc, sort_keys=True)
        ctx = Ctx(home, share, skel, doc, say)
        current = version.read() or None
        for number, name, step in STEPS:
            key = str(number)
            failed = doc["failed"].get(key, {})
            if failed and failed.get("bombadil") != current:
                # A newer (or older) Bombadil may not fail where this one did: the step gets its attempts back.
                failed = {}
                doc["failed"].pop(key, None)
            attempts = failed.get("attempts", 0)
            if attempts >= MAX_ATTEMPTS:
                continue
            try:
                result = step(ctx)
            except Exception as e:  # noqa: BLE001 - one step failing must not hold up the next
                now = time.time()
                error = f"{type(e).__name__}: {e}"[:300]
                counted = now - failed.get("epoch", 0) >= RETRY_AFTER_SECS
                if counted:
                    attempts += 1
                    doc["failed"][key] = {"attempts": attempts, "at": _now(), "epoch": int(now), "error": error,
                                          "bombadil": current}
                    if attempts >= MAX_ATTEMPTS:
                        ctx.notes.append(f"I could not finish a repair in your home folder ({name}) after "
                                         f"{MAX_ATTEMPTS} tries. I will try again after the next update. {error[:80]}")
                say(f"step {number} ({name}) failed, attempt {attempts} of {MAX_ATTEMPTS}"
                    f"{'' if counted else ' (not counted: the last one was less than an hour ago)'}: {error}")
                continue
            doc["failed"].pop(key, None)
            report.steps[key] = result.outcome
            if result.changed or key not in doc["steps"]:
                doc["steps"][key] = {"at": _now(), "bombadil": current, "result": result.outcome, **result.extra}
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


def _load(path: Path, say: Callable[[str], None]) -> dict | None:
    """home.json as it was saved. One that cannot be read, or is not a table this code can use, is set aside beside
    itself and the table starts empty (as proposals.json does); the steps look at the files anyway. One written by
    a newer Bombadil is left where it is and None is returned: this code cannot tell what its records mean (a
    deleted skel entry would come back), so it does nothing."""
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
        newer = doc.get("version") if isinstance(doc, dict) else None
        if isinstance(newer, int) and not isinstance(newer, bool) and newer > STATE_VERSION:
            return None
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
    doc["failed"] = {k: v for k, v in doc["failed"].items()
                     if isinstance(v, dict) and isinstance(v.get("attempts"), int)
                     and isinstance(v.get("epoch", 0), (int, float))}
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
_CARRY_START = re.compile(r"(?:hl\s*\.\s*[A-Za-z_]|(?:local|function|require|dofile|if|for|while|do)(?![A-Za-z0-9_]))")
_LOCAL = re.compile(r"(?<![\w.:])local\s+(?:function\s+)?([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)")
_FUNCTION = re.compile(r"function\s+([A-Za-z_]\w*)")
_ASSIGNED = re.compile(r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=(?!=)")
# A name that is used, not a field (after a single dot or a colon) or a key. The second dot of `..` is not a field
# dot: in `"x"..mod` the name is used.
_USED = re.compile(r"(?<![\w:])(?<!(?<!\.)\.)[A-Za-z_]\w*(?!\w)(?!\s*=(?!=))")
_KEY_OPEN = re.compile(r"([A-Za-z_]\w*) = \{")
_FIELD = re.compile(r"[A-Za-z_]\w* =(?!=)")
_VALUE_END = re.compile(r"(?:[A-Za-z_]\w*|[0-9][\w.]*|\"\"|[)\]}])\Z")
_RESERVED = frozenset({"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if", "in",
                       "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"})
# A statement is not over after one of these (the expression, the assignment or the list goes on), and it goes on
# into a row that starts with one of the next set, which no statement can start with.
_UNFINISHED = frozenset({"..", "+", "-", "*", "/", "%", "^", "#", "&", "|", "~", "<", ">", "=", ",", ".", ":",
                         "and", "or", "not", "in", "local", "until", "for", "while"})
_CONTINUING = frozenset({"..", ".", ":", "=", ",", "+", "-", "*", "/", "%", "^", "&", "|", "~", "<", ">", "and", "or",
                         "in"})
# Lua reads a row that starts with one of these as a call or an index of what ends the row before it.
_CALL_START = frozenset({"(", "[", "{", '""'})


@dataclass
class Row:
    code: str       # the line with comments taken out and each string folded to ""
    end: int        # how deep the line leaves us in brackets and blocks
    open: bool      # it ends inside a long string or comment
    bad: bool       # a string left open on it, or a closer with nothing to close
    head: str = ""  # its first token (a string is "", a long string too), empty when it has no code
    tail: str = ""  # its last token


def scan(lines: list[str]) -> list[Row]:
    """Read lines of Lua for nesting: (), {}, [], and function/if/do/repeat ... end/until, skipping strings and
    comments. Not a parser; enough to know where a top-level statement starts and ends."""
    rows: list[Row] = []
    depth = 0
    closing = ""    # what ends the long string or comment we are inside
    for raw in lines:
        code: list[str] = []
        tokens: list[str] = []
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
                tokens.append('""')
                i = j + 1
                continue
            if c == "[" and (m := _LONG_OPEN.match(raw, i)):
                closing, i = "]" + m.group(1) + "]", m.end()
                code.append('""')
                tokens.append('""')
                continue
            if m := _WORD.match(raw, i):
                word = m.group()
                depth += 1 if word in _OPENERS else -1 if word in _CLOSERS else 0
                code.append(word)
                tokens.append(word)
                i = m.end()
                continue
            if m := _NUMBER.match(raw, i):
                code.append(m.group())
                tokens.append(m.group())
                i = m.end()
                continue
            depth += 1 if c in "([{" else -1 if c in ")]}" else 0
            if c.isspace():
                code.append(c)
                i += 1
                continue
            # `..`, `...` and `::` are one token: a row that ends in `..` is not over, one that ends in `...` is.
            op = next((o for o in ("...", "..", "::") if raw.startswith(o, i)), c)
            code.append(op)
            tokens.append(op)
            i += len(op)
        if depth < 0:
            bad, depth = True, 0
        rows.append(Row("".join(code).strip(), depth, bool(closing), bad, tokens[0] if tokens else "",
                        tokens[-1] if tokens else ""))
    return rows


def _goes_on(start: str, tail: str, head: str) -> bool:
    """Is a statement that began with `start` and has code up to `tail` still going on in a row that begins with
    `head`? Lua reads on after an operator, an `=` or a comma, into a row that cannot start a statement (`.. x`,
    `:m()`), into a `(` or an index or a table or a string after a name or a call (they are a call or an index of
    it), and into the `do` of a loop whose head is over."""
    if not tail:
        return False
    if tail in _UNFINISHED or head in _CONTINUING:
        return True
    if head in _CALL_START and (tail in (")", "]") or (_WORD.fullmatch(tail) and tail not in _RESERVED)):
        return True
    return start in ("for", "while") and tail not in ("end", ";") and head == "do"


def statements(rows: list[Row]) -> list[tuple[int, int, bool]]:
    """The top-level statements as (first line, line after the last, fit): a statement runs until the nesting is
    back to nothing and it is not going on (a row that ends in `..` or a comma, or the next row starting with `..`,
    is one statement with the one it goes on in). Not fit: something was left open or closed too often in it."""
    after = [""] * len(rows)        # the first token of the next row that has code
    following = ""
    for k in range(len(rows) - 1, -1, -1):
        after[k] = following
        following = rows[k].head or following
    out = []
    i = 0
    while i < len(rows):
        j, bad, start, tail = i, False, "", ""
        while True:
            row = rows[j]
            bad = bad or row.bad
            start, tail = start or row.head, row.tail or tail
            closed = row.end == 0 and not row.open and (row.bad or not _goes_on(start, tail, after[j]))
            j += 1
            if closed or j >= len(rows):
                break
        out.append((i, j, bad or not closed))
        i = j
    return out


def _carryable(rows: list[Row], first: int, last: int, bad: bool) -> bool:
    """Whole statements only: a comment or a blank, or a call of hl., a local, a function, a require or dofile, or
    a block (if, for, while, do), that is not a table field's trailing comma."""
    if bad:
        return False
    code = " ".join(r.code for r in rows[first:last] if r.code)
    return not code or (bool(_CARRY_START.match(code)) and not code.endswith(","))


def _defines(code: str) -> set[str]:
    """The names a statement makes: its locals, the function it declares, a global it sets."""
    found: set[str] = set()
    for m in _LOCAL.finditer(code):
        found.update(_WORD.findall(m.group(1)))
    if m := _FUNCTION.match(code):
        found.add(m.group(1))
    elif m := _ASSIGNED.match(code):
        found.update(_WORD.findall(m.group(1)))
    return found


def _names_used(code: str) -> set[str]:
    """The names a statement uses: its words, but not a table field (after a dot or colon) or the key of one."""
    return set(_USED.findall(code))


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


@dataclass
class Piece:
    """Something to write under "Kept from your old file": the person's own lines as they were, or an hl.config call
    rebuilt around values they changed. `source` is what stands in for it as comments if it cannot be kept."""
    first: int          # the lines of the old file it comes from, first and one past the last
    last: int
    lines: list[str]    # what is written
    source: list[str]   # the old file's own lines
    code: str           # its code
    uses: set[str] = field(init=False)

    def __post_init__(self):
        self.uses = _names_used(self.code)


def _statement_texts(lines: list[str]) -> tuple[list[tuple[int, int, bool]], list[str]]:
    """The statements of a text, and each one as its stripped lines joined: what two texts are compared by. A blank
    line is left out: every text has many, a blank matched across a statement the person changed would pair the
    shipped one with nothing (it would be taken out) and the changed one with nothing (it would be all theirs)."""
    spans = [span for span in statements(scan(lines)) if any(s.strip() for s in lines[span[0]:span[1]])]
    return spans, ["\n".join(s.strip() for s in lines[a:b]) for a, b, _ in spans]


def _lines_of(spans: list[tuple[int, int, bool]], first: int, end: int) -> tuple[int, int]:
    """The lines that statements first..end-1 cover."""
    return (spans[first][0], spans[end - 1][1]) if end > first else (0, 0)


def _persons_lines(theirs: list[str], mine_lines: list[str]) -> tuple[list[bool], bool]:
    """Which lines of the old file are not Bombadil's, and whether a shipped line is missing from it. Statements
    are compared first: a statement the person added right after a shipped one that ends the same way (`end)`,
    `})`) is a statement of its own, where a line by line comparison would match its closing line to the shipped
    one's and tear it in two. Lines are compared only inside statements that differ."""
    user = [s.strip() for s in mine_lines]
    their_lines = [s.strip() for s in theirs]
    their_spans, their_keys = _statement_texts(theirs)
    user_spans, user_keys = _statement_texts(mine_lines)
    mine = [False] * len(user)
    removed = False
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, their_keys, user_keys, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        t1, t2 = _lines_of(their_spans, i1, i2)
        u1, u2 = _lines_of(user_spans, j1, j2)
        if tag == "replace":
            inner = difflib.SequenceMatcher(None, their_lines[t1:t2], user[u1:u2], autojunk=False).get_opcodes()
        else:
            inner = [(tag, 0, t2 - t1, 0, u2 - u1)]
        for kind, a1, a2, b1, b2 in inner:
            if kind in ("insert", "replace"):
                mine[u1 + b1:u1 + b2] = [True] * (b2 - b1)
            if kind in ("delete", "replace"):
                removed = removed or any(s and not s.startswith("--") for s in their_lines[t1 + a1:t1 + a2])
    return mine, removed


def _ends_a_value(code: str) -> bool:
    """Does the code end where a value can end (a name, a number, a string, a closing bracket), and not on an operator
    that wants something after it?"""
    m = _VALUE_END.search(code)
    return bool(m) and m.group() not in ("and", "or", "not")


def _leaf(lines: list[str], rows: list[Row], k: int, last: int) -> str | None:
    """The field on row k as it is written into a call of its own, or None when it cannot be placed for sure. A field
    that ends in a comma is taken as it is, and one that ends in a `;` gets a comma instead (a comma after a `;` is
    a syntax error). One with neither gets a comma only where a value ends and the next row is a field, a table
    opening or a closer: after `3 +` or `"a" or` a comma would cut the value off. A `--` in the line hides where
    the code ends, so a field that does not end in a comma is left then."""
    code, leaf = rows[k].code, lines[k].strip()
    if code.endswith(","):
        return leaf
    if "--" in leaf:
        return None
    if code.endswith(";"):
        return leaf[:-1].rstrip() + "," if leaf.endswith(";") and _ends_a_value(code[:-1].rstrip()) else None
    nxt = next((r.code for r in rows[k + 1:last] if r.code), "")
    if _ends_a_value(code) and (_FIELD.match(nxt) or _KEY_OPEN.fullmatch(nxt) or nxt.startswith("}")):
        return leaf + ","
    return None


def _config_pieces(lines: list[str], rows: list[Row], first: int, last: int,
                   mine: list[bool]) -> tuple[list[Piece], set[int]]:
    """The person's lines inside an `hl.config({ ... })` call of Bombadil's (a value they changed, a key they added)
    as calls of their own: `gaps_in = 3,` inside `general = {` becomes `hl.config({ general = { gaps_in = 3, }, })`,
    which Hyprland applies after Bombadil's call and which sets just that value. Only lines it can place for sure
    are rebuilt: a field on one line, in tables that each open on a line of their own. Returns the pieces and the
    lines they stand for (those lines, and the `key = {` and `},` of a table the person added around them)."""
    if rows[first].code != "hl.config({":
        return [], set()
    base = rows[first].end
    path: list[str] = []
    leaves: dict[tuple[str, ...], list[tuple[int, str]]] = {}     # per table: its fields, as written into the call
    shape: list[int] = []
    for k in range(first + 1, last):
        row, before = rows[k], rows[k - 1].end
        # A row that ends inside a long string or comment is not a field on one line. (A bad row cannot be here:
        # split() does not ask about a statement that has one.)
        if row.open or before != base + len(path):
            break
        if not row.code:
            continue
        if row.end == before and _FIELD.match(row.code):
            if mine[k] and (leaf := _leaf(lines, rows, k, last)) is not None:
                leaves.setdefault(tuple(path), []).append((k, leaf))
        elif row.end == before + 1 and (m := _KEY_OPEN.fullmatch(row.code)):
            path.append(m.group(1))
            if mine[k]:
                shape.append(k)
        elif row.end == before - 1 and path and row.code in ("}", "},"):
            path.pop()
            if mine[k]:
                shape.append(k)
        else:
            break
    pieces = []
    for where, rows_of in leaves.items():
        out = ["hl.config({"]
        out += [f"{'    ' * depth}{key} = {{" for depth, key in enumerate(where, 1)]
        out += ["    " * (len(where) + 1) + leaf for _, leaf in rows_of]
        out += [f"{'    ' * depth}}}," for depth in range(len(where), 0, -1)]
        out.append("})")
        ks = [k for k, _ in rows_of]
        pieces.append(Piece(ks[0], ks[-1] + 1, out, [lines[k].strip() for k in ks], " ".join(rows[k].code for k in ks)))
    covered = {k for rows_of in leaves.values() for k, _ in rows_of}
    return pieces, covered | set(shape) if covered else set()


def _lines(text: str) -> list[str]:
    """The lines of a Lua file. Lua ends a line at a newline, a carriage return or a pair of them and nowhere else;
    str.splitlines() also breaks at a form feed or a Unicode line separator, and a comment that has one in it would
    leave the text after it as code."""
    lines = re.split(r"\r\n|\r|\n", text)
    if lines[-1] == "":
        lines.pop()
    return lines


def split(text: str, share: Path, stub: str, kept_as: str = BEFORE_SPLIT) -> tuple[str, int, int]:
    """The new hyprland.lua for an old one, which is kept as `kept_as`: (its text, statements carried over, lines of
    code left as comments)."""
    shipped = _shipped(share)
    if not any(name == "bombadil.lua" for name, _ in shipped):
        raise RuntimeError(f"Bombadil's part is not in {share / 'share' / 'hypr'}")
    mine_lines = _lines(text)
    if len(mine_lines) > MAX_LINES:
        raise ValueError(f"hyprland.lua has {len(mine_lines)} lines")
    user = [s.strip() for s in mine_lines]
    mine, removed = _persons_lines(_nearest(shipped, user), mine_lines)
    removed = removed and bool(text.strip())        # an empty file took nothing out: it was never filled in
    # What Bombadil shipped at any time is Bombadil's, wherever the file has it (a file made of two versions).
    ever = set()
    for _, lines in shipped:
        ever.update(_statement_texts(lines)[1])
    rows = scan(mine_lines)
    pieces: list[Piece] = []
    left: list[tuple[int, list[str]]] = []      # lines to write as comments, by where they were in the old file
    lost: list[str] = []                        # the code of the person's own statements that were left out
    for first, last, bad in statements(rows):
        if not any(mine[first:last]):
            continue
        code = " ".join(r.code for r in rows[first:last] if r.code)
        if all(mine[first:last]):
            if not any(user[first:last]) or "\n".join(user[first:last]) in ever:
                continue
            if _carryable(rows, first, last, bad):
                pieces.append(Piece(first, last, [mine_lines[k].rstrip() for k in range(first, last)],
                                    [user[k] for k in range(first, last) if user[k]], code))
                continue
            lost.append(code)
            covered: set[int] = set()
        else:
            built, covered = (([], set()) if bad else _config_pieces(mine_lines, rows, first, last, mine))
            pieces += built
        left.append((first, [user[k] for k in range(first, last) if mine[k] and user[k] and k not in covered]))
    # Never keep a use of something that was left out: the call would fail at load, and everything after it with it.
    # What a piece that goes was making goes too, and so on.
    by_name: dict[str, list[Piece]] = {}
    for piece in pieces:
        for name in piece.uses:
            by_name.setdefault(name, []).append(piece)
    queue = list(set().union(*(_defines(c) for c in lost))) if lost else []
    seen, gone = set(queue), set()
    while queue:
        for piece in by_name.get(queue.pop(), ()):
            if id(piece) not in gone:
                gone.add(id(piece))
                left.append((piece.first, piece.source))
                queue += [n for n in _defines(piece.code) if n not in seen]
                seen.update(_defines(piece.code))
    pieces = [piece for piece in pieces if id(piece) not in gone]
    text_out = _compose(stub, pieces, left, removed, kept_as)
    if pieces and _lua_parses(text_out) is False:
        # luac does not like what we are about to write, and a syntax error stops Bombadil's part too.
        left += [(p.first, p.source) for p in pieces]
        pieces = []
        text_out = _compose(stub, pieces, left, removed, kept_as)
    kept = sum(1 for p in pieces if p.code)
    # A comment the person changed inside one of Bombadil's statements is kept in the file, but is nothing to tell them.
    return text_out, kept, sum(1 for _, lines in left for line in lines if not line.startswith("--"))


def _compose(stub: str, pieces: list[Piece], left: list[tuple[int, list[str]]], removed: bool, kept_as: str) -> str:
    """The stub, then what is kept of the old file (with a note or two on it), then what could not be."""
    sections = [stub if stub.endswith("\n") else stub + "\n"]
    mine: list[str] = []
    if pieces:
        mine.append(f"-- Kept from your old file; all of it is in {kept_as}.")
    if removed:
        mine.append("-- A line you changed or took out in Bombadil's part is back: that part comes from the system")
        mine.append("-- as a whole. To change one, write your own line below.")
    prev_end = None
    block: list[str] = []
    for piece in pieces:
        if prev_end is not None and piece.first != prev_end:
            block.append("")
        block += piece.lines
        prev_end = piece.last
    while block and not block[0]:
        block.pop(0)
    while block and not block[-1]:
        block.pop()
    mine += block
    if mine:
        sections.append("\n".join(mine) + "\n")
    lines = [line for _, group in sorted(left, key=lambda item: item[0]) for line in group]
    if lines:
        sections.append(f"{LEFT_OVER} {kept_as}\n" + "".join(f"-- {line}\n" for line in lines))
    return "\n".join(sections)


def _keep_name(target: Path, raw: bytes) -> tuple[Path, bool]:
    """Where the old file is kept, and whether the copy is already there: hyprland.lua.before-split when that name
    is free or holds these very bytes, else .2, .3 and so on. A name that holds anything else is passed over, and so
    is a link, even a dangling one."""
    for n in range(1, 100):
        name = target.with_name(BEFORE_SPLIT if n == 1 else f"{BEFORE_SPLIT}.{n}")
        if not os.path.lexists(name):
            return name, False
        try:
            if not name.is_symlink() and name.is_file() and name.stat().st_size == len(raw) and name.read_bytes() == raw:
                return name, True
        except OSError:
            pass
    raise OSError(f"{BEFORE_SPLIT} and 98 more are taken: no name is free to keep the old file under")


def _keep_old(target: Path, raw: bytes, copy: Path) -> None:
    """The old file as it was, in a name nothing else has. Raises, so that nothing is replaced, if it cannot be."""
    if not _create_only(copy, raw, stat.S_IMODE(target.stat().st_mode)):
        raise FileExistsError(f"{copy.name} appeared meanwhile; hyprland.lua is not replaced")


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
    copy, have_copy = _keep_name(target, raw)
    if copy.name != BEFORE_SPLIT:
        # The stub's fallback runs the old file, so it has to name this one and not an unrelated one from before.
        stub = stub.replace(f'{BEFORE_SPLIT}"', f'{copy.name}"')
    new, kept, left = split(text, ctx.share, stub, copy.name)
    if not have_copy:
        _keep_old(target, raw, copy)
    link = os.readlink(target) if target.is_symlink() else None
    _write(target, new.encode("utf-8", "surrogateescape"), stat.S_IMODE(target.stat().st_mode))
    ctx.reload = True
    if left:
        lines = f"{left} line{'s' if left != 1 else ''}"
        ctx.notes.append(f"I could not carry over {lines} of your old Hyprland settings. "
                         f"They are commented out in hyprland.lua, and your old file is {copy.name}.")
    elif copy.name != BEFORE_SPLIT:
        ctx.notes.append(f"Your old Hyprland file is kept as {copy.name}, because {BEFORE_SPLIT} was already there.")
    if link is not None:
        ctx.notes.append(f"hyprland.lua was a link to {link}. It is a plain file now; what the link pointed at is as it was.")
    return Result("stub-only" if new == stub else "migrated", True, {"kept": kept, "left": left, "old_file": copy.name})


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
