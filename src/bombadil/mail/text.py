"""Text handling for mail that other people wrote: turning HTML into plain text, quoting, file names,
and the test for paths that must never go out as an attachment.

Everything here treats its input as hostile. HTML is parsed, never fetched or run: nothing in a mail
is loaded, an image's address is not followed, and what a reader is shown is bounded. A link whose
words differ from where it goes says where it goes, because that is how a phishing mail hides.
Text hidden by the mail's own styles (the usual place to put instructions for a machine reader) is
left out, since a person looking at the mail would not see it either.
"""

import fnmatch
import os
import re
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from .. import paths

MAX_TEXT = 200_000        # characters of a mail's text that any caller is given
MAX_HTML = 4 << 20        # characters of HTML that are looked at
MAX_DEPTH = 200           # open elements remembered; deeper than this is not a mail
MAX_TARGET = 500          # characters of a link's address that are shown
NAME_BYTES = 120

_DROP = {"script", "style", "head", "template", "title"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
         "track", "wbr"}
_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "dl", "pre", "section", "article",
          "header", "footer", "address", "figure", "form", "center"}
_LINE = {"div", "tr", "li", "dt", "dd", "thead", "tbody", "tfoot", "caption", "nav", "main", "aside"}
_ZERO = re.compile(r"0+(?:\.0+)?(?:px|pt|em|rem|ex|%|vh|vw)?")
_FAR = re.compile(r"-\s*(\d+(?:\.\d+)?)\s*(?:px|pt|em|rem|ex|%|vh|vw)")
_TINY = re.compile(r"[0-2](?:\.\d+)?px")
_CLIP = re.compile(r"rect\([01](?:px)?,[01](?:px)?,[01](?:px)?,[01](?:px)?\)")


def _hidden_style(style: str) -> bool:
    """Does this inline style keep the element from being seen? The usual ways a mail hides words from a
    person (and so, in a phishing or injection attempt, for a machine reader): display, visibility, opacity,
    no-size boxes that clip, text pushed off the page, text of no size, clear text."""
    rules: dict[str, str] = {}
    for declaration in style.lower().split(";"):
        name, colon, value = declaration.partition(":")
        if colon:
            rules[name.strip()] = " ".join(value.replace("!important", " ").split())
    get = rules.get
    if get("display") == "none" or get("visibility") in ("hidden", "collapse") or get("mso-hide") == "all":
        return True
    if get("opacity") and _ZERO.fullmatch(get("opacity")) or get("color") == "transparent":
        return True
    size = get("font-size", "")
    if size and (_ZERO.fullmatch(size) or _TINY.fullmatch(size)):
        return True
    clipped = get("overflow") in ("hidden", "clip") or get("overflow-y") in ("hidden", "clip")
    for name in ("max-height", "max-width"):
        if name in rules and _ZERO.fullmatch(rules[name]):
            return True
    if clipped and any(name in rules and _ZERO.fullmatch(rules[name])
                       for name in ("height", "width", "line-height")):
        return True
    if "text-indent" in rules and _far(rules["text-indent"]):
        return True
    if get("position") in ("absolute", "fixed") and any(
            _far(rules.get(side, "")) for side in ("left", "top", "right", "bottom")):
        return True
    return (_CLIP.fullmatch(get("clip", "").replace(" ", "")) is not None
            or get("clip-path", "").replace(" ", "") in ("inset(100%)", "inset(50%)")
            or get("transform", "").replace(" ", "") in ("scale(0)", "scale(0,0)"))


def _far(value: str) -> bool:
    found = _FAR.fullmatch(value.strip())
    return found is not None and float(found.group(1)) >= 500


_SPACES = re.compile("[ \t\r\f\v\xa0\u2000-\u200a\u202f\u205f\u3000]+")
# Invisible padding that newsletters put after the subject line, and marks that reorder text on screen.
_INVISIBLE = re.compile("[\u00ad\u034f\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SCHEME = re.compile(r"^(?:https?://|mailto:)(?:www\.)?", re.IGNORECASE)


def _collapse(text: str) -> str:
    return _SPACES.sub(" ", _INVISIBLE.sub("", text)).strip()


def _same_target(words: str, href: str) -> bool:
    def norm(s: str) -> str:
        return _SCHEME.sub("", s.strip()).rstrip("/").lower()
    return norm(words) == norm(href)


class _Reader(HTMLParser):
    """Collects lines. `self.lines` holds (quote depth, text) and blank lines as (depth, "")."""

    def __init__(self, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.size = 0
        self.lines: list[tuple[int, str]] = []
        self.cur: list[str] = []
        self.depth = 0
        self.stack: list[str] = []
        self.hidden_at: int | None = None      # stack length when a hidden element opened
        self.dropping: str | None = None       # script, style or head: nothing inside is text
        self.pre = 0
        self.link: list | None = None          # [href, [text pieces]] while inside an <a>
        self.link_size = 0

    @property
    def full(self) -> bool:
        return self.size >= self.limit

    # -- output --

    def _flush(self) -> None:
        line = _collapse("".join(self.cur)) if not self.pre else "".join(self.cur).rstrip()
        self.cur = []
        if line:
            self.lines.append((self.depth, line))
            self.size += len(line) + 1

    def _break(self, blank: bool = False) -> None:
        if self.link is not None:
            self.link[1].append(" ")
            return
        self._flush()
        if blank and self.lines and self.lines[-1][1]:
            self.lines.append((self.depth, ""))

    def _put(self, text: str) -> None:
        if self.link is None:
            self.cur.append(text)
            return
        self.link[1].append(text)
        self.link_size += len(text)
        if self.link_size > 1000:
            # An <a> that was never closed would swallow the rest of the mail into one line.
            words = "".join(self.link[1])
            self.link = None
            self.cur.append(words)

    # -- the parser's events --

    def handle_starttag(self, tag, attrs):
        if self.full:
            return
        if self.dropping == "head" and tag == "body":
            self.dropping = None   # a head that was never closed ends where the body starts
        if self.dropping:
            return
        if tag in _DROP:
            self.dropping = tag
            return
        attrs = dict(attrs)
        if tag not in _VOID:
            if len(self.stack) >= MAX_DEPTH:
                self.size = self.limit   # nested this deep is not a mail, and hidden text could hide in it
                return
            self.stack.append(tag)
            if self.hidden_at is None and ("hidden" in attrs or _hidden_style(attrs.get("style") or "")):
                self.hidden_at = len(self.stack)
        if self.hidden_at is not None:
            return
        if tag == "br":
            self._break()
        elif tag == "hr":
            self._break()
            self.cur.append("---")
            self._break()
        elif tag == "blockquote":
            self._break(blank=True)
            self.depth += 1
        elif tag in _BLOCK:
            self._break(blank=True)
            if tag == "pre":
                self.pre += 1
        elif tag in _LINE:
            self._break()
            if tag == "li":
                self._put("- ")
        elif tag in ("td", "th"):
            self._put(" ")
        elif tag == "a":
            if self.link is not None:
                self._end_link()   # a new <a> closes one that was left open
            self.link = [attrs.get("href") or "", []]
            self.link_size = 0

    def handle_startendtag(self, tag, attrs):
        if tag in _DROP:
            return   # "<script/>" is an open tag to a browser, and there is nothing to skip to
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.dropping:
            if tag == self.dropping:
                self.dropping = None
            return
        if tag in _VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
            if self.hidden_at is not None and len(self.stack) < self.hidden_at:
                self.hidden_at = None
                return   # the hidden element's own end tag
        if self.hidden_at is not None:
            return
        if tag == "a" and self.link is not None:
            self._end_link()
        elif tag == "blockquote":
            self._break()
            self.depth = max(0, self.depth - 1)
            self._break(blank=True)
        elif tag in _BLOCK:
            self._break(blank=True)
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
        elif tag in _LINE:
            self._break()

    def handle_data(self, data):
        if self.dropping or self.hidden_at is not None or self.full:
            return
        if self.pre and self.link is None:
            *first, last = data.split("\n")
            for piece in first:
                self.cur.append(piece)
                self._flush()
            self.cur.append(last)
        else:
            self._put(data.replace("\n", " "))   # a line end in HTML source is a space, not a new line
            if len(self.cur) > 2000:
                self.cur = ["".join(self.cur)]   # keep the pieces of one long line from piling up

    def _end_link(self) -> None:
        href, pieces = self.link
        self.link = None
        words = _collapse("".join(pieces))
        target = href.strip()
        if not words:
            return
        shown = target.lower().startswith(("http://", "https://", "mailto:", "tel:"))
        if shown and not _same_target(words, target):
            cut = target if len(target) <= MAX_TARGET else target[:MAX_TARGET] + "…"
            self.cur.append(f"{words} ({cut})")
        else:
            self.cur.append(words)

    def result(self) -> str:
        if self.link is not None:
            self._end_link()
        self._flush()
        out: list[str] = []
        for depth, line in self.lines:
            if not line and (not out or not out[-1].strip("> ")):
                continue   # never two blank lines in a row, never one first
            out.append(("> " * depth + line) if line else ("> " * depth).rstrip())
        while out and not out[-1].strip("> "):
            out.pop()
        return "\n".join(out)


def html_to_text(html: str, limit: int = MAX_TEXT) -> str:
    """The text a person would read in this HTML, at most `limit` characters. Nothing is fetched."""
    reader = _Reader(limit)
    html = html[:MAX_HTML]
    try:
        for i in range(0, len(html), 1 << 16):
            reader.feed(html[i:i + (1 << 16)])
            if reader.full:
                break
        else:
            reader.close()
    except (AssertionError, RecursionError, ValueError):
        pass   # the parser gave up on markup it cannot make sense of: what it had is what there is
    return reader.result()[:limit]


def tidy(text: str) -> str:
    """Plain text from a mail made safe to show: one kind of line end, no control characters."""
    return _CONTROL.sub("", _INVISIBLE.sub("", text.replace("\r\n", "\n").replace("\r", "\n")))


def body_text(text: str | None, html: str | None, limit: int = MAX_TEXT) -> tuple[str, bool]:
    """What to show of a mail's body, and whether it was cut: its plain part when it has one,
    otherwise its HTML turned into text."""
    if text and text.strip():
        out = tidy(text)
    elif html:
        out = html_to_text(html, limit + 1)
    else:
        out = ""
    return out[:limit], len(out) > limit


def quote_reply(text: str, sender, when: float | None = None) -> str:
    """The quoted message a reply ends with: "On Tue, 30 Sep 2026 at 09:05, Priya <p@x.test> wrote:" and the
    message with each line behind "> ". `sender` is an Addr or a string."""
    lead = f"{sender} wrote:"
    if when:
        lead = f"On {datetime.fromtimestamp(when).astimezone().strftime('%a, %d %b %Y at %H:%M')}, {lead}"
    body = [(f"> {line}" if line.strip() else ">") for line in tidy(text).split("\n")]
    return "\n".join([lead, *body])


# -- file names --

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_UNSAFE = re.compile(r'[/\\<>:"|?*]')


def sanitize_filename(name: str, limit: int = NAME_BYTES) -> str:
    """A name that is safe to create in any folder: no separators, no control or direction-changing
    characters, no leading dot, no Windows-reserved name, at most `limit` bytes with its extension kept."""
    name = "".join(c for c in str(name) if unicodedata.category(c) not in ("Cc", "Zl", "Zp"))
    name = _INVISIBLE.sub("", name)
    name = _UNSAFE.sub("_", name).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext.encode()) > 16:
        stem, ext = name, ""
    if stem.split(".", 1)[0].strip().lower() in _RESERVED:
        stem = "_" + stem
    if stem.startswith("-"):
        stem = "_" + stem[1:]
    room = max(1, limit - (len(ext.encode()) + 1 if ext else 0))
    stem = stem.encode()[:room].decode(errors="ignore").rstrip(" .")
    if not stem:
        stem = "attachment"
    return f"{stem}.{ext}" if ext else stem


def unique_path(directory, name: str) -> Path:
    """`directory/name`, or `directory/name (1).ext` and on, for the first that does not exist (a dangling
    link counts as existing). Creating the file is the caller's, with O_EXCL: this only picks the name."""
    directory = Path(directory)
    name = sanitize_filename(name)
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    for n in range(10_000):
        candidate = directory / (name if n == 0 else f"{stem} ({n}){'.' + ext if ext else ''}")
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError(f"no free name for {name} in {directory}")


# -- paths that never go out --

_ABSOLUTE = ("/etc/shadow", "/etc/gshadow", "/etc/ssh", "/etc/sudoers", "/etc/sudoers.d", "/etc/ssl/private",
             "/proc", "/sys")
_NAMES = ("id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.pem", "*.key", "*.p12", "*.pfx", "*.ppk",
          "*.kdbx", "*.kdb", "*.jks", "*.keystore", "key3.db", "key4.db", "cert9.db", "logins.json",
          "cookies.sqlite", "login data", ".netrc", "_netrc", ".env", ".env.*", ".pgpass", ".git-credentials",
          ".npmrc", ".pypirc", ".htpasswd", "credentials*", "*password*", "*passwd*", "*passphrase*")


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def is_sensitive_path(path) -> bool:
    """Is this file one that holds keys, passwords, sign-ins or Bombadil's own state? Symlinks are
    followed first, so a harmless name pointing at ~/.ssh/id_rsa is caught; a path that cannot be
    resolved counts as sensitive. Anything in a dot-folder of the home directory is, which covers
    ~/.ssh, ~/.gnupg, ~/.aws, ~/.config/gcloud, ~/.mozilla, ~/.thunderbird, the browsers' profiles and
    Bombadil's own state and data; so is /etc/shadow, /etc/ssh and any file named like a key, a
    password store or a credentials file."""
    try:
        target = Path(os.path.realpath(os.fspath(path), strict=False))
        home = Path(os.path.realpath(paths.home()))
    except (OSError, RuntimeError, ValueError, TypeError):
        return True
    if any(_under(target, Path(a)) for a in _ABSOLUTE):
        return True
    # Bombadil's own places, wherever the environment put them (under home they are dot-folders anyway).
    for own in (paths.state_dir(), paths.data_dir(), paths.config_dir(), paths.runtime_dir(),
                paths.mail_files(), paths.mail_profile()):
        if _under(target, Path(os.path.realpath(own))):
            return True
    if home in target.parents and target.relative_to(home).parts[0].startswith("."):
        # ~/.ssh ~/.gnupg ~/.aws ~/.config ~/.mozilla ~/.thunderbird ~/.local and the browsers' profiles
        # are all dot-folders in home; a dotfile there is a secret more often than not.
        return True
    name = target.name.lower()
    if name.endswith(".pub"):
        return False
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in _NAMES)


# -- addresses in free text --

_ADDRESS = re.compile(r"[\w.%+'-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}")


def addresses_in(text: str) -> list[str]:
    """The email addresses written in some words, lower-cased, each once, in the order they appear."""
    seen: dict[str, None] = {}
    for m in _ADDRESS.finditer(str(text)[:1 << 20]):
        seen.setdefault(m.group(0).lower().strip(".'"), None)
    return list(seen)
