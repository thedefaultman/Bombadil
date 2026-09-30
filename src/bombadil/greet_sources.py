"""Where the welcome line's facts come from: one small reader per source, each returning Facts.

A reader takes its path and its clock, never raises, and returns [] when its file is missing or odd,
so a source that has not landed yet is simply absent. The clauses are worded in share/voice/lines.toml
([fact]), the same in every voice. Whether a fact is said at all is greet.build's call (the ledger).
"""

import json
import math
from datetime import datetime
from pathlib import Path

from . import greet, paths
from .greet import Fact

MANY = 2  # more finished requests than this are said as one count
NAMES = 3  # app names shown in "Since last time you built ..."


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _ts(now) -> float:
    return now.timestamp() if isinstance(now, datetime) else float(now)


def read_rows(path: Path | None = None) -> list[dict]:
    """turns.jsonl as dicts, skipping lines that are not JSON objects."""
    rows = []
    try:
        for line in (path or paths.turns_log()).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        pass
    return rows


def boots(rows) -> float:
    """When the last boot row was written, 0.0 if there is none. The boot row of this login is written
    after the greeting, so while it is built this is the previous boot."""
    return max((t for r in rows if isinstance(r, dict) and r.get("action") == "boot"
                and (t := _num(r.get("t"))) is not None), default=0.0)


def _lower(text: str) -> str:
    # "installed ffmpeg", but "ISO download" keeps its capitals
    return text if text[:1].islower() or text[1:2].isupper() else text[:1].lower() + text[1:]


def turns(rows, since_ts: float, *, now) -> list[Fact]:
    """What finished or failed in the turns since `since_ts`: one fact per finished turn that has a summary,
    one count when there are many, and one for failures. Turns that were stopped say nothing."""
    end = _ts(now)
    done, failed = [], []
    for r in rows:
        t = _num(r.get("t")) if isinstance(r, dict) and "kind" not in r else None
        if t is None or not since_ts < t <= end or r.get("stopped"):
            continue
        summary = r.get("summary")
        if r.get("ok") is False:
            failed.append(t)
        elif isinstance(summary, str) and summary.strip().rstrip("."):
            done.append((t, _lower(summary.strip().rstrip("."))))
    facts = []
    if len(done) > MANY:
        text = greet.phrase("requests_finished", n=len(done))
        facts.append(Fact("turns", "finished", text, f"{len(done)}:{done[-1][0]}"))
    else:
        facts += [Fact(f"turn:{t}", "finished", text) for t, text in done]
    if failed:
        text = greet.phrase("requests_failed", n=len(failed))
        facts.append(Fact("turns-failed", "failed", text, f"{len(failed)}:{failed[-1]}"))
    return [f for f in facts if f.text]


def made(rows, since_boot_ts: float) -> list[Fact]:
    """The apps made since the previous boot, as "Passwords and Tracker" (at most three names)."""
    names: list[str] = []
    for r in rows:
        t = _num(r.get("t")) if isinstance(r, dict) else None
        if t is None or t <= since_boot_ts or not isinstance(r.get("made"), list):
            continue
        for name in r["made"]:
            name = name.strip() if isinstance(name, str) else ""
            if name and name not in names:
                names.append(name)
    if not names:
        return []
    shown = names[:NAMES]
    if len(names) > NAMES:
        shown.append(greet.phrase("more", n=len(names) - NAMES))
    text = greet.join_names(shown)
    return [Fact("made", "made", text, "|".join(names))] if text else []


def updates(path: Path | None = None) -> list[Fact]:
    """updates.json is {"count": 214, "security": 4}; nothing when there are none or the file is odd."""
    try:
        data = json.loads((path or paths.updates_file()).read_text(encoding="utf-8"))
        count, security = data.get("count"), data.get("security", 0)
    except (OSError, ValueError, AttributeError):
        return []
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return []
    if not isinstance(security, int) or isinstance(security, bool) or security < 0:
        security = 0
    text = greet.phrase("updates", count=count)
    if security and text:
        text += greet.phrase("security", security=security)
    return [Fact("updates", "updates", text, f"{count}/{security}", standing=True)] if text else []


def _title(s: dict) -> str:
    title = s.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    role, project = s.get("role"), s.get("project")
    if isinstance(role, str) and isinstance(project, str) and role and project:
        return f"{role} on {project}"
    return next((v for v in (role, project, s.get("key")) if isinstance(v, str) and v), "")


def dev(path: Path | None, now, since_ts: float) -> list[Fact]:
    """The coding sessions in the dev registry: stopped by a restart, finished or failed since `since_ts`.
    Never a waiting fact, because the placeholder already says who is waiting. [] when the registry does
    not exist, as on main today."""
    if path is None:
        path = (paths.dev_dir() if hasattr(paths, "dev_dir") else paths.state_dir() / "dev") / "sessions.json"
    try:
        sessions = json.loads(path.read_text(encoding="utf-8")).get("sessions")
    except (OSError, ValueError, AttributeError):
        return []
    end = _ts(now)
    facts = []
    for s in sessions if isinstance(sessions, list) else []:
        if not isinstance(s, dict) or not (title := _title(s)):
            continue
        state, since = s.get("state"), _num(s.get("since"))
        ident = f"dev:{s.get('key') or title}"
        value = "" if since is None else str(since)
        if state == "asleep" and s.get("last") == "Stopped by a restart":
            progress = s.get("progress") if isinstance(s.get("progress"), dict) else {}
            done, total = _num(progress.get("done")), _num(progress.get("total"))
            if done is not None and total is not None and done >= 0 and total > 0:
                text = greet.phrase("stopped_at", title=title, done=int(done), total=int(total))
            else:
                text = greet.phrase("stopped", title=title)
            facts.append(Fact(f"{ident}:stopped", "stopped", text, value))
        elif state in ("done", "failed") and since is not None and since_ts < since <= end:
            kind = "finished" if state == "done" else "failed"
            facts.append(Fact(f"{ident}:{state}", kind, greet.phrase(kind, title=title), value))
    return [f for f in facts if f.text]
