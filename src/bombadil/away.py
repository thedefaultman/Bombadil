"""The Away widget's list: what happened while the person was gone, in the order they would want it.

The sources are the ones agentd already keeps: the rows of turns.jsonl (turns that ended, and the
background jobs that did), and the coding sessions agentd is told about. `items` is pure: it is handed
the rows, the sessions, the time the person left and the time now, and gives back what is worth a row.
`Away` holds the card between "the person came back" and "they have seen it", remembers when that last
was, and builds the one `away` message the shell draws. agentd owns the triggers: the person asking
for it ("show away"), and the moment they come back from a break.

Nothing here raises on a row it cannot make sense of, and nothing here reaches the model.
"""

import json
import os
import time
from pathlib import Path

from . import paths

LOOKBACK = 24 * 3600        # how far back the card looks when it was never seen
MAX_LOOKBACK = 7 * 24 * 3600
TAIL = 400_000              # bytes of turns.jsonl read from the end: a week of it, in a busy one
ROWS = 4                    # rows on the card; the rest are counted in one more row
TITLE = 44                  # characters of a row's title and of its second line
KINDS = ("failed", "waiting", "finished", "changed")     # the card's order, most urgent first
TONES = {"failed": "red", "waiting": "sessions", "finished": "ok", "changed": "machine"}


def _cut(text, n: int = TITLE) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _first_line(text) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _number(value, default=0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if n == n and abs(n) != float("inf") else default


def _clock(t: float, now: float) -> str:
    """"09:05", and the weekday before it when it was not today."""
    when, today = time.localtime(t), time.localtime(now)
    stamp = time.strftime("%H:%M", when)
    if (when.tm_year, when.tm_yday) == (today.tm_year, today.tm_yday):
        return stamp
    return time.strftime("%a ", when) + stamp


def read_tail(path: Path | None = None, limit: int = TAIL) -> list[dict]:
    """The rows of turns.jsonl from the last `limit` bytes, oldest first. A row cut by the read or by a
    crash is dropped."""
    path = path or paths.turns_log()
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return []
    rows = []
    for line in data.splitlines():      # a row the read began in the middle of is not JSON, so it is dropped here
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _turn_item(row: dict, t: float) -> dict | None:
    prompt = row.get("prompt")
    prompt = prompt.strip() if isinstance(prompt, str) else ""
    # Not what the person ran themselves ("!"), not what an app asked, not a turn they stopped or one
    # that is waiting for a limit to lift (it has not ended), and not one that was cut off.
    if (not prompt or prompt.startswith(("!", "[from app")) or row.get("stopped") or row.get("requeued")
            or row.get("ok") not in (True, False)):
        return None
    n = row.get("n")
    key = "turn:" + (str(n) if isinstance(n, int) and not isinstance(n, bool) else str(int(t)))
    if row["ok"] is False:
        return {"key": key, "kind": "failed", "title": _cut(prompt),
                "sub": _cut(_first_line(row.get("result"))) or "It did not finish.", "t": t}
    wrote = row["files"].get("wrote") if isinstance(row.get("files"), dict) else None
    summary = str(row.get("summary") or "").strip()
    if summary and (row.get("snapshot") or wrote):
        return {"key": key, "kind": "changed", "title": _cut(summary), "sub": "", "t": t}
    return {"key": key, "kind": "finished", "title": _cut(prompt), "sub": "", "t": t}


def _job_item(row: dict, t: float) -> dict | None:
    if row.get("action") != "job" or row.get("stopped"):
        return None     # the person stopped it themselves: that is not news
    failed = row.get("ok") is False
    job = row.get("job")
    item = {"key": "job:" + (str(job) if job else str(int(t))), "kind": "failed" if failed else "finished",
            "title": _cut(row.get("target") or "A job"), "sub": _cut(_first_line(row.get("result"))), "t": t}
    if failed and job:
        item["job"] = str(job)
    return item


def _session_title(s: dict) -> str:
    """"reviewer on Bombadil"; just the project for a session with no role (as the Needs you card says it)."""
    project = str(s.get("projectTitle") or s.get("project") or "")
    role = str(s.get("role") or "")
    return role + " on " + project if role and project else project or role or str(s.get("key"))


def _session_item(s: dict, now: float) -> dict | None:
    key = s.get("key")
    if key is None or key == "":
        return None
    state = s.get("state")
    if state == "asked":
        kind = "waiting"
    elif state in ("done", "failed") and s.get("unseen"):
        kind = "finished" if state == "done" else "failed"
    else:
        return None
    t = _number(s.get("since"), now)
    return {"key": "dev:" + str(key), "kind": kind, "title": _cut(_session_title(s)),
            "sub": _cut(_first_line(s.get("last"))), "t": t if 0 < t <= now else now, "session": str(key)}


def items(rows, sessions=(), *, since: float, now: float) -> list[dict]:
    """What is worth a row since `since`: failures first, then who is waiting, what finished, what
    changed; the newest first within each. A row is {key, kind, title, sub, t} and, where the card has a
    button for it, "job" or "session" (the id that button acts on)."""
    found: dict[str, dict] = {}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        t = _number(row.get("t"))
        if t <= since or t > now + 60:
            continue
        if row.get("kind") == "local":
            item = _job_item(row, t)
        elif paths.is_turn_row(row):
            item = _turn_item(row, t)
        else:
            item = None
        if item is not None:
            found[item["key"]] = item
    for s in sessions or ():
        item = _session_item(s, now) if isinstance(s, dict) else None
        if item is not None:
            found[item["key"]] = item
    return sorted(found.values(), key=lambda i: (KINDS.index(i["kind"]), -i["t"]))


def _row(item: dict, now: float) -> dict:
    sub = item["sub"] or ("at " + _clock(item["t"], now))
    row = {"key": item["key"], "kind": "dot", "title": item["title"], "sub": sub,
           "tone": TONES[item["kind"]], "button": ""}
    if "session" in item:
        row["button"] = "Open"
    elif "job" in item:
        row["button"] = "Why?"
    return row


class Away:
    """The card, between the person's coming back and their having seen it."""

    def __init__(self, path: Path | None = None, log: Path | None = None, clock=time.time):
        self._path = path
        self._log = log
        self._clock = clock
        self._msg = self._empty()
        self.seen_at = self._load()

    @property
    def path(self) -> Path:
        return self._path or paths.state_dir() / "away.json"

    @staticmethod
    def _empty() -> dict:
        return {"type": "away", "since": 0, "count": 0, "why": "", "rows": []}

    def _load(self) -> float | None:
        try:
            t = _number(json.loads(self.path.read_text()).get("seen_at"), 0.0)
        except (OSError, ValueError, AttributeError, RecursionError):
            return None
        return t if t > 0 else None

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"seen_at": self.seen_at}) + "\n")
            tmp.replace(self.path)
        except OSError:
            pass    # not remembered: the next look reaches back a day, which is all that is lost

    def message(self) -> dict:
        """What the shell is told: the card, or rows [] for none. A copy, so what is sent cannot change the card."""
        return dict(self._msg, rows=[dict(r) for r in self._msg["rows"]])

    @property
    def up(self) -> bool:
        return bool(self._msg["rows"])

    def _build(self, since: float, sessions, now: float, asked: bool) -> dict:
        since = max(since, now - MAX_LOOKBACK)
        found = items(read_tail(self._log), sessions, since=since, now=now)
        rows = [_row(i, now) for i in found[:ROWS]]
        if len(found) > ROWS:
            rows.append({"key": "more", "kind": "plain", "title": f"and {len(found) - ROWS} more", "sub": "",
                         "tone": "you", "button": ""})
        if not rows and asked:
            rows = [{"key": "none", "kind": "plain", "title": "Nothing happened", "sub": "", "tone": "you",
                     "button": ""}]
        if not rows:
            self._msg = self._empty()
        else:
            self._msg = {"type": "away", "since": since, "count": len(found),
                         "why": f"since {_clock(since, now)} · a click or your next ask puts this away",
                         "rows": rows}
        return self.message()

    def back(self, since: float, sessions=(), now: float | None = None) -> dict:
        """The person is back from a break that began at `since`: the card, when there is anything to say."""
        return self._build(_number(since), sessions, self._clock() if now is None else now, False)

    def ask(self, sessions=(), since: float | None = None, now: float | None = None) -> dict:
        """The person asked for it. `since` is the last time they were seen to be there (their ask before
        this one); with none, since the card was last seen, or a day when it never was. Always a card."""
        now = self._clock() if now is None else now
        if since is None:
            since = self.seen_at if self.seen_at else now - LOOKBACK
        return self._build(_number(since), sessions, now, True)

    def seen(self, now: float | None = None) -> bool:
        """The person has seen the card (a click, or their next ask). True when one was up."""
        was = self.up
        self._msg = self._empty()
        if was:
            self.seen_at = self._clock() if now is None else now
            self._save()
        return was
