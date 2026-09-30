"""What Bombadil calls you and how it talks: ~/.config/bombadil/persona.toml.

Three keys: name (optional), voice (merry, plain or quiet) and greet (true unless the user said to
stop greeting them). The file is read defensively, per field, so a broken one can never stop agentd
or a turn: it falls back to the defaults. It is its own file because config.save_user rewrites all of
config.toml, and because memory.md is loaded by every coding session, where a voice would leak into
code reviews. The name reaches the system prompt and a TOML file, so it is checked, never escaped.
"""

import json
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import paths

VOICES = ("merry", "plain", "quiet")
DEFAULT_VOICE = "merry"
MAX_NAME = 24
MAX_WORDS = 3
MAX_MARKS = 3   # combining marks on one letter: Vietnamese and Arabic use two or three, more is a glitch

# Letters and marks that draw nothing: the Hangul and Khitan fillers, the grapheme joiner, the Khmer
# inherent vowels, and every variation selector. They pass isalpha() or category M, so they are named.
_BLANK = re.compile("[\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u3164\ufe00-\ufe0f\uffa0"
                    "\U00016fe4\U000e0100-\U000e01ef]")

# The name clause, then what each voice asks of the model's own closing sentence.
_CALLED = "The user goes by {name}; use it only when greeting or asking."
_STYLE = {
    "merry": ("Be brisk and friendly. At most one reply in four may end with a short aside from this "
              "turn's facts, never after an error or system change."),
    "plain": "Keep replies plain and short: one or two sentences, no asides, no sign-off.",
    "quiet": "Say as little as will do: one sentence, no aside, no question at the end.",
}


@dataclass(frozen=True)
class Persona:
    name: str = ""
    voice: str = DEFAULT_VOICE
    greet: bool = True


def clean_name(s) -> str:
    """The name as it would be stored, or "" when it is not a valid one: one to three words of at most
    24 characters in all, made of letters, combining marks, hyphens, apostrophes and dots, and starting
    with a letter. Only plain spaces separate words, so no control character or line break gets through.
    What would draw nothing or a glitch is no name either: a blank letter, a mark with no letter under it,
    or more than three marks on one letter."""
    if not isinstance(s, str):
        return ""
    name = re.sub(" +", " ", s.strip(" "))
    if not name or len(name) > MAX_NAME or name.count(" ") >= MAX_WORDS or not name[0].isalpha():
        return ""
    if _BLANK.search(name):
        return ""
    marks, based = 0, False   # the marks run so far, and whether a letter is under them
    for ch in name:
        if unicodedata.category(ch).startswith("M"):
            marks += 1
            if not based or marks > MAX_MARKS:
                return ""
        elif ch.isalpha():
            marks, based = 0, True
        elif ch in " -'’.":
            marks, based = 0, False
        else:
            return ""
    return name


def valid_name(s) -> bool:
    return clean_name(s) != ""


def load() -> Persona:
    """The saved persona. Never raises: each field that is missing or wrong takes its default."""
    try:
        data = tomllib.loads(paths.persona_file().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return Persona()
    voice, greet = data.get("voice"), data.get("greet")
    voice = voice.strip().lower() if isinstance(voice, str) else None   # the agent may write "Plain"
    return Persona(clean_name(data.get("name")), voice if voice in VOICES else DEFAULT_VOICE,
                   greet if isinstance(greet, bool) else True)


def exists() -> bool:
    try:
        return paths.persona_file().is_file()
    except OSError:
        return False


def save(name: str = "", voice: str = DEFAULT_VOICE, greet: bool = True) -> Persona:
    """Write persona.toml atomically and return what was stored. ValueError when a field is not valid."""
    cleaned = clean_name(name) if name else ""
    if name and not cleaned:
        raise ValueError(f"not a name: {name!r}")
    if voice not in VOICES:
        raise ValueError(f"not a voice: {voice!r}")
    if greet is not True and greet is not False:
        raise ValueError(f"greet must be true or false, not {greet!r}")
    # A link (a dotfiles manager keeps the real file) stays a link: write to what it points at, with the
    # temp file next to that, because replacing the link itself would leave the real copy stale.
    path = Path(os.path.realpath(paths.persona_file()))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    text = (f"name = {json.dumps(cleaned, ensure_ascii=False)}\n"
            f"voice = {json.dumps(voice)}\n"
            f"greet = {'true' if greet else 'false'}\n")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return Persona(cleaned, voice, greet)


def ensure_defaults() -> bool:
    """Write the defaults when there is no file yet, so any way out of the setup card leaves a valid
    setting. True when it wrote."""
    if exists():
        return False
    save()
    return True


def sentence(p: Persona) -> str:
    """The one sentence of the system prompt that sets the voice. The name clause drops without a name."""
    name = clean_name(p.name)
    style = _STYLE.get(p.voice, _STYLE[DEFAULT_VOICE])
    return f"{_CALLED.format(name=name)} {style}" if name else style


def note(p: Persona | None = None) -> str:
    """What goes into the system prompt: the voice sentence (none before persona.toml exists) and always
    where and how the agent changes it."""
    where = (f"The user's name and voice are kept in {paths.persona_file()}, a TOML file with the keys "
             "name (letters, hyphens, apostrophes and dots only, starting with a letter, one to three "
             "words, at most 24 characters; if it does not fit, say so and leave the file alone), "
             'voice = "merry" | "plain" | "quiet" in lower case, and greet = true | false. '
             "Edit it when the user asks to be called something else, to be less chatty (plain), to be "
             "quieter (quiet) or to stop the greetings (greet = false). "
             "Keep it valid TOML, and say what you changed in one short sentence.")
    if p is None:
        p = load() if exists() else None
    return f"{sentence(p)} {where}" if p is not None else where


def templates() -> list[dict]:
    """The rows of the setup card: {"id", "name", "card", "reply"}. A card string holds {n}, which the card
    fills with ", Name" or nothing; the reply is the closing sentence of a sample answer in that voice."""
    from . import greet  # greet needs this module, so the import waits for the call
    table = greet.lines()
    names, cards, replies = table.get("voices", {}), table.get("card", {}), table.get("reply", {})
    return [{"id": v, "name": names.get(v, v.capitalize()), "card": cards.get(v, ""),
             "reply": replies.get(v, "")} for v in VOICES]
