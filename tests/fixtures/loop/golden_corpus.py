"""The hand-written corpus the counting is checked against, and what it takes to feed it in.

`golden_asks.jsonl` is one ask per line: what he typed, the day and time, what the turn did (a list
of tool events in short form), and the request it truly is a repeat of (`group`). A group name is
the label a person would give the request; "single" is an ask nothing else repeats; "none" is an
ask that must not count at all (`expect` says why). `golden_pairs.jsonl` is 100+ pairs picked and
labelled by hand: easy ones, the hard ones a rule gets wrong, and the near misses.

It is synthetic. It was written to look like the asks of one person on an Arch machine with a few
apps; it says nothing about how a real ledger will group. `LoopStore.replay` on his own turns.jsonl
is the check that does.

An event is a string (a shell command: the Bash tool) or [tool name, input].
"""

import json
import time
from pathlib import Path

HERE = Path(__file__).parent
BASE = (2026, 9, 7)           # day 0: a Monday
APPS = (("passwords", "Passwords"), ("runs", "Runs"), ("tracker", "Tracker"))


def epoch(day: int, at: str) -> float:
    """Local epoch seconds of day `day` (0 = BASE) at "HH:MM". Local, because the loop counts days by
    the local date."""
    hour, minute = (int(x) for x in at.split(":"))
    return time.mktime((BASE[0], BASE[1], BASE[2] + day, hour, minute, 0, 0, 0, -1))


def load_asks(name: str = "golden_asks.jsonl") -> list[dict]:
    """Every ask of a corpus file, oldest first, with `t` (when he asked) added. `golden_holdout.jsonl`
    is the smaller corpus that was written after the rules were settled."""
    out = []
    for line in (HERE / name).read_text().splitlines():
        if line.strip():
            ask = json.loads(line)
            ask["t"] = epoch(ask["day"], ask["at"])
            out.append(ask)
    return sorted(out, key=lambda a: (a["t"], a["id"]))


def load_pairs() -> list[dict]:
    """The hand-labelled pairs: {"a", "b", "same", "why"}, a and b ask ids."""
    out = []
    for line in (HERE / "golden_pairs.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out.append({"a": row[0], "b": row[1], "same": bool(row[2]), "why": row[3] if len(row) > 3 else ""})
    return out


def counted_asks(asks: list[dict] | None = None) -> list[dict]:
    """The asks that are meant to count: everything but the "none" ones."""
    return [a for a in (asks if asks is not None else load_asks()) if a["group"] != "none"]


def tool_events(ask: dict) -> list[dict]:
    """The events of the turn's own log, as agentd writes them."""
    out = []
    for i, ev in enumerate(ask.get("events") or []):
        name, tool_input = ("Bash", {"command": ev}) if isinstance(ev, str) else (ev[0], ev[1])
        out.append({"t": ask["t"] + 1 + i, "type": "event", "kind": "tool", "name": name, "input": tool_input})
    return out


def row_of(ask: dict, logs_dir: Path | None = None) -> dict:
    """The ledger v2 row the turn would have written."""
    events = tool_events(ask)
    names = list(dict.fromkeys(e["name"] for e in events))
    row = {
        "v": 2, "t": ask["t"] + ask.get("seconds", 10), "started": ask["t"], "seconds": ask.get("seconds", 10),
        "prompt": ask["text"], "result": "done", "ok": ask.get("ok", True), "provider": "claude",
        "id": ask["id"], "origin": ask.get("origin", "typed"), "tools": {"n": len(events), "names": names},
        "snapshot": None, "summary": "Done." if ask.get("changed") else "",
    }
    if logs_dir is not None:
        row["details"] = str(Path(logs_dir) / f"{ask['id']}.jsonl")
    return row


def write_corpus(directory: Path, asks: list[dict] | None = None) -> tuple[Path, Path]:
    """Write turns.jsonl and the turns' own logs under `directory`; returns (turns.jsonl, the logs'
    directory)."""
    directory = Path(directory)
    logs = directory / "turns"
    logs.mkdir(parents=True, exist_ok=True)
    rows = []
    for ask in asks if asks is not None else load_asks():
        lines = [{"t": ask["t"], "type": "event", "kind": "text", "text": "on it"}, *tool_events(ask)]
        (logs / f"{ask['id']}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
        rows.append(row_of(ask, logs))
    rows.sort(key=lambda r: r["t"])
    turns = directory / "turns.jsonl"
    turns.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return turns, logs


def app_list():
    """The apps the corpus's person has, as the launcher would list them."""
    from bombadil import apps
    return [apps.App(name, Path("."), title) for name, title in APPS]
