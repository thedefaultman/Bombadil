"""Findings: what the probes find, kept once per problem, counted only when it counts, and written
down with evidence that holds nothing of his.

A finding is one problem as it always reads (its fingerprint), however often it is seen. The store
(`FindingsStore`, tables in loop.db) decides WHEN A SIGHTING COUNTS:

  invariant  a state of the machine: counts when one retry 500 ms later is red too (a probe asks for
             it with Result.retry_after; `record_retry` is the whole protocol; `record` refuses a
             first look). A probe that turns green on retry three times in a week is quarantined and
             becomes a finding about itself.
  event, crash, drift
             a fact that already happened: counts on its first sighting.
  friction   what he had to work around: counts at 3 sightings on 2 days, or at one sighting with a
             probe firing within 5 minutes.

The same state seen on every run for an hour is one sighting (runs less than half an hour apart are
the same episode); a past fact carries its own time (Result.at) and is one sighting however often it
is read. A finding he dismissed, or said never to, is not raised again. Nothing here raises: a full
disk or a locked database costs one line on stderr and the call returns as if nothing had been seen.

Findings about the loop's own checks (component "loop") are never fixable: the loop cannot edit its
judges, so they are always reports. `fixable` is False for everything today; the mender will set it.

Evidence is built from typed fields, not scrubbed after the fact: windows without titles, monitors,
layers, the bar's rectangles, at most 40 log lines, the turn row without its words and its tool
events, versions, and the probe id (`bombadil probe <id>` runs it again). No screenshot, no prompt,
no path under home, no title.
"""

import functools
import hashlib
import math
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .. import paths
from . import db
from .probes import Observation, Result, attach_words, scrub_text, strip_line, window_kind, write_json_atomic

STATES = ("open", "reported", "sent", "dismissed", "never")
SHOWING = ("open", "reported")   # what still waits for him; `sent` is done, the others are his no
SAME_EPISODE = 1800.0    # seconds: a state seen again within this of the last time is the same sighting
FRICTION_SIGHTINGS = 3
FRICTION_DAYS = 2
PROBE_WITHIN = 300.0     # a friction sighting with a probe this close counts at once
FLIPS = 3                # red then green on retry this many times ...
FLIP_WINDOW = 7 * 86400.0    # ... in this long is a flaky probe
KEEP_PENDING = 30 * 86400.0  # a friction that never counted is forgotten after this
LOG_LINES = 40

SCHEMA = ["""
CREATE TABLE findings(
    fp TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    rule TEXT NOT NULL,
    probe TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    expected TEXT NOT NULL,
    observed TEXT NOT NULL,
    first_t REAL NOT NULL,
    last_t REAL NOT NULL,
    n INTEGER NOT NULL,
    days INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    counted INTEGER NOT NULL DEFAULT 0,
    fixable INTEGER NOT NULL DEFAULT 0,
    evidence TEXT NOT NULL DEFAULT ''
);
CREATE TABLE sightings(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fp TEXT NOT NULL,
    t REAL NOT NULL,
    day TEXT NOT NULL,
    key TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    cleared INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX sightings_fp ON sightings(fp, t);
CREATE TABLE probe_flips(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    probe TEXT NOT NULL,
    t REAL NOT NULL
);
CREATE INDEX probe_flips_probe ON probe_flips(probe, t)
"""]


@dataclass
class Finding:
    """One problem. `first` and `last` are epoch seconds of the first and latest sighting, `n` how many
    times it was seen (an episode or a past fact is one) and `days` on how many different days.
    `state` is what he did with it. `evidence` is the folder with evidence.json, `probe` the check
    that found it (`bombadil probe <probe>` runs it again)."""

    fp: str
    component: str
    rule: str
    title: str
    expected: str
    observed: str
    first: float
    last: float
    n: int
    days: int
    state: str = "open"
    fixable: bool = False
    evidence: str = ""
    probe: str = ""
    kind: str = ""

    @property
    def report_only(self) -> bool:
        """Nothing can mend it: the loop's own checks, and everything until the mender exists."""
        return self.component == "loop" or not self.fixable

    def to_dict(self) -> dict:
        return {"fp": self.fp, "component": self.component, "rule": self.rule, "title": self.title,
                "expected": self.expected, "observed": self.observed, "first": self.first, "last": self.last,
                "n": self.n, "days": self.days, "state": self.state, "fixable": self.fixable,
                "evidence": self.evidence, "probe": self.probe, "kind": self.kind}


# -- fingerprints --

def _first_line(text) -> str:
    for line in str(text).splitlines():
        if line.strip():
            return line.strip()
    return ""


def fingerprint(component: str, rule: str, line: str) -> str:
    """component:rule:hash, e.g. hypr:apps-stacked:3c91a0. The hash is of the first line of what was
    observed with paths, pids, numbers and hex ids taken out, so the same problem is the same finding
    on every machine and every day."""
    digest = hashlib.sha1(strip_line(_first_line(line)).encode()).hexdigest()[:6]
    return f"{component}:{rule}:{digest}"


def fingerprint_of(result: Result) -> str:
    return fingerprint(result.component, result.rule, result.observed)


def _slug(fp: str) -> str:
    return re.sub(r"[^\w:.-]", "_", fp)


def evidence_dir(fp: str) -> Path:
    return paths.loop_dir() / "findings" / _slug(fp)


# -- the evidence bundle --

_BANNED = frozenset({"title", "initialTitle", "prompt", "result", "summary", "details", "word", "target",
                     "text", "session", "cmdline", "address", "pid"})
_TURN_KEYS = ("id", "t", "started", "seconds", "origin", "ok", "stopped", "provider", "model", "cost", "v",
              "kind", "verb", "via", "action", "of", "snapshot")


def _num(v):
    return v if not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) else None


def _pair(v) -> list | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(_num(x) is not None for x in v):
        return list(v)
    return None


def _secrets(obs: Observation | None, turn) -> list[str]:
    """Everything of his that must not reach a bundle: window titles, what he typed, what was answered."""
    found: set[str] = set()
    for c in [*(obs.clients if obs and isinstance(obs.clients, list) else []),
              obs.activewindow if obs else None]:
        if isinstance(c, dict):
            found |= {c.get(k) for k in ("title", "initialTitle")}
    rows = [*(obs.ledger if obs and isinstance(obs.ledger, list) else []), turn]
    for r in rows:
        if isinstance(r, dict):
            found |= {r.get(k) for k in ("prompt", "result", "summary")}
    # A title that is a class name, or too short to be his, would only eat the bundle's own words.
    return sorted((s for s in found if isinstance(s, str) and len(s) >= 4 and not s.startswith("bombadil")),
                  key=len, reverse=True)


def _homes() -> list[str]:
    homes = {str(Path.home()), os.environ.get("HOME", "")}
    return sorted((h for h in homes if len(h) > 1 and h != "/"), key=len, reverse=True)


def _redact(text, secrets: list[str], limit: int = 300) -> str:
    s = str(text)
    for secret in secrets:
        s = s.replace(secret, "…")
    s = scrub_text(s)
    for home in _homes():    # what the path pattern cannot see: a bare home folder
        s = s.replace(home, "~")
    return s[:limit]


def _clean(v, secrets: list[str], depth: int = 0):
    """A value that is safe to write down: no banned key, no secret, no path, nothing that is not plain."""
    if isinstance(v, dict) and depth < 6:
        return {str(k)[:60]: _clean(x, secrets, depth + 1)
                for k, x in list(v.items())[:60] if k not in _BANNED}
    if isinstance(v, (list, tuple)) and depth < 6:
        return [_clean(x, secrets, depth + 1) for x in list(v)[:60]]
    if isinstance(v, str):
        return _redact(v, secrets)
    if isinstance(v, bool) or v is None or _num(v) is not None:
        return v
    return None


def _window(c: dict) -> dict:
    ws = c["workspace"] if isinstance(c.get("workspace"), dict) else {}
    special = isinstance(ws.get("name"), str) and ws["name"].startswith("special:")
    return {"class": window_kind(c), "at": _pair(c.get("at")), "size": _pair(c.get("size")),
            "floating": c.get("floating") is True, "mapped": c.get("mapped") is not False,
            "hidden": c.get("hidden") is True, "fullscreen": _num(c.get("fullscreen")),
            "workspace": ws["name"] if special else _num(ws.get("id")), "monitor": _num(c.get("monitor"))}


def _monitor(m: dict) -> dict:
    active = m["activeWorkspace"] if isinstance(m.get("activeWorkspace"), dict) else {}
    special = m["specialWorkspace"] if isinstance(m.get("specialWorkspace"), dict) else {}
    return {"name": m.get("name"), "width": _num(m.get("width")), "height": _num(m.get("height")),
            "x": _num(m.get("x")), "y": _num(m.get("y")), "scale": _num(m.get("scale")),
            "transform": _num(m.get("transform")), "disabled": m.get("disabled") is True,
            "workspace": _num(active.get("id")), "special": special.get("name")}


def _layers(layers) -> list[dict]:
    out = []
    for monitor, mon in (layers.items() if isinstance(layers, dict) else []):
        levels = mon.get("levels") if isinstance(mon, dict) else None
        for level, entries in (levels.items() if isinstance(levels, dict) else []):
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict):
                    out.append({"monitor": monitor, "level": level, "namespace": e.get("namespace"),
                                "x": _num(e.get("x")), "y": _num(e.get("y")), "w": _num(e.get("w")),
                                "h": _num(e.get("h"))})
    return out[:60]


def _bar(bar: dict) -> dict:
    screens = bar.get("screens") if isinstance(bar.get("screens"), dict) else {}
    out = {}
    for name, screen in screens.items():
        if not isinstance(screen, dict):
            continue
        rects = screen["rects"] if isinstance(screen.get("rects"), list) else []
        out[name] = {"w": _num(screen.get("w")), "h": _num(screen.get("h")),
                     "rects": [{k: r.get(k) for k in ("name", "x", "y", "w", "h")}
                               for r in rects[:60] if isinstance(r, dict)]}
    return {"alive_at": _num(bar.get("alive_at")), "connected_at": _num(bar.get("connected_at")),
            "build": bar.get("build"), "screens": out}


def build_bundle(fp: str, result: Result, now: float, obs: Observation | None = None, log=(), turn=None,
                 tools=(), versions=None) -> dict:
    """What evidence.json holds for one finding. Every string and key passes `_clean` last, so a
    field added here by mistake is still scrubbed."""
    secrets = _secrets(obs, turn)
    bundle: dict = {"fp": fp, "probe": result.id, "component": result.component, "rule": result.rule,
                    "kind": result.kind, "at": now, "expected": result.expected, "observed": result.observed,
                    "evidence": result.evidence, "command": f"bombadil probe {result.id}"}
    if obs is not None:
        if isinstance(obs.clients, list):
            bundle["windows"] = [_window(c) for c in obs.clients if isinstance(c, dict)][:80]
        if isinstance(obs.activewindow, dict) and obs.activewindow:
            bundle["activewindow"] = _window(obs.activewindow)
        if isinstance(obs.monitors, list):
            bundle["monitors"] = [_monitor(m) for m in obs.monitors if isinstance(m, dict)][:16]
        if isinstance(obs.layers, dict):
            bundle["layers"] = _layers(obs.layers)
        if isinstance(obs.bar, dict):
            bundle["bar"] = _bar(obs.bar)
    if log:
        bundle["log"] = [str(line) for line in list(log)[-LOG_LINES:]]
    if isinstance(turn, dict):
        row = {k: turn[k] for k in _TURN_KEYS if k in turn}
        names = turn["tools"].get("names") if isinstance(turn.get("tools"), dict) else None
        row["tools"] = {"n": _num(turn["tools"].get("n")) if isinstance(turn.get("tools"), dict) else None,
                        "names": names if isinstance(names, list) else []}
        bundle["turn"] = row
    if tools:
        bundle["tool_events"] = [{"name": t.get("name") or t.get("tool"), "ok": t.get("ok"),
                                  "error": _first_line(t.get("error") or "")[:160]}
                                 for t in tools if isinstance(t, dict)][:60]
    if isinstance(versions, dict):
        bundle["versions"] = {str(k)[:40]: str(v)[:80] for k, v in list(versions.items())[:30]}
    return _clean(bundle, secrets)


# -- the store --

def _guard(default=None):
    """Nothing in the store raises: one line on stderr, once per distinct trouble, and `default`."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception as e:  # noqa: BLE001 - a finding not kept costs a line, never the runner
                self._complain(f"{fn.__name__}: {type(e).__name__}: {e}")
                return default() if callable(default) else default
        return inner
    return wrap


def _day(t: float) -> str:
    """The local day a sighting was on: "3 times on 2 days" is his days, not UTC's."""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(t))
    except (OverflowError, OSError, ValueError):
        return "unknown"


class FindingsStore:
    """Findings, sightings and probe flips in loop.db. Use it from one thread at a time, like the
    connection it wraps (`db.connect()` when none is given)."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self.conn = conn if conn is not None else db.connect()
        db.schema(self.conn, "findings", SCHEMA)
        self._complained: set[str] = set()

    def close(self) -> None:
        self.conn.close()

    # -- recording --

    @_guard()
    def record(self, result: Result, now: float | None = None, *, obs: Observation | None = None, log=(),
               turn: dict | None = None, tools=(), versions: dict | None = None) -> Finding | None:
        """Write down one probe's Result when it counts. Returns the finding when this sighting was
        new (a new finding, or one more time for a known one), else None: green, not checked, a first
        look at an invariant that still waits for its retry, a probe that is quarantined, a finding
        he dismissed, the same event again, or a friction that has not happened often enough yet.

        `obs`, `log` (the last lines of the relevant log), `turn` (the ledger row), `tools` (that turn's
        tool events) and `versions` only fill the evidence bundle."""
        now = _num(now) if _num(now) is not None else time.time()
        if not isinstance(result, Result) or result.ok is not False:
            return None
        if result.retry_after is not None:
            return None     # an invariant seen once: the runner looks again (record_retry) before it counts
        if result.component != "loop" and result.id in self.quarantined(now):
            return None
        # What is passed in only fills the evidence: a wrong kind of thing is left out, not an error.
        context = {"obs": obs if isinstance(obs, Observation) else None,
                   "log": list(log) if isinstance(log, (list, tuple)) else [],
                   "turn": turn if isinstance(turn, dict) else None,
                   "tools": list(tools) if isinstance(tools, (list, tuple)) else [],
                   "versions": versions if isinstance(versions, dict) else None}
        return self._count(result, now, context)

    @_guard()
    def record_retry(self, first: Result, again, now: float | None = None, **context) -> Finding | None:
        """The retry protocol in one call: `first` was red and asked for a look in retry_after seconds;
        `again` is what the probe said then (a Result, or the list run_probe gives, taken with
        retried=True). Red again: it counts. Green: the probe flipped, and a probe that does it three
        times in a week is quarantined and written up as a finding about itself (returned).
        A retry that could not be checked counts for nothing and is not a flip."""
        now = _num(now) if _num(now) is not None else time.time()
        results = [again] if isinstance(again, Result) else [r for r in again or [] if isinstance(r, Result)]
        if not isinstance(first, Result) or first.ok is not False:
            return None
        reds = [r for r in results if r.ok is False]
        if reds:
            found = None
            for r in reds:
                found = self.record(replace(r, retry_after=None), now, **context) or found
            return found
        if any(r.ok is True for r in results):
            return self._flip(first, now)
        return None

    def _count(self, r: Result, now: float, context: dict) -> Finding | None:
        fp = fingerprint_of(r)
        t = _num(r.at) if _num(r.at) is not None else now
        key = f"{t:.3f}" if _num(r.at) is not None else ""
        counted = False
        secrets = _secrets(context.get("obs"), context.get("turn"))
        with db.transaction(self.conn):
            row = self.conn.execute("SELECT * FROM findings WHERE fp=?", (fp,)).fetchone()
            if row is not None and row["state"] in ("dismissed", "never"):
                return None
            if key and self.conn.execute("SELECT 1 FROM sightings WHERE fp=? AND key=?",
                                         (fp, key)).fetchone():
                return None     # this very event is already written down
            if not key and row is not None and t - row["last_t"] < SAME_EPISODE:
                self.conn.execute("UPDATE findings SET last_t=MAX(last_t, ?) WHERE fp=?", (t, fp))
                return None     # still the same episode of the same state
            self.conn.execute("INSERT INTO sightings(fp, t, day, key, kind) VALUES(?, ?, ?, ?, ?)",
                              (fp, t, _day(t), key, r.kind))
            n, days, first, last = self.conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT day), MIN(t), MAX(t) FROM sightings "
                "WHERE fp=? AND cleared=0", (fp,)).fetchone()
            counted = bool(row and row["counted"]) or self._counts(r.kind, fp, n, days, t)
            self.conn.execute(
                "INSERT INTO findings(fp, component, rule, probe, kind, title, expected, observed, first_t, "
                "last_t, n, days, counted, evidence) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(fp) DO UPDATE SET title=excluded.title, expected=excluded.expected, "
                "observed=excluded.observed, first_t=excluded.first_t, "
                "last_t=MAX(findings.last_t, excluded.last_t), n=excluded.n, days=excluded.days, "
                "counted=MAX(findings.counted, excluded.counted)",
                (fp, r.component, r.rule, r.id, r.kind, _redact(r.title or r.id, secrets),
                 _redact(r.expected, secrets), _redact(r.observed, secrets), first, last, n, days,
                 int(counted), str(evidence_dir(fp))))
            if r.kind != "friction" and r.component != "loop":
                # A probe firing is what makes a friction seen once count: look back as well as forward.
                self.conn.execute(
                    "UPDATE findings SET counted=1 WHERE counted=0 AND fp IN (SELECT fp FROM sightings "
                    "WHERE kind='friction' AND cleared=0 AND ABS(t - ?) <= ?)", (t, PROBE_WITHIN))
            self._prune(now)
        self._write_bundle(fp, r, now, context)
        return self.get(fp) if counted else None

    def _counts(self, kind: str, fp: str, n: int, days: int, t: float) -> bool:
        if kind != "friction":
            return True
        if n >= FRICTION_SIGHTINGS and days >= FRICTION_DAYS:
            return True
        near = self.conn.execute(
            "SELECT 1 FROM sightings WHERE kind != 'friction' AND cleared=0 AND fp != ? "
            "AND fp NOT LIKE 'loop:%' AND ABS(t - ?) <= ? LIMIT 1", (fp, t, PROBE_WITHIN)).fetchone()
        return near is not None

    def _write_bundle(self, fp: str, r: Result, now: float, context: dict) -> None:
        try:
            write_json_atomic(evidence_dir(fp) / "evidence.json", build_bundle(fp, r, now, **context))
        except (OSError, TypeError, ValueError) as e:
            self._complain(f"evidence for {fp}: {type(e).__name__}: {e}")

    def _prune(self, now: float) -> None:
        """Forget what can no longer matter: a friction that never counted, old flips, and the
        sightings of findings that were cleared (kept until then so a clear does not bring back a
        crash that is still in coredumpctl)."""
        old = now - KEEP_PENDING
        stale = self.conn.execute("SELECT fp FROM findings WHERE counted=0 AND last_t < ?", (old,)).fetchall()
        for row in stale:
            shutil.rmtree(evidence_dir(row["fp"]), ignore_errors=True)
        self.conn.execute("DELETE FROM findings WHERE counted=0 AND last_t < ?", (old,))
        self.conn.execute("DELETE FROM sightings WHERE cleared=1 AND t < ?", (old,))
        self.conn.execute("DELETE FROM sightings WHERE t < ? AND fp NOT IN (SELECT fp FROM findings)", (old,))
        self.conn.execute("DELETE FROM probe_flips WHERE t < ?", (now - FLIP_WINDOW * 4,))

    # -- flaky probes --

    def _flip(self, first: Result, now: float) -> Finding | None:
        """A probe that was red and then green on retry: one more flip. The third in a week
        quarantines it and raises a finding about it."""
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO probe_flips(probe, t) VALUES(?, ?)", (first.id, now))
            flips = self.conn.execute("SELECT COUNT(*) FROM probe_flips WHERE probe=? AND t > ?",
                                      (first.id, now - FLIP_WINDOW)).fetchone()[0]
        if flips < FLIPS:
            return None
        about = Result(False, first.id, "loop", "flaky-probe", "a check gives the same answer twice in a row",
                       f"{first.id} went red and then green on retry {flips} times in a week",
                       {"probe": first.id, "flips": flips}, kind="event",
                       title=f"One of Bombadil's own checks ({first.id}) keeps changing its mind, "
                             "so it is set aside.")
        return self._count(about, now, {})

    @_guard(set)
    def quarantined(self, now: float | None = None) -> set[str]:
        """The probes that flipped red to green on retry three times in the last week: leave them out
        of a run (run_all(skip=...)); they come back by themselves when a week has passed."""
        now = _num(now) if _num(now) is not None else time.time()
        rows = self.conn.execute(
            "SELECT probe FROM probe_flips WHERE t > ? GROUP BY probe HAVING COUNT(*) >= ?",
            (now - FLIP_WINDOW, FLIPS)).fetchall()
        return {r["probe"] for r in rows}

    @_guard()
    def release(self, probe_id: str) -> None:
        """Let a quarantined probe run again (its flips are forgotten)."""
        self.conn.execute("DELETE FROM probe_flips WHERE probe=?", (probe_id,))

    # -- reading and what he does --

    def _finding(self, row) -> Finding:
        return Finding(row["fp"], row["component"], row["rule"], row["title"], row["expected"],
                       row["observed"], row["first_t"], row["last_t"], row["n"], row["days"], row["state"],
                       bool(row["fixable"]) and row["component"] != "loop", row["evidence"], row["probe"],
                       row["kind"])

    @_guard(list)
    def open_findings(self) -> list[Finding]:
        """What waits for him (open or reported), newest first."""
        return self.all(SHOWING)

    @_guard(list)
    def all(self, states=None) -> list[Finding]:
        """Counted findings in any of `states` (all of them when None), newest first."""
        wanted = tuple(states) if states is not None else STATES
        marks = ",".join("?" for _ in wanted)
        rows = self.conn.execute(f"SELECT * FROM findings WHERE counted=1 AND state IN ({marks}) "
                                 f"ORDER BY last_t DESC", wanted).fetchall()
        return [self._finding(r) for r in rows]

    @_guard(list)
    def pending(self) -> list[Finding]:
        """Friction seen but not often enough to count yet."""
        rows = self.conn.execute("SELECT * FROM findings WHERE counted=0 ORDER BY last_t DESC").fetchall()
        return [self._finding(r) for r in rows]

    @_guard()
    def get(self, fp: str) -> Finding | None:
        row = self.conn.execute("SELECT * FROM findings WHERE fp=? AND counted=1", (fp,)).fetchone()
        return self._finding(row) if row else None

    @_guard()
    def mark(self, fp: str, state: str) -> Finding | None:
        """What he did with it: reported (the report is held), sent, dismissed (Not now), never, or
        open again (Bring back). Dismissed and never are not raised again until he brings them back."""
        if state not in STATES:
            raise ValueError(f"no such state: {state!r}")
        self.conn.execute("UPDATE findings SET state=? WHERE fp=? AND counted=1", (state, fp))
        return self.get(fp)

    @_guard(0)
    def clear_found(self) -> int:
        """"Clear what it found": every finding he has not said no to, with its evidence and held
        report, is dropped. A problem that is still there is found again at the next run; what he
        dismissed or said never to stays dismissed. Returns how many were cleared."""
        with db.transaction(self.conn):
            fps = [r["fp"] for r in self.conn.execute(
                "SELECT fp FROM findings WHERE state NOT IN ('dismissed', 'never')").fetchall()]
            shown = self.conn.execute("SELECT COUNT(*) FROM findings WHERE counted=1 "
                                      "AND state NOT IN ('dismissed', 'never')").fetchone()[0]
            for fp in fps:
                # The sightings stay (cleared) so an event already read once is not read again.
                self.conn.execute("UPDATE sightings SET cleared=1 WHERE fp=?", (fp,))
                self.conn.execute("DELETE FROM findings WHERE fp=?", (fp,))
        for fp in fps:
            shutil.rmtree(evidence_dir(fp), ignore_errors=True)
            try:
                (paths.loop_dir() / "reports" / f"{_slug(fp)}.md").unlink()
            except OSError:
                pass
        return shown

    @_guard(list)
    def sighting_times(self, fp: str) -> list[float]:
        rows = self.conn.execute("SELECT t FROM sightings WHERE fp=? AND cleared=0 ORDER BY t",
                                 (fp,)).fetchall()
        return [r["t"] for r in rows]

    @_guard(list)
    def add_words(self, fp: str, prompts) -> list[str]:
        """His words of trouble ("still", "won't", "stuck") that came within five minutes of this
        finding's probe firing, written into its evidence as "words". Never the sentence."""
        finding = self.get(fp)
        return attach_words(finding, prompts, fired=self.sighting_times(fp)) if finding else []

    def _complain(self, text: str) -> None:
        """One line on stderr per distinct trouble."""
        if text not in self._complained and len(self._complained) < 50:
            self._complained.add(text)
            print(f"findings: {text}", file=sys.stderr)

