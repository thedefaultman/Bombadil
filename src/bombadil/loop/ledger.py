"""Reading what the machine already wrote: turns.jsonl (old rows and ledger v2) and the per-turn logs.

Nothing here counts or judges; it only turns lines into rows and rows into `Request`s, and it
never raises on what it reads. A torn last line, a line that is not JSON, a row with keys
missing, a file that was rotated or shrank: each costs that line, never the reader. Old rows
(no `v`) are read with what they have: their `t` is when they asked, their id comes from the
per-turn log's name, their origin is typed.

A `Request` is one model turn. `t` is when they asked (the turn's start), `ended` when the row was
written. A stop or an undo is a later `kind: "local"` row; `resolve()` hands it to the turn it
acted on, so the turn knows `undone_at` and `stopped_at`.
"""

import json
import math
import os
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# A line longer than this is not a ledger row or an event a reader needs (a screenshot's bytes).
MAX_LINE = 2_000_000

# What a provider says when it wants them to sign in first. Such a turn never counts as an ask.
_SIGNIN_RE = re.compile(
    r"sign[ -]?in|signed out|log[ -]?in\b|logged in|/login|not authenticated|authentication|"
    r"invalid api key|unauthori[sz]ed|\b401\b|credentials", re.IGNORECASE)


@dataclass
class Request:
    id: str
    t: float                      # when they asked: the turn's start (old rows: the row's own t)
    day: str                      # the local date of t, YYYY-MM-DD
    text: str
    origin: str = "typed"
    ok: bool | None = None
    stopped: bool = False
    seconds: float = 0.0
    steps: int = 0
    tools: list[str] = field(default_factory=list)
    provider: str = ""
    session: str | None = None
    snapshot: int | None = None
    details: str = ""             # the per-turn log's path
    model: str | None = None
    cost: float | None = None
    ended: float = 0.0            # when the row was written: the turn's end
    changed: bool = False         # the turn changed something (its summary says what)
    signin: bool = False          # the provider asked them to sign in
    undone_at: float | None = None
    stopped_at: float | None = None


def day_of(t: float) -> str:
    """The local date of an epoch time, or "" for one that is no time at all."""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return ""


def _num(v, default: float | None = None) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return float(v)


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


# -- turns.jsonl --

def _upgrade(row) -> dict | None:
    """One line's object as a row a reader can use, or None when it is not one. Model rows (no
    `kind`) get the keys an old row lacks: `started`, `seconds`, `origin`, `id`."""
    if not isinstance(row, dict):
        return None
    t = _num(row.get("t"))
    if t is None or t <= 0:
        return None
    row = {**row, "t": t}
    kind = row.get("kind")
    if kind is not None:
        return row if isinstance(kind, str) else None
    started = _num(row.get("started"))
    row["started"] = started if started is not None and 0 < started <= t + 1 else t
    row["seconds"] = max(0.0, _num(row.get("seconds"), 0.0) or 0.0)
    details = row.get("details")
    details = details if isinstance(details, str) else ""
    row["details"] = details
    rid = row.get("id")
    if not isinstance(rid, str) or not rid:
        stem = PurePosixPath(details).stem if details else ""
        rid = stem or f"t{int(t * 1000)}"
    row["id"] = rid
    prompt = row.get("prompt")
    row["prompt"] = prompt if isinstance(prompt, str) else ""
    origin = row.get("origin")
    if not isinstance(origin, str) or not origin:
        # An old row has none; the apps' own prefix says where the text came from.
        origin = "app" if row["prompt"].startswith("[from app ") else "typed"
    row["origin"] = origin
    return row


def start_offset(path: Path, offset: int = 0, inode: int | None = None) -> int:
    """Where to read from: `offset`, unless the file is another one than the offset belongs to
    (its inode changed) or shrank below it (truncated, rotated): then all of it is new."""
    try:
        st = os.stat(path)
    except OSError:
        return 0
    if offset < 0 or offset > st.st_size or (inode is not None and inode != st.st_ino):
        return 0
    return offset


@dataclass
class Batch:
    rows: list[tuple[dict, int]]   # (row, the offset just after it)
    end: int                       # where the next read starts: after the last line used or skipped
    inode: int                     # the file's, to keep next to `end`
    reset: bool = False            # the file was replaced or shrank, so this read started at 0
    more: bool = False             # it stopped at `limit` rows: there may be more after `end`


def read_rows(path: Path, offset: int = 0, inode: int | None = None, limit: int | None = None) -> Batch:
    """Everything in turns.jsonl after `offset`, or the first `limit` rows of it (`end` is then just
    after the last of them, and `more` says to read again from there). A last line without its
    newline is left for next time unless it already is whole JSON (something wrote it by hand)."""
    path = Path(path)
    try:
        st = os.stat(path)
    except OSError:
        return Batch([], 0, inode or 0, reset=offset > 0)
    start = start_offset(path, offset, inode)
    rows: list[tuple[dict, int]] = []
    pos = start
    more = False
    try:
        with path.open("rb") as f:
            f.seek(start)
            for raw in f:
                if limit is not None and len(rows) >= limit:
                    more = True
                    break
                whole = raw.endswith(b"\n")
                try:
                    obj = json.loads(raw) if len(raw) <= MAX_LINE else None
                except ValueError:
                    obj = None
                    if not whole:
                        break   # torn: its writer is not done
                if not whole and not isinstance(obj, dict):
                    break
                pos += len(raw)
                row = _upgrade(obj)
                if row is not None:
                    rows.append((row, pos))
    except OSError:
        pass
    return Batch(rows, pos, st.st_ino, reset=start == 0 and offset > 0, more=more)


def iter_rows(path: Path, offset: int = 0, inode: int | None = None) -> Iterator[tuple[dict, int]]:
    """(row, next_offset) for each usable line after `offset`. Pass the inode you kept to notice
    a replaced file; with none, only a shrunk file starts again from 0."""
    yield from read_rows(path, offset, inode).rows


def request_from_row(row: dict) -> Request | None:
    """The request a model turn's row describes, or None for any other row (a launcher action,
    the loop's own trail) and for a turn with no words."""
    row = _upgrade(row)
    if row is None or row.get("kind") is not None:
        return None
    text = row["prompt"].strip()
    if not text:
        return None
    t = row["started"]
    tools = row.get("tools") if isinstance(row.get("tools"), dict) else {}
    listed = tools.get("names") if isinstance(tools.get("names"), list) else []
    names = [n for n in listed if isinstance(n, str)]
    steps = _int(tools.get("n"))
    ok = row.get("ok")
    ok = ok if isinstance(ok, bool) else None
    snapshot = _int(row.get("snapshot"))
    session = row.get("session")
    cost = _num(row.get("cost"))
    model = row.get("model")
    result = row.get("result") if isinstance(row.get("result"), str) else ""
    return Request(
        id=row["id"], t=t, day=day_of(t), text=text, origin=row["origin"], ok=ok,
        stopped=bool(row.get("stopped")), seconds=row["seconds"],
        steps=steps if steps is not None and steps >= 0 else len(names), tools=names,
        provider=str(row.get("provider") or ""), session=session if isinstance(session, str) else None,
        snapshot=snapshot, details=row["details"], model=model if isinstance(model, str) else None,
        cost=cost, ended=row["t"], changed=bool(row.get("summary")),
        signin=ok is not True and bool(_SIGNIN_RE.search(result[:400])),
    )


# -- what a later row says about an earlier turn --

def local_effect(row: dict) -> tuple[str, str | None, int | None, float] | None:
    """(`undo` or `stop`, the id of the turn it acted on, the restore point it went back to, when)
    for a launcher row that took a turn back or ended it; None for any other row."""
    if not isinstance(row, dict) or row.get("kind") != "local":
        return None
    action = row.get("action") or row.get("verb")
    t = _num(row.get("t"))
    if action not in ("undo", "stop") or t is None:
        return None
    of = row.get("of")
    return action, str(of) if of not in (None, "") else None, _int(row.get("of_snapshot")), t


def find_target(requests: Sequence[Request], effect: tuple[str, str | None, int | None, float]) -> Request | None:
    """The turn an undo or stop row acted on: by `of`, then `of_snapshot`, and for a row with neither
    (written before ledger v2) by time: a stop reaches the turn that was running, an undo the last
    turn begun before it."""
    action, of, snap, t = effect
    target = next((r for r in requests if of and r.id == of), None)
    if target is None and snap is not None:
        target = next((r for r in requests if r.snapshot == snap), None)
    if target is None and of is None and snap is None:
        ordered = sorted(requests, key=lambda r: r.t)
        if action == "stop":
            target = next((r for r in ordered if r.t <= t <= max(r.ended, r.t) + 1), None)
        else:
            target = next((r for r in reversed(ordered) if r.t <= t), None)
    return target


def resolve(requests: Sequence[Request], rows: Iterable[dict]) -> int:
    """Hand each undo and stop row to the turn it acted on (see `find_target`), so the turn knows
    `undone_at` and `stopped_at`. Returns how many turns changed."""
    changed: set[str] = set()
    for row in rows:
        eff = local_effect(row)
        target = find_target(requests, eff) if eff else None
        if target is None:
            continue
        if eff[0] == "undo" and target.undone_at is None:
            target.undone_at = eff[3]
            changed.add(target.id)
        elif eff[0] == "stop" and target.stopped_at is None:
            target.stopped_at = eff[3]
            changed.add(target.id)
    return len(changed)


# -- a turn's own log --

def read_turn_log(path: str | Path, kinds: Iterable[str] | None = None) -> list[dict]:
    """The events of one turn's log (`{"t", "type": "event", "kind": "tool", "name", "input", ...}`),
    skipping lines that are not JSON objects. With `kinds`, only events of those kinds are parsed.
    A missing or unreadable log is an empty list."""
    want = set(kinds) if kinds is not None else None
    marks = tuple(f'"kind": "{k}"' for k in want) + tuple(f'"kind":"{k}"' for k in want) if want else ()
    out: list[dict] = []
    if not path:
        return out
    try:
        with open(path, "rb") as f:
            for raw in f:
                if len(raw) > MAX_LINE:
                    continue
                if marks and not any(m.encode() in raw for m in marks):
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(ev, dict) and isinstance(ev.get("kind"), str) and (want is None or ev["kind"] in want):
                    out.append(ev)
    except OSError:
        pass
    return out
