"""Which Bombadil this is: "Bombadil 2026.10.06 (d9dde3b), installed 2 Oct".

Two files say it, and nothing here asks pacman or agentd, so it answers offline and with no daemon:

- /usr/share/bombadil/VERSION, one line, `YYYY.MM.DD (commit)`, written by the package build. It moves with every
  update, so it is the version that is running.
- /var/lib/bombadil/install.json, written by the installer. Only its dates are used: `first_installed` (the first
  install of this home; a record from before that key existed gives its `date`) and, when `mode` is "refresh",
  `date` (the latest refresh). Its `image_version` stays at the image the machine was installed from.

A file that is missing or unreadable costs the words it would have given, never an error.
"""

import json
import re
from datetime import datetime

from . import paths

UNKNOWN = "Bombadil (version unknown)"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LINE = re.compile(r"(?P<number>[^\s()]+)(?:\s*\((?P<commit>[^()]*)\))?")


def read() -> str:
    """The VERSION line as the package wrote it, or "" when there is none to read."""
    try:
        text = paths.version_file().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def _record() -> dict:
    try:
        doc = json.loads(paths.install_record().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _day(stamp) -> str:
    """'2026-10-02T09:14:00+0000' as '2 Oct' (the day as the machine then wrote it); "" if it is not a date."""
    if not isinstance(stamp, str):
        return ""
    for parse in (lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z"), datetime.fromisoformat):
        try:
            when = parse(stamp)
        except ValueError:
            continue
        return f"{when.day} {MONTHS[when.month - 1]}"
    return ""


def facts() -> dict:
    """Everything the line is made from: version (the VERSION line), number, commit, installed and refreshed (as
    the installer wrote them, or None), and mode. A key is None when its file does not say."""
    line = read()
    m = _LINE.fullmatch(line) if line else None
    record = _record()
    first = record.get("first_installed") or record.get("date")
    mode = record.get("mode") if isinstance(record.get("mode"), str) else None
    latest = record.get("date")
    refreshed = latest if mode == "refresh" and _day(latest) and _day(latest) != _day(first) else None
    return {"version": line or None, "number": m.group("number") if m else None,
            "commit": (m.group("commit") or "").strip() or None if m else None,
            "installed": first if _day(first) else None, "refreshed": refreshed, "mode": mode}


def version_line() -> str:
    """The answer to "what version is this". Never raises."""
    try:
        f = facts()
        if not f["number"]:
            return UNKNOWN
        line = f"Bombadil {f['number']}" + (f" ({f['commit']})" if f["commit"] else "")
        if f["installed"]:
            line += f", installed {_day(f['installed'])}"
        if f["refreshed"]:
            line += f", refreshed {_day(f['refreshed'])}"
        return line
    except Exception:  # noqa: BLE001 - a line about the version must never be the thing that breaks
        return UNKNOWN
