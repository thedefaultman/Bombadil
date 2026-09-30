"""What counts as an ask, which asks are the same request, and how a repeated one ages.

Everything here is local and plain: no model, no embeddings. Two asks are the same request when
their words, the kind of thing asked, the things they name and what the turn actually did
(`route.py`) agree enough. The rule is fixed so it can be explained:

    two asks join when their verb classes agree (open, ask and tell-me-when are all "looking"
    and agree with each other), and
        0.4 x text overlap + 0.6 x route overlap >= 0.5,
    or on text alone at 0.8.        (two asks that name different things never join)

Text is compared as trigrams of what is left after a fixed pass: prefixes the pill adds, numbers
and paths and addresses turned into slots, a filler list, a suffix stripper and a table of
folded synonyms. Route is the overlap of the two turns' topic sets. The design hangs together
on a written corpus (tests/fixtures/loop); whether it holds on his real asks is what
`LoopStore.replay` is for.

`counted()` says whether an ask counts and why not. A turn he stopped, that failed, or that he
undid within 60 s, and its rephrase within 2 minutes, are friction: returned with their reason,
never dropped silently, and attached to the group they would have joined.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields
from itertools import pairwise

from .. import launcher
from . import route as routes
from .ledger import Request, day_of

# -- what counts --

MAX_TEXT = 300            # a selection or pasted text longer than this is not an ask
UNDO_WINDOW = 60          # seconds after a turn within which an undo means it was not wanted
REPHRASE_WINDOW = 120     # seconds after a failed turn within which the next ask is a retry
FRICTION = ("failed", "stopped", "undone", "rephrase")

HALF_LIFE_DAYS = 14       # an ask weighs 1 and halves every two weeks
LIST_DAYS = 30            # a group with no ask for this long leaves the list, keeping its counts
TEXT_DAYS = 90            # after this its words are dropped and only the counts stay

TEXT_ALONE = 0.8          # the join rule's numbers: text alone, or text and route together
W_TEXT, W_ROUTE, JOIN = 0.4, 0.6, 0.5

_PREFIX_APP = re.compile(r"^\s*\[from app [^\]]*\]\s*", re.IGNORECASE)
_PREFIX_ABOUT = re.compile(r"^\s*About\s+(?:[~/.][^\n:]*|\S+\.\w{1,6}):\s*", re.IGNORECASE)

# Text about these is never counted, whatever the route says.
PRIVATE_TEXT = re.compile(
    r"\.ssh\b|ssh[- ]keys?|\.gnupg|\bgpg\b|\bpgp\b|key-?ring|keychain|kwallet|\bsecrets?\b|\btokens?\b|"
    r"api[_ -]?keys?|\.env\b|private[_ -]?keys?|credentials?|seed phrase|recovery (?:phrase|codes?)|"
    r"\b2fa\b|\botp\b|\bpasswords?\s+(?:for|of|to)\b|\bmy\s+\w+\s+password\b|\bpassphrase\b|"
    r"what(?:'s| is)\s+(?:the |my )?password", re.IGNORECASE)


def clean_text(text: str) -> str:
    """His words without the prefixes the pill and the apps add ("[from app x]", "About <path>:")."""
    t = _PREFIX_APP.sub("", str(text))
    return _PREFIX_ABOUT.sub("", t).strip()


def is_private_text(text: str) -> bool:
    return bool(PRIVATE_TEXT.search(clean_text(text)))


def is_signin(req: Request) -> bool:
    return req.signin


def friction_of(req: Request) -> str:
    """Why a turn was not a clean answer to what he asked: stopped, failed, undone within a minute.
    A sign-in turn is neither: the provider needed him, not the other way round."""
    if req.stopped or req.stopped_at is not None:
        return "stopped"
    if req.ok is not True:
        return "" if req.signin else "failed"
    if req.undone_at is not None and req.undone_at - req.ended <= UNDO_WINDOW:
        return "undone"
    return ""


def counted(req: Request, prev: Request | None = None, route: Sequence[str] = (),
            nevers: Iterable["Profile"] = (), things: dict | None = None) -> tuple[bool, str]:
    """(does this ask count, why not). `prev` is the turn before it, `route` the topics this turn
    touched, `nevers` the signatures of groups he said Never to. Reasons: `origin`, `empty`, `shell`,
    `sign-in`, `private`, `long`, the friction ones (`stopped`, `failed`, `undone`, `rephrase`),
    `never`; "" when it counts."""
    if req.origin != "typed":          # buttons, apps, sessions, routines, the loop, `bombadil ask`
        return False, "origin"
    text = clean_text(req.text)
    if not text:
        return False, "empty"
    if req.text.lstrip().startswith("!"):
        return False, "shell"
    if req.signin:
        return False, "sign-in"
    if is_private_text(text) or routes.is_private(route):
        return False, "private"
    if len(text) > MAX_TEXT:
        return False, "long"
    why = friction_of(req)
    if why:
        return False, why
    retry = prev is not None and prev.id != req.id and 0 <= req.t - prev.ended <= REPHRASE_WINDOW
    if retry and friction_of(prev) and _alike(prev.text, text, things):
        return False, "rephrase"
    nevers = list(nevers)
    if nevers:
        p = make_profile(req, route, things)
        if any(joins(p, n) for n in nevers):
            return False, "never"
    return True, ""


def _alike(a: str, b: str, things: dict | None) -> bool:
    """Is `b` the same thing said again: a content word or a named thing in common."""
    ta, tb = _content(a), _content(b)
    if ta & tb:
        return True
    return bool(things and named_things(a, things) & named_things(b, things))


def _content(text: str) -> set[str]:
    return {s for _, s in _words(text) if not s.startswith("<")}


# -- words --

# Said before the point and worth nothing to a comparison. Longest first when matched.
FILLER_PHRASES = (
    "show me", "tell me", "let me see", "let me", "can you", "could you", "would you", "will you", "do you",
    "i want to", "i would like to", "i'd like to", "id like to", "i need to", "i want", "go ahead and",
    "real quick", "right now", "for me", "take a look at", "have a look at", "give me", "get me", "what is",
    "what are", "how much", "how many", "is there", "are there", "i wonder", "thank you", "thanks",
)
def _w(words: str) -> list[str]:
    return words.split()


# Small words, and the verbs that only mean "look at" (they tell the verb class, not the subject).
STOPWORDS = frozenset(_w(
    "a an the my me mine i we you your our it its is are was were be been am do does did to of and or but so "
    "that this these those there here just now again also very really some any for at in by with about what "
    "whats hows thats theres whos which who whom whose why how up out on please pls hey ok okay hi then when much "
    "many lot lots thing "
    "things stuff show display see view check look list print tell give get let can could would will should "
    "have has had if as from all most app apps application"))

# Said one way or another, meant one way. Keys are run through the suffix stripper too.
SYNONYMS = {
    "use": _w("eat hog consume take drain chew burn using used uses gobble suck occupy"),
    "memory": _w("ram mem"),
    "disk": _w("storage hdd ssd drive drives"),
    "cpu": _w("processor processors"),
    "slow": _w("sluggish laggy lag lagging crawl slowly"),
    "wifi": _w("wlan wireless internet connection connectivity online"),
    "delete": _w("remove erase wipe trash purge empty"),
    "tidy": _w("clean organize organise sort declutter arrange"),
    "download": _w("grab fetch"),
    "find": _w("locate lookup search"),
    "upgrade": _w("update"),
    "timer": _w("alarm countdown stopwatch"),
    "open": _w("launch"),
    "close": _w("quit exit"),
    "big": _w("large huge heavy giant"),
    "error": _w("bug fail crash crashing"),
    "weather": _w("forecast"),
    "rain": _w("raining rainy"),
    "hot": _w("warm overheating overheat"),
    "dim": _w("dimmer darker"),
    "bright": _w("brighter brighten"),
    "run": _w("running jog ran"),
    "log": _w("record jot journal"),
    "docker": _w("container containers"),
}
# Phrases folded before the words are split.
PHRASES = (
    (r"\bwi[\s-]?fi\b", "wifi"), (r"\be-mail\b", "email"), (r"\bpull up\b", "open"), (r"\bbring up\b", "open"),
    (r"\bgo to\b", "open"), (r"\bswitch to\b", "open"), (r"\bturn off\b", "disable"), (r"\bturn on\b", "enable"),
    (r"\bclean up\b", "clean"), (r"\bsort out\b", "sort"), (r"\blook (?:for|up)\b", "find"),
    (r"\bback up\b", "backup"), (r"\bset ?up\b", "install"), (r"\bsign in\b", "login"), (r"\blog in\b", "login"),
)

_SLOTS = (
    (re.compile(r"(?:https?://|www\.)\S+"), " <url> "),
    (re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|in|co|uk|de|fr|app|ai|so|me|us|edu|gov)\b"
                r"(?:/\S*)?"), " <url> "),
    (re.compile(r"(?:(?<=\s)|^)(?:~|\.{1,2}|\$home)?/\S*"), " <path> "),
    (re.compile(r"\b[\w-]+\.(?:txt|pdf|csv|png|jpe?g|md|py|sh|json|toml|zip|tar|gz|mp4|mkv|mp3|log|iso|qml|odt|"
                r"docx?|xlsx?|svg|gif|webm|flac)\b"), " <path> "),
    (re.compile(r"\d+(?:[.,:]\d+)*"), " <n> "),
)

# (suffix, replacement, shortest stem left): the first that fits wins, then a final "e" goes.
SUFFIX_RULES = (
    ("ations", "", 4), ("ation", "", 4), ("sses", "ss", 2), ("ies", "y", 2), ("ied", "y", 2), ("ches", "ch", 2),
    ("shes", "sh", 2), ("xes", "x", 2), ("ingly", "", 4), ("ing", "", 3), ("edly", "", 4), ("ed", "", 3),
    ("ness", "", 3), ("ments", "", 4), ("ment", "", 4), ("ables", "", 4), ("able", "", 4), ("ibles", "", 4),
    ("ible", "", 4), ("fully", "", 4), ("ful", "", 4), ("less", "", 4), ("ly", "", 4), ("ity", "", 4),
    ("s", "", 3),
)


def stem(word: str) -> str:
    """A consistent root for comparing words, not a dictionary word: installing, installed and
    installs are all "install"; files and file are "fil"."""
    w = word
    if not w.isalpha() or len(w) <= 3:
        return w
    for suffix, repl, least in SUFFIX_RULES:
        if w.endswith(suffix) and len(w) - len(suffix) >= least:
            if suffix == "s" and w.endswith(("ss", "us", "is")):
                continue
            w = w[:-len(suffix)] + repl
            if suffix in ("ing", "ed", "edly", "ingly") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lszaeiou":
                w = w[:-1]   # running -> run, but not installing -> instal
            break
    if w.endswith("e") and len(w) >= 4 and not w.endswith(("ee", "ye", "oe")):
        w = w[:-1]
    return w


_FOLD = {stem(w): canon for canon, words in SYNONYMS.items() for w in (canon, *words)}
_PHRASES = tuple((re.compile(p), r) for p, r in PHRASES)
_FILLERS = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in sorted(FILLER_PHRASES, key=len, reverse=True))
                      + r")\b")


def _raw_tokens(text: str) -> list[str]:
    """Lowercase words and slots of his text, nothing dropped."""
    t = launcher.normalize(clean_text(text))
    for rx, slot in _SLOTS:
        t = rx.sub(slot, t)
    for rx, repl in _PHRASES:
        t = rx.sub(repl, t)
    t = t.replace("'", "").replace("’", "")
    return re.findall(r"<[a-z]+>|[^\W_]+", t)


def _words(text: str) -> list[tuple[str, str]]:
    """(the word as he said it, its folded root) for what is left after fillers and small words."""
    t = " ".join(_raw_tokens(text))
    t = _FILLERS.sub(" ", t)
    out = []
    for w in t.split():
        if w in STOPWORDS or (len(w) == 1 and not w.startswith("<")):
            continue
        s = stem(w)
        out.append((w, _FOLD.get(s, s)))
    return out


def normalise(text: str) -> str:
    """The comparable form of an ask: "what's eating my RAM?" and "memory hog" are both
    "memory use"-ish (a fold of eat/hog/use, ram/memory), "timer for 25 minutes" is "timer <n> minut"."""
    seen: dict[str, None] = {}
    for _, s in _words(text):
        seen[s] = None
    return " ".join(seen)


def trigrams(s: str) -> frozenset[str]:
    s = f" {s} "
    return frozenset(s[i:i + 3] for i in range(len(s) - 2))


def text_overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Dice overlap of two trigram sets: 1 for the same words, near 0 for different ones."""
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


# -- verbs --

# One row per class. The first word of an ask that belongs to a class other than "ask" and "open"
# says what the ask is; with none of those, a question word makes it an ask, and "open" counts only
# when the ask names an app or a panel. (`log` is matched as a whole word, not as a root: logs are
# a thing you read.)
VERBS = {
    "open": _w("open launch start run bring pull go goto switch close quit exit hide show display view see"),
    "ask": _w("what whats which who whom whose why how explain describe check status tell"),
    "make": _w("make build create generate write draft design add produce prepare compose code"),
    "change": _w("change set toggle enable disable increase decrease raise lower dim brighten mute unmute "
                 "rename adjust configure down brighter dimmer louder quieter higher lower bigger smaller turn "
                 "restart reboot kill terminate pause resume reload zip unzip compress extract copy move backup "
                 "connect disconnect pair play next skip previous send upload share"),
    "install": _w("install uninstall download upgrade update setup reinstall"),
    "tidy": _w("tidy clean organize organise sort clear delete remove erase wipe purge trash declutter "
               "archive empty arrange"),
    "fix": _w("fix repair debug troubleshoot resolve solve unbreak broken crash crashes fail fails failing stuck"),
    "find": _w("find search locate lookup grep"),
}
LOG_WORDS = frozenset(_w("log record track jot journal logging"))
VERB_CLASSES = ("open", "ask", "make", "change", "install", "tidy", "log", "fix", "find", "tell-me-when")
_VERB: dict[str, str] = {}
for _cls, _list in VERBS.items():
    for _word in _list:
        _VERB.setdefault(stem(_word), _cls)
_OPEN_WORDS = frozenset(VERBS["open"])
_LOOK = {"show", "display", "view", "see"}
_QUESTION_START = {"is", "are", "does", "did", "has", "was", "were", "am"}   # "do" and "have" also start orders
_DETERMINERS = {"the", "my", "a", "an", "this", "that", "your", "our", "new", "its", "his", "her", "their"}
_POLITE = {"please", "pls", "to", "you", "and", "then", "also", "just", "now", "can", "could", "would", "will",
           "lets"}
_ENTRY = {"add", "put", "enter", "write"}   # with a number in it, these put a record somewhere, not build a thing
_WH = {"what", "whats", "which", "who", "whom", "whose", "why", "how", "where", "wheres", "when"}
_WAITS = re.compile(
    r"\btell me when\b|\blet me know\b|\bnotify me\b|\balert me\b|\bping me\b|\bwake me\b|\bremind(?:er)?\b|"
    r"\b(?:timer|alarm|countdown)\b|\b(?:done|finished|ready|complete|completed|built|over) yet\b|"
    r"\b(?:is|are|has|have|did|does)\b.{0,30}\b(?:done|finished|ready|complete|completed|finish|over)\b|"
    r"\bwhen (?:it|its|it's|the \w+(?: \w+)?) (?:is |are |gets |has )?(?:done|finished|ready|complete)\b|"
    r"\bonce (?:it|its|it's) (?:is )?(?:done|finished|ready)\b", re.IGNORECASE)
_LOG = re.compile(r"\b(?:add|put|enter|write)\b.{0,30}\b(?:log|journal|diary|tracker)\b", re.IGNORECASE)
_MAKE_CHANGE = re.compile(r"\bmake\b.{0,20}\b(?:brighter|dimmer|louder|quieter|bigger|smaller|darker|lighter|"
                          r"warmer|cooler|faster|slower|shorter|longer|briefer|simpler)\b", re.IGNORECASE)
_THING_WORDS = {"app", "application", "window", "panel", "page", "program"}
_TOO = re.compile(r"\btoo (?:bright|dark|dim|loud|quiet|small|big|large|tiny|hot|cold|slow|fast|warm)\b")


_S_VERBS = {"whats", "wheres", "crashes", "fails", "status"}


def _is_verbish(w: str) -> bool:
    """Plural words are things, not verbs: "downloads" is a folder and "logs" are to read, while
    "download" and "log" are what he asks for."""
    return not w.endswith("s") or w.endswith(("ss", "us", "is")) or w in _S_VERBS


def verb_class(text: str, named: Iterable[str] = (), bare: bool = False) -> str:
    """Which of the ten kinds of ask this is: open, ask, make, change, install, tidy, log, fix, find,
    tell-me-when. `named` is what the text names (see `named_things`); `bare` says the ask is nothing
    but "open <that thing>"."""
    t = clean_text(text).lower().replace("’", "'")
    if _WAITS.search(t):
        return "tell-me-when"
    if _LOG.search(t):
        return "log"
    if _MAKE_CHANGE.search(t):
        return "change"
    raw = _raw_tokens(text)
    if raw and (raw[0] in _QUESTION_START or raw[0] in _WH):
        return "ask"
    if _TOO.search(t) or any(a == "to" and b == "<n>" for a, b in pairwise(raw)):
        return "change"   # "it's too bright in here", "volume to 30": the setting is to change
    hits = []
    for i, w in enumerate(raw):
        if not _is_verbish(w) or (i and raw[i - 1] in _DETERMINERS):   # "the build", "my install": a thing
            continue
        if w in LOG_WORDS:
            if i == 0 or raw[i - 1] in _POLITE:   # "log my run", not "git log" or "the system log"
                hits.append((w, "log"))
        elif stem(w) in _VERB and (_VERB[stem(w)] != "open" or w in _OPEN_WORDS):   # "running" is not "run"
            hits.append((w, "log" if w in _ENTRY and "<n>" in raw else _VERB[stem(w)]))
    for _, cls in hits:
        if cls not in ("ask", "open"):
            return cls
    if any(c == "ask" for _, c in hits):
        return "ask"
    opens = [w for w, c in hits if c == "open"]
    if bare or (opens and (any(n.startswith(("app:", "panel:")) for n in named) or "<url>" in raw or "<path>" in raw)):
        return "open"
    if opens:
        # Without an app or a panel to open, "show me" only looks and "start" or "close" does something.
        return "ask" if all(w in _LOOK for w in opens) else "change"
    return "ask"


# -- named things --

def things_from_launcher(app_list: list | None = None) -> dict[str, tuple[str, ...]]:
    """What a word can name, from the launcher's own tables: each app by its name and title, each
    panel and utility by its plain word. {"app:passwords": ("passwords",), "panel:browser": ("browser",),
    "util:wifi": ("wifi",)}. Pass `app_list` to name the apps; with none, the ones on this machine."""
    app_list = launcher.known_apps() if app_list is None else app_list
    things: dict[str, tuple[str, ...]] = {}
    for a in app_list:
        phrases = {str(a.name).replace("-", " ").lower(), str(a.title).lower()}
        things[f"app:{a.name}"] = tuple(sorted(p for p in phrases if p))
    for panel, words in launcher.PANEL_WORDS.items():
        things[f"panel:{panel}"] = (words[0],)
    for util, words in launcher.UTILITY_COMMANDS.items():
        things[f"util:{util}"] = (words[0],)
    return things


def _fold(s: str) -> str:
    k = launcher._key(s)
    return k[:-1] if len(k) > 3 and k.endswith("s") and not k.endswith("ss") else k


def named_things(text: str, things: dict[str, Iterable[str]]) -> frozenset[str]:
    """The ids of the apps, panels and utilities this text names, however it is spaced or pluralised
    ("Memory Viewer", "memory-viewer", "memoryviewer"; "password" for Passwords)."""
    if not things:
        return frozenset()
    words = _raw_tokens(text)
    grams = {_fold("".join(words[i:i + n])) for n in (1, 2, 3) for i in range(len(words) - n + 1)}
    found = set()
    for tid, phrases in things.items():
        if any(_fold(p) and _fold(p) in grams for p in phrases):
            found.add(tid)
    return frozenset(found)


def _bare_open(text: str, thing_ids: Iterable[str], things: dict) -> bool:
    """Is all of this ask "open <the thing>": what a word for it would already say?"""
    ids = [t for t in thing_ids if t.startswith(("app:", "panel:"))]
    if len(ids) != 1 or "?" in text or any(w in _WH for w in _raw_tokens(text)):
        return False
    name = {w for p in things.get(ids[0], ()) for w in _raw_tokens(p)}
    name |= {stem(w) for w in name}
    rest = [w for w, s in _words(text) if w not in name and s not in name]
    rest = [w for w in rest if w not in _OPEN_WORDS and w not in _THING_WORDS and stem(w) not in name]
    return not rest


# -- one ask, as the grouping sees it --

@dataclass(frozen=True)
class Profile:
    id: str
    t: float
    day: str
    text: str
    tokens: tuple[str, ...]        # folded roots, each once
    surfaces: tuple[str, ...]      # the words as he said them, same order
    grams: frozenset[str]
    verb: str
    named: frozenset[str]
    route: frozenset[str]
    seconds: float = 0.0
    steps: int = 0
    changed: bool = False
    near_miss: bool = False
    style: bool = False
    open_target: str = ""          # the one thing this ask opened, when that is all it did
    word: str = ""                 # what he would say to a word for it


# How he wants answers to read: asked again and again, this is a preference, not a request. Said in
# different words, so it is told by kind (and the kind stands in for the route it does not have).
_STYLE_KINDS = (
    ("brevity", re.compile(
        r"\b(?:shorter|briefer|terser|concise|fewer words|less verbose|too long|too wordy|too verbose|simpler|"
        r"plainer|stop explaining|just the answer|one line|two lines|less chatty|be brief|"
        r"(?:keep|make) (?:it|answers?|replies|responses?|them) (?:short|brief)|"
        r"short (?:answers?|replies|responses?))\b", re.IGNORECASE)),
    ("detail", re.compile(r"\b(?:more detail|less detail|longer (?:answers?|replies|responses?)|elaborate)\b",
                         re.IGNORECASE)),
    ("format", re.compile(r"\b(?:bullets?|no markdown|plain text|more casual|less formal)\b", re.IGNORECASE)),
)


def style_kind(text: str) -> str:
    """"brevity", "detail" or "format" when the text is about how answers should read, else ''."""
    t = clean_text(text)
    return next((kind for kind, rx in _STYLE_KINDS if rx.search(t)), "")
_LEAD = re.compile(r"^(?:(?:hey|ok|okay|so|um|please|pls|could you|can you|would you|will you|i want to|"
                   r"i would like to|i'd like to|i need to|let me|let's|go ahead and)\s+)+")
_LEAD_VERB = re.compile(r"^(?:open|show|launch|start|run|bring up|pull up|go to|switch to|take me to|give me|"
                        r"show me|let me see)\s+(?:me\s+)?")
_TRAIL = re.compile(r"\s+(?:please|pls|now|for me|app|application|window|panel)$")


def word_phrase(text: str) -> str:
    """What he would say to a word for this ask: "show me my passwords" -> "my passwords". Empty
    when it is not short enough to be a word (more than four words)."""
    t = launcher.normalize(clean_text(text))
    for _ in range(3):
        t = _LEAD.sub("", t)
        t = _LEAD_VERB.sub("", t)
        t = _TRAIL.sub("", t)
    t = re.sub(r"^the ", "", t).strip(" .!?,;:")
    slotted = any(tok.startswith("<") for tok in _raw_tokens(text))   # a path, an address or a number is not a word
    return t if 0 < len(t.split()) <= 4 and not slotted else ""


def make_profile(req: Request, route_topics: Iterable[str] = (), things: dict | None = None,
                 app_list: list | None = None) -> "Profile":
    """Everything the grouping compares for one ask. `app_list` is the apps the launcher knows, for
    telling a near miss from a word it already answers (none: only the panels and commands)."""
    things = things or {}
    text = clean_text(req.text)
    pairs = _words(text)
    surfaces, tokens = [], []
    for w, s in pairs:
        if s not in tokens:
            tokens.append(s)
            surfaces.append(w)
    named = named_things(text, things)
    bare = _bare_open(text, named, things)
    verb = verb_class(text, named, bare)
    rt = frozenset(t for t in route_topics if t != routes.PRIVATE)
    look = style_kind(text) if not rt else ""
    if look:
        verb, rt = "change", frozenset((f"style:{look}",))
    near = bool(bare and verb == "open" and launcher.match(text, app_list or []) is None)
    opens = routes.opens_thing(rt) if rt and not look else (
        min((n for n in named if n.startswith(("app:", "panel:"))), default="") if bare and verb == "open" else "")
    return Profile(
        id=req.id, t=req.t, day=req.day or day_of(req.t), text=text, tokens=tuple(tokens), surfaces=tuple(surfaces),
        grams=trigrams(" ".join(sorted(tokens))), verb=verb, named=named, route=rt, seconds=req.seconds,
        steps=req.steps, changed=req.changed, near_miss=near, style=bool(look),
        open_target=opens, word=word_phrase(text))


def light_profile(tokens: Sequence[str], verb: str, named: Iterable[str], route_topics: Iterable[str]) -> Profile:
    """A profile made of what a signature kept: enough to ask "would this ask have joined it"."""
    return Profile(id="", t=0.0, day="", text="", tokens=tuple(tokens), surfaces=tuple(tokens),
                   grams=trigrams(" ".join(sorted(tokens))), verb=verb, named=frozenset(named),
                   route=frozenset(route_topics))


# The three kinds of ask that only look: "show me my passwords", "I need my passwords", "is the batch
# finished yet" and "how's the batch going" are the same request to the one who answers. The rest do
# something, and two asks that do different things are two requests.
LOOKING = frozenset(("open", "ask", "tell-me-when"))
# Asks to build or repair something: what they touched is where the work was, not which work.
AUTHORING = frozenset(("make", "fix"))


def same_kind(a: str, b: str) -> bool:
    return a == b or (a in LOOKING and b in LOOKING)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score(a: Profile, b: Profile) -> float:
    """How much two asks are one request, 0 to 1; 0 when they may not join. At JOIN or above
    they are the same request."""
    if a.named and b.named and a.named != b.named:
        return 0.0
    if not same_kind(a.verb, b.verb):
        return 0.0
    text = text_overlap(a.grams, b.grams)
    if text >= TEXT_ALONE:
        return text
    if a.verb in AUTHORING or b.verb in AUTHORING:
        return W_TEXT * text   # two changes to one app touch the same place: only the words tell them apart
    return W_TEXT * text + W_ROUTE * jaccard(a.route, b.route)


def joins(a: Profile, b: Profile) -> bool:
    return score(a, b) >= JOIN


# -- groups --

@dataclass
class Group:
    id: str                                   # "g" + the first member's id, so it survives new members
    label: str = ""                           # at most four of his most common words
    members: list[str] = field(default_factory=list)   # request ids, oldest first
    first: float = 0.0
    last: float = 0.0
    days: list[str] = field(default_factory=list)      # distinct local dates, oldest first
    n: int = 0
    weight: float = 0.0                       # n, each ask halved every 14 days (set by `weigh`)
    sentences: list[str] = field(default_factory=list)  # three of his own, newest first, distinct
    verb: str = ""                            # the commonest of the ten classes
    named: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)    # topics, commonest first
    seconds_total: float = 0.0
    steps: list[int] = field(default_factory=list)     # one per member
    near_miss: bool = False                   # every ask only looked, and ended opening the one same thing the launcher missed
    existing: str = ""                        # the launcher word that already does it, when an exact one does
    friction: int = 0                         # retries and undone turns that would have been this group
    # What the forms and offers read:
    times: list[float] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)
    verbs: dict[str, int] = field(default_factory=dict)
    changed: int = 0
    style: int = 0
    opens: str = ""                           # the one app or panel every ask only opened
    word: str = ""                            # what he would say to a word for it
    text_dropped: bool = False
    # What the store keeps about what became of it:
    state: str = "counting"                   # counting, offered, not_now, said_no, made, got_it
    form: str = ""                            # the letter of the form offered or made

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Group":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def weigh(self, now: float) -> "Group":
        self.weight = decayed(self.times, now)
        return self


def decayed(times: Iterable[float], now: float) -> float:
    """Each ask weighs 1 and halves every 14 days."""
    return sum(0.5 ** (max(now - t, 0.0) / (HALF_LIFE_DAYS * 86400)) for t in times)


def listed(group: Group, now: float) -> bool:
    """Is it still on the list: asked within the last 30 days."""
    return group.n > 0 and now - group.last <= LIST_DAYS * 86400


def apply_decay(group: Group, now: float) -> Group:
    """Age a group to `now`: its weight halves as it should; 30 days without an ask takes it off the
    list (`listed`), keeping its counts; after 90 days its words go and only the counts stay."""
    group.weigh(now)
    if now - group.last > TEXT_DAYS * 86400 and not group.text_dropped:
        group.label, group.word, group.sentences, group.text_dropped = "", "", [], True
    return group


class _Acc:
    """One group while it grows: its members' profiles, and what is worked out from them."""

    def __init__(self, gid: str):
        self.gid = gid
        self.members: list[Profile] = []
        self.friction: list[str] = []
        self.named: frozenset[str] = frozenset()

    def add(self, p: Profile) -> None:
        self.members.append(p)
        if p.named and not self.named:
            self.named = p.named

    def compare_with(self) -> list[Profile]:
        """The members a new ask is compared to: the first two and the latest eight."""
        m = self.members
        return m if len(m) <= 10 else m[:2] + m[-8:]

    def group(self) -> Group:
        ms = self.members
        stems: Counter = Counter()
        surface: dict[str, Counter] = {}
        first_seen: dict[str, int] = {}
        for p in ms:
            for s, w in zip(p.tokens, p.surfaces, strict=False):
                if s.startswith("<"):
                    continue
                stems[s] += 1
                surface.setdefault(s, Counter())[w] += 1
                first_seen.setdefault(s, len(first_seen))
        top = sorted(stems, key=lambda s: (-stems[s], first_seen[s]))[:4]
        top.sort(key=lambda s: first_seen[s])
        label = " ".join(surface[s].most_common(1)[0][0] for s in top) or (ms[-1].text[:30] if ms else "")
        recent: dict[str, tuple[float, str]] = {}
        for p in ms:
            recent[p.text.lower()] = (p.t, p.text)
        sentences = [txt for _, txt in sorted(recent.values(), key=lambda x: -x[0])[:3]]
        verbs = Counter(p.verb for p in ms)
        routes_: Counter = Counter(t for p in ms for t in p.route)
        days = sorted({p.day for p in ms})
        n = len(ms)
        targets = {p.open_target for p in ms}
        opens = targets.pop() if len(targets) == 1 and n else ""
        words = Counter(p.word for p in ms if p.word)
        return Group(
            id=self.gid, label=label, members=[p.id for p in ms], first=ms[0].t if ms else 0.0,
            last=ms[-1].t if ms else 0.0, days=days, n=n, sentences=sentences,
            verb=max(verbs, key=lambda v: (verbs[v], -list(verbs).index(v))) if verbs else "",
            named=sorted(self.named), routes=[t for t, _ in sorted(routes_.items(), key=lambda kv: (-kv[1], kv[0]))[:8]],
            seconds_total=round(sum(p.seconds for p in ms), 3), steps=[p.steps for p in ms],
            near_miss=bool(opens) and all(p.verb in LOOKING for p in ms), friction=len(self.friction),
            times=[p.t for p in ms], seconds=[p.seconds for p in ms], verbs=dict(verbs),
            changed=sum(1 for p in ms if p.changed), style=sum(1 for p in ms if p.style),
            opens=opens,
            word=words.most_common(1)[0][0] if words else "")


class Grouper:
    """Groups asks as they come, in the order they were asked. An ask joins the group holding the
    member it scores highest with (when that passes the join rule), or starts a group named for
    itself. A group's id never changes and a member never leaves it, so what was offered stays
    what it was offered as. Asks that only *retried* something are attached to the group they
    would have joined, as friction, and change nothing else."""

    def __init__(self):
        self.acc: dict[str, _Acc] = {}
        self._by_token: dict[str, set[str]] = {}
        self._by_topic: dict[str, set[str]] = {}
        self.assigned: dict[str, str] = {}
        self._order: dict[str, int] = {}

    def group_of(self, rid: str) -> str | None:
        return self.assigned.get(rid)

    def _index(self, gid: str, p: Profile) -> None:
        for t in p.tokens:
            self._by_token.setdefault(t, set()).add(gid)
        for t in p.route:
            self._by_topic.setdefault(t, set()).add(gid)

    def _candidates(self, p: Profile) -> set[str]:
        out: set[str] = set()
        for t in p.tokens:
            out |= self._by_token.get(t, set())
        for t in p.route:
            out |= self._by_topic.get(t, set())
        return out

    def best(self, p: Profile) -> str | None:
        """The group this ask would join, or None."""
        best, best_key = None, None
        for gid in self._candidates(p):
            acc = self.acc[gid]
            if p.named and acc.named and p.named != acc.named:
                continue
            top = max((score(p, m) for m in acc.compare_with()), default=0.0)
            if top >= JOIN:
                key = (top, len(acc.members), -self._order[gid])
                if best_key is None or key > best_key:
                    best, best_key = gid, key
        return best

    def add(self, p: Profile) -> str:
        """Place one counted ask; returns its group's id."""
        gid = self.best(p) or f"g{p.id}"
        return self.place(p, gid)

    def place(self, p: Profile, gid: str) -> str:
        acc = self.acc.get(gid)
        if acc is None:
            acc = self.acc[gid] = _Acc(gid)
            self._order[gid] = len(self._order)
        acc.add(p)
        self._index(gid, p)
        self.assigned[p.id] = gid
        return gid

    def attach_friction(self, p: Profile) -> str | None:
        gid = self.best(p)
        if gid is not None:
            self.place_friction(p, gid)
        return gid

    def place_friction(self, p: Profile, gid: str) -> None:
        acc = self.acc.get(gid)
        if acc is not None and p.id not in acc.friction:
            acc.friction.append(p.id)
            self.assigned[p.id] = gid

    def group(self, gid: str) -> Group | None:
        acc = self.acc.get(gid)
        return acc.group() if acc and acc.members else None

    def groups(self) -> list[Group]:
        return [g for gid in self.acc if (g := self.group(gid)) is not None]

    def remove(self, rid: str) -> str | None:
        """Take a member out (a turn found undone after it was counted); the group keeps its id."""
        gid = self.assigned.pop(rid, None)
        acc = self.acc.get(gid) if gid else None
        if acc is not None:
            acc.members = [m for m in acc.members if m.id != rid]
            acc.friction = [f for f in acc.friction if f != rid]
            acc.named = next((m.named for m in acc.members if m.named), frozenset())
        return gid


# -- what a group can be told apart by, for "Never" --

def signature(profiles: Iterable[Profile], keep: int = 5) -> list[list]:
    """What to remember of a group he said Never to: up to five members' folded words, verb, named
    things and route topics. Enough to tell a later ask "you would have joined this", and no
    sentence of his own."""
    out = []
    for p in list(profiles)[:keep]:
        out.append([list(p.tokens), p.verb, sorted(p.named), sorted(p.route)])
    return out


def from_signature(sig: Iterable) -> list[Profile]:
    profiles = []
    for item in sig or ():
        try:
            tokens, verb, named, route_ = item
            profiles.append(light_profile([str(t) for t in tokens], str(verb), [str(n) for n in named],
                                          [str(r) for r in route_]))
        except (TypeError, ValueError):
            continue
    return profiles


def never_blocks(p: Profile, never: Iterable[Profile]) -> bool:
    return any(joins(p, n) for n in never)


def existing_word(phrase: str, app_list: list | None = None) -> str:
    """The launcher word that already does what `phrase` says: the phrase itself, when the launcher
    answers it exactly, else ''."""
    if not phrase:
        return ""
    return phrase if launcher.match(phrase, app_list) is not None else ""


def sentence_of(text: str, limit: int = 80) -> str:
    t = " ".join(clean_text(text).split())
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"
