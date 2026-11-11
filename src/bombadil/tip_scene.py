"""The vocabulary of a tip's scene, how long it runs, and the validator.

A tip's scene is a list of beats (share/tips/tips.toml, `beats = [ {...}, ... ]`) that Kit.TipScene
(share/qml/Bombadil/TipScene.qml) draws. This module is the one place the vocabulary is written down for
Python: the tips table tests call `validate(tip)` on every row, and `tests/test_tip_scene.py` checks that
`duration()` agrees with the QML's own clock to the millisecond. It is standard library only, so agentd
and the tests can import it without Qt. docs/TIP-SCENES.md is the same vocabulary for people.

A beat is a table. Its drawing keys say what happens (`key`, `type`, `stone`, `line`, `queue`, `window`,
`card`, `picture`, `fold`, `hold`); its modifier keys say how (`tag`, `tone`, `rows`, ...). Beats play one
after the other; the keys inside one beat happen together.
"""

from __future__ import annotations

# -- the vocabulary: every key, what it takes, and which drawing key it belongs to --

KEYS = {"super": "⊞", "enter": "↵", "esc": "Esc", "tab": "Tab", "bang": "!"}
FACES = ("rest", "listening", "working", "needs", "done", "stopped", "resting")
TONES = ("amber", "red")
TAGS = ("template", "typed", "model", "output", "caption")
CARDS = ("watching", "now", "machine")
FROM = ("bottom", "left", "right")
NOW_STATES = ("done", "current", "next")
BAR_TONES = ("amber",)

# Drawing keys. A beat needs at least one of them.
DRAWS = ("key", "type", "stone", "line", "queue", "window", "card", "picture", "fold", "hold")
# Modifier keys and the drawing keys they may sit beside (any one of them is enough).
MODIFIERS = {
    "ghost": ("type",),
    "tag": ("line", "queue", "window", "card", "picture", "fold", "type"),
    "tone": ("line",),
    "command": ("line",),
    "counter": ("line",),
    "buttons": ("line",),
    "wait": ("queue",),
    "rows": ("window", "card"),
    "columns": ("window",),
    "lock": ("window",),
    "doc": ("window",),
    "reload": ("window",),
    "from": ("window",),
    "title": ("card",),
    "why": ("card",),
    "boxes": ("picture",),
    "bars": ("picture",),
    "lit": ("picture",),
    "strips": ("fold",),
    "phase": DRAWS,
}
KNOWN = frozenset(DRAWS) | frozenset(MODIFIERS)

MAX_WINDOW_ROWS = 3      # what a window holds on the card's poster (the stage holds four)
MAX_QUEUE = 2            # waiting asks the mini screen holds as chips
MAX_SECONDS = 10         # never over this
TARGET_SECONDS = (6, 8)  # what a scene aims for: `validate(tip, strict=True)` holds it to this
CAPTIONS = 3
CAPTION_WORDS = 3

# -- timing, in milliseconds. TipScene.js mirrors every number here ----------------------

KEY_MS = 360             # a keycap: in, pressed, out (the brief: 300 to 400)
TYPE_MS_PER_CHAR = 40    # 25 characters a second
TYPE_SETTLE_MS = 240     # after the last character
STONE_MS = {"done": 700, "needs": 1600, "working": 600}   # the hop, one knock beat, a roll and a bit
STONE_DEFAULT_MS = 300
LINE_MS = 900            # a line fades in over 200 and stays for reading
LINE_CLEAR_MS = 200
QUEUE_MS = 300           # a waiting ask's chip fades in over 200 and stays a moment
SLIDE_MS = 300           # a window or a picture card slides on (0.16, 1, 0.3, 1)
ROW_MS = 200             # a window row, a column or a drawn box arrives in this long
WINDOW_DWELL_MS = 300
WINDOW_HIDE_MS = 300
CARD_MS = 800            # the desk's own arrival (150) and a pause
BOX_MS = 320
PICTURE_DWELL_MS = 600
FOLD_MS = 1000


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _items(v) -> list:
    return list(v) if isinstance(v, (list, tuple)) else []


def _is_str(v) -> bool:
    return isinstance(v, str)


def _units(s: str) -> int:
    """A string's length as JavaScript counts it, in UTF-16 units: a character past U+FFFF is two, so the
    duration here is the renderer's to the millisecond."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _phase(b: dict) -> int | None:
    """The phase a beat opens, 1 or 2; None for anything else (`True` is not a phase)."""
    v = b.get("phase")
    return v if _is_int(v) and v in (1, 2) else None


def shows_window(b: dict) -> bool:
    """A `window` beat that brings a window (a title), as against one that hides it (false or "")."""
    return isinstance(b.get("window"), str) and b["window"] != ""


def beat_ms(b: dict) -> int:
    """How long one beat takes, from its keys. Keys in one beat run together, so the beat is as long as
    its longest one; `hold` then adds a pause after it, and alone it is the pause."""
    parts = []
    if "key" in b:
        parts.append(KEY_MS)
    if isinstance(b.get("type"), str):
        parts.append(TYPE_MS_PER_CHAR * _units(b["type"]) + TYPE_SETTLE_MS)
    if "stone" in b:
        parts.append(STONE_MS.get(b["stone"], STONE_DEFAULT_MS) if _is_str(b["stone"]) else STONE_DEFAULT_MS)
    if "line" in b:
        parts.append(LINE_MS if b["line"] != "" else LINE_CLEAR_MS)
    if "queue" in b:
        parts.append(QUEUE_MS)
    if "window" in b:
        if shows_window(b):
            slide = 0 if b.get("reload") else SLIDE_MS
            parts.append(slide + ROW_MS * len(_items(b.get("rows"))) + ROW_MS * len(_items(b.get("columns")))
                         + WINDOW_DWELL_MS)
        else:
            parts.append(WINDOW_HIDE_MS)
    if "card" in b:
        parts.append(CARD_MS)
    if "picture" in b:
        parts.append(SLIDE_MS + BOX_MS * max(len(_items(b.get("boxes"))), len(_items(b.get("bars"))))
                     + PICTURE_DWELL_MS)
    if "fold" in b:
        parts.append(FOLD_MS)
    base = max(parts) if parts else 0
    if "hold" in b:
        hold = b["hold"]
        base += hold if _is_int(hold) and hold > 0 else 0
    return base


def beat_times(beats: list) -> list[tuple[int, int]]:
    """(start, length) of every beat, in order."""
    out, at = [], 0
    for b in beats:
        n = beat_ms(b) if isinstance(b, dict) else 0
        out.append((at, n))
        at += n
    return out


def duration(beats: list) -> int:
    """The scene's length in ms: where the last beat ends. The scene holds that frame for ever."""
    times = beat_times(beats)
    return times[-1][0] + times[-1][1] if times else 0


def phase_starts(beats: list) -> tuple[int, int]:
    """When phases 1 and 2 begin: at the beats that say `phase = 1` and `phase = 2`, or, with no marks,
    a third and two thirds of the way through."""
    times = beat_times(beats)
    total = duration(beats)
    marks = {}
    for b, (start, _n) in zip(beats, times, strict=False):
        if isinstance(b, dict) and _phase(b) and _phase(b) not in marks:
            marks[_phase(b)] = start
    if 1 in marks and 2 in marks:
        return marks[1], marks[2]
    return total // 3, total * 2 // 3


# -- the validator -----------------------------------------------------------------------

def _words(s) -> int:
    return len(str(s).split())


def _check_beat(i: int, b, out: list[str]) -> None:
    where = f"beat {i + 1}"
    if not isinstance(b, dict):
        out.append(f"{where}: a beat is a table, not {type(b).__name__}")
        return
    for k in b:
        if k not in KNOWN:
            out.append(f"{where}: unknown key {k!r}")
    draws = [k for k in DRAWS if k in b]
    if not draws:
        out.append(f"{where}: draws nothing (it needs one of {', '.join(DRAWS)})")
    for k in b:
        if k in MODIFIERS and not any(d in b for d in MODIFIERS[k]):
            out.append(f"{where}: {k!r} goes with {' or '.join(repr(d) for d in MODIFIERS[k])}")

    def need(cond: bool, msg: str) -> None:
        if not cond:
            out.append(f"{where}: {msg}")

    if "key" in b:
        need(_is_str(b["key"]) and b["key"] in KEYS, f"key must be one of {', '.join(KEYS)} (not {b['key']!r})")
    if "type" in b:
        need(isinstance(b["type"], str) and b["type"] != "", "type is the words typed, a string")
    if "ghost" in b:
        need(isinstance(b["ghost"], str) and b["ghost"] != "", "ghost is the faint completion, a string")
    if "stone" in b:
        need(_is_str(b["stone"]) and b["stone"] in FACES,
             f"stone must be one of {', '.join(FACES)} (not {b['stone']!r})")
    if "line" in b:
        need(_is_str(b["line"]), "line is a string (\"\" clears it)")
    if "tone" in b:
        need(b["tone"] in TONES, f"tone must be amber or red (not {b['tone']!r})")
    if "command" in b:
        need(isinstance(b["command"], str) and b["command"] != "", "command is a string")
    if "counter" in b:
        need(isinstance(b["counter"], bool) or _is_int(b["counter"]) and b["counter"] >= 0,
             "counter is true, false or the seconds to start from")
    if "buttons" in b:
        bt = b["buttons"]
        need(isinstance(bt, list) and 0 < len(bt) <= 2 and all(isinstance(x, str) and x for x in bt),
             "buttons is one or two labels, such as [\"Undo\", \"Details\"]")
    if "queue" in b:
        q = b["queue"]
        need(isinstance(q, list) and len(q) <= MAX_QUEUE and all(isinstance(x, str) and x for x in q),
             f"queue is the asks that wait, one to {MAX_QUEUE} of them, as typed ([] puts the chips away)")
    if "wait" in b:
        need(isinstance(b["wait"], str) and b["wait"] != "", "wait is the chip's label, a string")
    if "window" in b:
        need(_is_str(b["window"]) or b["window"] is False, "window is a title (false or \"\" slides it out)")
    if "from" in b:
        need(b["from"] in FROM, f"from must be one of {', '.join(FROM)}")
    for k in ("doc", "reload"):
        if k in b:
            need(isinstance(b[k], bool), f"{k} is true or false")
    if "lock" in b:
        need(isinstance(b["lock"], str) and b["lock"] != "", "lock is the lock screen's words, a string")
    if "card" in b:
        need(b["card"] in CARDS, f"card must be one of {', '.join(CARDS)} (not {b['card']!r})")
    for k in ("title", "why"):
        if k in b:
            need(isinstance(b[k], str) and b[k] != "", f"{k} is a string")
    if "rows" in b:
        rows = b["rows"]
        ok = isinstance(rows, list) and all(
            isinstance(r, str) or isinstance(r, list) and r and all(isinstance(c, (str, int)) for c in r)
            for r in rows)
        need(ok, "rows is a list of strings, or of [label, value] lists")
        if ok and "window" in b:
            need(len(rows) <= MAX_WINDOW_ROWS, f"a window shows at most {MAX_WINDOW_ROWS} rows, so the card's "
                 f"poster holds them all (this has {len(rows)})")
        if ok and b.get("card") == "now":
            for r in rows:
                if isinstance(r, list) and len(r) > 1:
                    need(r[1] in NOW_STATES, f"a Now row's state is one of {', '.join(NOW_STATES)}")
        if ok and b.get("card") == "machine":
            for r in rows:
                if isinstance(r, list) and len(r) > 1:
                    need(_is_int(r[1]) and 0 <= r[1] <= 100, "a Machine row's value is a meter from 0 to 100")
    if "columns" in b:
        cols = b["columns"]
        need(isinstance(cols, list) and all(isinstance(c, list) and c and all(isinstance(x, str) for x in c)
                                            for c in cols), "columns is a list of [heading, cell, cell, ...]")
    if "picture" in b:
        need(isinstance(b["picture"], str) and b["picture"] != "", "picture is its title, a string")
        has = "boxes" in b or "bars" in b
        need(has, "a picture needs boxes or bars")
        need(not ("boxes" in b and "bars" in b), "a picture has boxes or bars, not both")
    if "boxes" in b:
        need(isinstance(b["boxes"], list) and b["boxes"] and all(isinstance(x, str) for x in b["boxes"]),
             "boxes is a list of names")
    if "bars" in b:
        bars = b["bars"]
        need(isinstance(bars, list) and bars and all(
            isinstance(x, list) and len(x) >= 2 and isinstance(x[0], str) and _is_int(x[1]) and 0 <= x[1] <= 100
            and (len(x) < 3 or x[2] in BAR_TONES) for x in bars), "bars is a list of [label, percent] or [label, percent, \"amber\"]")
    if "lit" in b:
        need("boxes" in b, "lit lights one of the boxes: it goes with boxes, not bars (a bar is amber instead)")
        n = len(_items(b.get("boxes")))
        need(_is_int(b["lit"]) and 0 <= b["lit"] < max(n, 1), "lit is the index of a box")
    if "fold" in b:
        need(isinstance(b["fold"], bool), "fold is true (cards to strips) or false (back)")
    if "strips" in b:
        need(isinstance(b["strips"], list) and all(isinstance(x, str) and x for x in b["strips"]),
             "strips is a list of labels")
    if "hold" in b:
        need(_is_int(b["hold"]) and b["hold"] > 0, "hold is a number of milliseconds")
    if "phase" in b:
        need(_phase(b) is not None, "phase is 1 or 2: the beat that opens that phase")

    # The truth rule: every string a scene shows is tagged. `type` is the tip's own words and is typed.
    # A line, a window, a card, a picture and a fold with strips each carry one tag for their strings.
    needs_tag = (
        (isinstance(b.get("line"), str) and b["line"] != "")
        or shows_window(b) or "card" in b or "picture" in b or bool(_items(b.get("strips")))
        or bool(b.get("wait"))
    )
    tag = b.get("tag")
    if needs_tag and tag is None:
        out.append(f"{where}: has strings and no tag (one of {', '.join(TAGS)})")
    elif tag is not None:
        if tag not in TAGS:
            out.append(f"{where}: tag must be one of {', '.join(TAGS)} (not {tag!r})")
        elif "type" in b and tag != "typed" and not needs_tag:
            out.append(f"{where}: what is typed is tagged typed")


def validate(tip: dict, allow_empty: bool = False, strict: bool = False) -> list[str]:
    """Everything wrong with a tip's scene, one sentence each (an empty list means it is sound):
    keys the vocabulary does not know, values it does not take, a scene over 10 s, strings without a
    tag, and a number of captions other than three (at most three words each). `tip` is a row of the
    tips table, or any dict with `beats` and `captions`. `allow_empty`: a row whose beats are not written
    yet is not a problem. `strict`: a scene also has to run 6 to 8 s, as the shipped tips do (a test
    fixture or a draft may be shorter)."""
    out: list[str] = []
    beats = tip.get("beats")
    if beats is None:
        beats = []
    if not isinstance(beats, list):
        return ["beats is a list of tables"]
    if not beats and not allow_empty:
        out.append("no beats")
    for i, b in enumerate(beats):
        _check_beat(i, b, out)
    marks = [_phase(b) for b in beats if isinstance(b, dict) and _phase(b)]
    if marks and marks != [1, 2]:
        out.append("phase marks come as one phase = 1 and then one phase = 2, or not at all")
    if all(isinstance(b, dict) for b in beats):
        total = duration(beats)
        if total > MAX_SECONDS * 1000:
            out.append(f"the scene runs {total / 1000:.1f} s, over the {MAX_SECONDS} s limit")
        elif strict and beats and not TARGET_SECONDS[0] * 1000 <= total <= TARGET_SECONDS[1] * 1000:
            out.append(f"the scene runs {total / 1000:.1f} s, and a tip's runs {TARGET_SECONDS[0]} to "
                       f"{TARGET_SECONDS[1]} s")
    caps = tip.get("captions")
    if not isinstance(caps, list) or len(caps) != CAPTIONS:
        n = len(caps) if isinstance(caps, list) else 0
        out.append(f"captions: there are three, one for each phase (this has {n})")
    else:
        for c in caps:
            if not isinstance(c, str) or not c.strip():
                out.append("captions: each is a few words")
            elif _words(c) > CAPTION_WORDS:
                out.append(f"captions: {c!r} is over {CAPTION_WORDS} words")
    return out


def strings(tip: dict) -> list[tuple[str, str]]:
    """(tag, text) of every string the scene shows, for the truth test: `template` strings are matched
    against the real sentences in the code, `typed` against the tip's words, the rest are plausible only."""
    out = []
    for b in tip.get("beats") or []:
        tag = b.get("tag", "typed" if "type" in b else "")
        if "type" in b:
            out.append(("typed", b["type"]))
        if b.get("line"):
            out.append((tag, b["line"]))
        if b.get("command"):
            out.append((tag, b["command"]))
        for x in _items(b.get("buttons")):
            out.append(("template", x))
        for x in _items(b.get("queue")):
            out.append(("typed", x))                       # an ask that waits is words the person typed
        if b.get("wait"):
            out.append((tag, b["wait"]))
        if shows_window(b):
            out.append((tag, b["window"]))
        if b.get("lock"):
            out.append((tag, b["lock"]))
        # The steps of a Now card are the agent's own plan, whatever the beat's tag says of its title and why.
        row_tag = "model" if b.get("card") == "now" else tag
        for r in _items(b.get("rows")):
            out.append((row_tag, r if isinstance(r, str) else str(r[0])))
            if not isinstance(r, str) and len(r) > 1 and isinstance(r[1], str) and r[1] not in NOW_STATES:
                out.append((row_tag, r[1]))                # a figure or a word in a row, read as the row's tag
        # A card's title and its why line are the desk's own words, whatever the beat's tag says of the rest: a beat
        # can carry the agent's reply on its line (model) and the card that goes with it (template) together.
        if b.get("title"):
            out.append(("template", b["title"]))
        if b.get("why"):
            out.append(("template", b["why"]))
        for c in _items(b.get("columns")):
            out.extend((tag, x) for x in c)
        if b.get("picture"):
            out.append((tag, b["picture"]))
        out.extend((tag, x) for x in _items(b.get("boxes")))
        out.extend((tag, x[0]) for x in _items(b.get("bars")))
        out.extend((tag, x) for x in _items(b.get("strips")))
    for c in tip.get("captions") or []:
        out.append(("caption", c))
    return out
