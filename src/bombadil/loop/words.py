"""words.toml: the few words Bombadil makes for you from what you keep asking.

A word is one of your own phrases ("my passwords") that opens an app or shows a panel, and that
is all it can be: never a shell command, sudo or the model, so the worst a bad row can do is open
the wrong window. launcher.match reads them last, after every name the machine already knows, so
a word never takes anything over.

The file is yours too: edit it by hand and it is read again when it changes. A file that does not
parse gives no words and one line on stderr; a wrong row is skipped, never the file. Reads are
cached by the file's mtime, because launcher.match runs on every Enter. Writes are all or
nothing (a temp file, then os.replace), and a file that had problems is kept as words.toml.bad
before Bombadil rewrites it, so a typo of yours is never lost to the next word it makes.

Nothing here runs on a turn's path except `lookup`, which costs one stat.
"""

import os
import shutil
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .. import apps, paths

MAX_WORDS = 30        # active words; one put away does not count
MAX_PHRASE = 80       # a word is a short phrase, not a pasted sentence
UNUSED_DAYS = 28      # unused this long, a word is put away
KINDS = ("app", "panel")
HEADER = (
    "# Words Bombadil made from what you keep asking. Each one only opens an app or shows a panel.\n"
    "# You can edit or delete rows here; \"noticed\" lists them with Undo.\n"
)


class WordError(ValueError):
    """Base of what add() and bring_back() refuse with. str() is one plain sentence for the line
    above the pill; `why` says which rule it was."""

    why = ""

    def __init__(self, why: str, text: str):
        super().__init__(text)
        self.why = why


class WordRefused(WordError):
    """The phrase or what it opens is not one a word may have."""


class WordsFull(WordError):
    """There are already MAX_WORDS active words."""

    def __init__(self, text: str = ""):
        super().__init__("full", text or f"There are already {MAX_WORDS} words. Put one away first.")


@dataclass(frozen=True)
class Word:
    phrase: str            # normalised as the launcher normalises: lowercase, single spaces, no edge signs
    kind: str              # "app" or "panel"
    name: str              # the app's name (its folder, "passwords") or the panel's ("browser")
    made: int = 0          # epoch seconds; bring_back starts the unused clock again by setting it
    from_group: str = ""   # the loop's group it came from, "" for one written by hand
    away: bool = False     # put away: kept, matches nothing until brought back

    @property
    def opens(self) -> dict:
        return {"kind": self.kind, "name": self.name}


@dataclass(frozen=True)
class _Snapshot:
    key: tuple
    words: tuple[Word, ...]
    by_phrase: dict
    problems: int          # rows or a whole file that could not be used: kept aside before a rewrite

    @property
    def active(self) -> int:
        return sum(1 for w in self.words if not w.away)


_lock = threading.Lock()     # one writer at a time in this process; os.replace covers the rest
_cache: _Snapshot | None = None


def _say(text: str) -> None:
    print(f"words: {text}", file=sys.stderr)


def _norm(text) -> str:
    from .. import launcher  # imported late: launcher imports this module
    return launcher.normalize(text)


def _clean_phrase(raw) -> str:
    """The phrase as stored, or WordRefused. "!" at the front is a shell command for the pill, and
    normalising would strip it, so it is checked on what was typed."""
    text = str(raw).strip()
    if text.startswith("!"):
        raise WordRefused("shell", "A word cannot start with “!”: that is a shell command.")
    phrase = _norm(text)
    if not phrase:
        raise WordRefused("empty", "A word needs some words in it.")
    if len(phrase) > MAX_PHRASE:
        raise WordRefused("long", f"A word is a short phrase, at most {MAX_PHRASE} letters.")
    try:
        phrase.encode("utf-8")   # a lone surrogate cannot be written to the file
    except UnicodeEncodeError:
        raise WordRefused("empty", "A word cannot hold that character.") from None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in phrase):
        raise WordRefused("empty", "A word cannot hold control characters.")
    return phrase


def _clean_target(opens) -> tuple[str, str]:
    """(kind, name) of what a word may open, or WordRefused. Only what is named is kept: a row
    that says more ("run", "cmd") has the rest dropped, not stored."""
    if not isinstance(opens, Mapping):
        raise WordRefused("kind", "A word can only open an app or show a panel.")
    kind, name = opens.get("kind"), opens.get("name")
    if kind not in KINDS:
        raise WordRefused("kind", "A word can only open an app or show a panel, never run anything.")
    if not isinstance(name, str):
        raise WordRefused("target", f"A word needs the name of the {kind} it opens.")
    if kind == "app" and not apps.NAME_RE.match(name):
        raise WordRefused("target", f"“{name}” is not an app name.")
    if kind == "panel":
        from .. import launcher
        if name not in launcher.PANEL_TITLES:
            raise WordRefused("target", f"“{name}” is not a panel.")
    return kind, name


# -- reading --

def _stat_key(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None)
    # The inode too: os.replace gives a new one, so a rewrite that keeps size and mtime still shows.
    return (str(path), st.st_mtime_ns, st.st_size, st.st_ino)


def _row(row) -> Word | None:
    if not isinstance(row, dict):
        return None
    opens, made = row.get("opens"), row.get("made", 0)
    group, away = row.get("from_group", ""), row.get("away", False)
    if not isinstance(row.get("phrase"), str) or not isinstance(opens, dict):
        return None
    kind, name = opens.get("kind"), opens.get("name")
    if kind not in KINDS or not isinstance(name, str) or not name.strip():
        return None
    if isinstance(made, bool) or not isinstance(made, (int, float)):
        return None
    if not isinstance(group, str) or not isinstance(away, bool):
        return None
    try:
        phrase = _clean_phrase(row["phrase"])
    except WordRefused:
        return None
    return Word(phrase, kind, name.strip(), int(made), group, away)


def _read(path: Path) -> tuple[list[Word], int]:
    """(words, problems). Never raises."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], 0
    except (OSError, UnicodeDecodeError) as e:
        _say(f"cannot read {path.name} ({type(e).__name__}); no words until it can be read")
        return [], 1
    try:
        data = tomllib.loads(text)
    except (ValueError, RecursionError) as e:   # TOMLDecodeError is a ValueError
        _say(f"{path.name} does not parse ({e}); no words until it does")
        return [], 1
    rows = data.get("word", [])
    if not isinstance(rows, list):
        _say(f"{path.name} has no [[word]] tables; no words")
        return [], 1
    out: list[Word] = []
    seen: set[str] = set()
    problems = 0
    for n, row in enumerate(rows, start=1):
        w = _row(row)
        if w is None:
            _say(f"skipped row {n} of {path.name}: it needs a phrase and opens = {{ kind, name }}")
        elif w.phrase in seen:
            _say(f"skipped row {n} of {path.name}: “{w.phrase}” is there twice")
            w = None
        if w is None:
            problems += 1
            continue
        seen.add(w.phrase)
        out.append(w)
    return out, problems


def _snapshot() -> _Snapshot:
    global _cache
    try:
        path = paths.words_file()
    except (RuntimeError, OSError):   # no home directory to look in: no words, and nothing to keep
        return _Snapshot(("",), (), {}, 0)
    key = _stat_key(path)
    snap = _cache
    if snap is not None and snap.key == key:
        return snap
    words, problems = _read(path)
    snap = _Snapshot(key, tuple(words), {w.phrase: w for w in words}, problems)
    _cache = snap
    return snap


def load() -> list[Word]:
    """Every word, put away ones too, in file order. Never raises: a file that cannot be used
    gives []. Cheap to call often (one stat while the file is unchanged)."""
    return list(_snapshot().words)


def active() -> list[Word]:
    return [w for w in _snapshot().words if not w.away]


def get(phrase: str) -> Word | None:
    """The word for this phrase, put away or not."""
    return _snapshot().by_phrase.get(_norm(phrase))


def lookup(phrase: str) -> Word | None:
    """The active word for this phrase: what launcher.match asks on every Enter."""
    w = _snapshot().by_phrase.get(_norm(phrase))
    return None if w is None or w.away else w


# -- writing --

def _quote(s: str) -> str:
    """A TOML basic string. json.dumps is not safe here: it writes characters outside the BMP as
    surrogate pairs, which TOML rejects, and leaves DEL raw."""
    out = ['"']
    for ch in s.encode("utf-8", "replace").decode("utf-8"):   # a lone surrogate becomes "?"
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\u{o:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _dump(words) -> str:
    lines = [HEADER]
    for w in words:
        lines += [
            "[[word]]",
            f"phrase = {_quote(w.phrase)}",
            f"opens = {{ kind = {_quote(w.kind)}, name = {_quote(w.name)} }}",
            f"made = {int(w.made)}",
            f"from_group = {_quote(w.from_group)}",
            f"away = {'true' if w.away else 'false'}",
            "",
        ]
    return "\n".join(lines)


def _save(words: list[Word], before: _Snapshot) -> None:
    """All or nothing. The text is read back before it replaces anything, so a bug here costs an
    exception and leaves the old file as it was."""
    global _cache
    path = paths.words_file()
    text = _dump(words)
    tomllib.loads(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    if before.problems:
        try:
            shutil.copy2(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass   # nothing to keep: the file was not there
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _cache = None


def add(phrase: str, opens: Mapping, group: str | None = None, now: float | None = None,
        known: Callable[[str], str] | None = None) -> Word:
    """Make a word. `opens` is {"kind": "app"|"panel", "name": ...}. Raises WordRefused (a phrase
    that already means something, anything but an app or a panel) or WordsFull; an OSError when
    the file cannot be written. `known(phrase)` says what the normalised phrase already does
    ("an app", "a command"... or "" when it is free); the default is launcher.means, and tests
    pass their own so they need no machine. It does not check that the target exists: the caller
    picks it from something it just saw, and match() ignores a word whose app is gone."""
    p = _clean_phrase(phrase)
    kind, name = _clean_target(opens)
    if known is None:
        from .. import launcher
        known = launcher.means
    what = known(p)
    if what:
        what = what if isinstance(what, str) else "something"
        raise WordRefused("means", f"“{p}” already means something else: it is {what}.")
    with _lock:
        snap = _snapshot()
        old = snap.by_phrase.get(p)
        if old is not None:
            raise WordRefused("word", f"“{p}” is already a word" + (", put away." if old.away else "."))
        if snap.active >= MAX_WORDS:
            raise WordsFull()
        word = Word(p, kind, name, int(time.time() if now is None else now), str(group or ""), False)
        _save([*snap.words, word], snap)
        return word


def remove(phrase: str) -> Word | None:
    """Take a word out, put away or not. The word that went, or None when there was none."""
    p = _norm(phrase)
    with _lock:
        snap = _snapshot()
        old = snap.by_phrase.get(p)
        if old is None:
            return None
        _save([w for w in snap.words if w is not old], snap)
        return old


def _set_away(phrase: str, away: bool, now: float | None = None) -> Word | None:
    p = _norm(phrase)
    with _lock:
        snap = _snapshot()
        old = snap.by_phrase.get(p)
        if old is None or old.away == away:
            return None
        if not away and snap.active >= MAX_WORDS:
            raise WordsFull()
        new = replace(old, away=away)
        if not away:
            new = replace(new, made=int(time.time() if now is None else now))
        _save([new if w is old else w for w in snap.words], snap)
        return new


def put_away(phrase: str) -> Word | None:
    """Keep the word but match nothing. The changed word, or None when there was nothing to do."""
    return _set_away(phrase, True)


def bring_back(phrase: str, now: float | None = None) -> Word | None:
    """Match again. The 28 days start over (`made` becomes now), or the next sweep would put it
    straight back. WordsFull when there are already MAX_WORDS active."""
    return _set_away(phrase, False, now)


def words_unused(last_used: Mapping, days: int = UNUSED_DAYS, now: float | None = None) -> list[str]:
    """The phrases of active words to put away: not used for `days`. `last_used` maps a phrase to
    the epoch it last opened something (the loop keeps it from the ledger); a word never used
    counts from when it was made."""
    now = time.time() if now is None else now
    used: dict[str, float] = {}
    for k, v in (last_used or {}).items():
        try:
            p = _norm(k)
            used[p] = max(used.get(p, 0.0), float(v))
        except (TypeError, ValueError):
            continue
    cutoff = now - days * 86400
    return [w.phrase for w in _snapshot().words if not w.away and max(w.made, used.get(w.phrase, 0.0)) <= cutoff]
