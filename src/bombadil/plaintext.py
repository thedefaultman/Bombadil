"""Characters that draw as nothing, and the one place that says which they are.

Somebody else's words must not carry anything the person cannot see (a name that reads as another name, a
sentence spelled in characters that draw as nothing), and neither may the words that would be sent when the person
presses Send: what they read in the box has to be all of what goes. So the same list guards both ways:
`strip` takes the hidden characters out of words that come in (a name, a message, a title) and out of a draft, and
`has_hidden` is the last look before a press, in a driver that will not post what `strip` would have changed.

The list is the Unicode default-ignorable code points and the fillers that draw blank, not a hand list: it
includes the "tag" characters and the variation selectors, which can spell a whole message that no one sees.
The exceptions are the two that emoji are spelled with, kept only where an emoji needs them: the variation
selector after a symbol (a heart drawn as a picture) and the joiner between two symbols (a family, a technologist).
What is left of that is a bit or two for each emoji in a row of them, which is visible, and no more.
"""

import re
import unicodedata

# Soft hyphen, combining grapheme joiner, Arabic letter mark, Hangul fillers, Khmer vowel inherents, the Mongolian
# variation selectors and vowel separator, the zero-width and direction marks, the invisible operators and deprecated
# formatting characters, the braille blank, the Hangul filler, the variation selectors, the byte order mark, the
# halfwidth filler, the interlinear annotation marks, the musical formatting characters, and the tag block with the
# ideographic variation selectors, the shorthand and hieroglyph format controls, and the unassigned code points beside
# the annotation marks that are default-ignorable too.
_RANGES = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
           (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x2800, 0x2800),
           (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFFB),
           (0x1BCA0, 0x1BCA3), (0x13430, 0x1343F), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
HIDDEN = re.compile("[" + "".join(re.escape(chr(a)) + (("-" + re.escape(chr(b))) if b != a else "")
                                  for a, b in _RANGES) + "]")

_JOINER, _TEXT_STYLE, _PICTURE_STYLE, _KEYCAP = chr(0x200D), chr(0xFE0E), chr(0xFE0F), chr(0x20E3)
_KEYCAP_BASES = "0123456789#*"


def _symbol(ch: str) -> bool:
    """Whether an emoji can begin or end with this: a symbol, or one of the two punctuation marks that are pictures."""
    return len(ch) == 1 and (unicodedata.category(ch) in ("So", "Sk") or ch in (chr(0x203C), chr(0x2049)))


def _spelled(s: str, i: int) -> bool:
    """Whether the hidden character at s[i] is one an emoji is spelled with, from the characters around it."""
    ch = s[i]
    before = s[i - 1] if i else ""
    after = s[i + 1] if i + 1 < len(s) else ""
    if ch in (_TEXT_STYLE, _PICTURE_STYLE):
        return _symbol(before) or (len(before) == 1 and before in _KEYCAP_BASES and after == _KEYCAP)
    if ch == _JOINER:
        if before == _PICTURE_STYLE and i >= 2:    # a heart and then a flame: symbol, selector, joiner, symbol
            before = s[i - 2]
        return _symbol(before) and _symbol(after)
    return False


def strip(value) -> str:
    """The text without the characters that draw as nothing, except where an emoji needs them."""
    s = str(value if value is not None else "")
    if not HIDDEN.search(s):
        return s
    return "".join(ch for i, ch in enumerate(s) if not (HIDDEN.match(ch) and not _spelled(s, i)))


def has_hidden(value) -> bool:
    """Whether `strip` would change the text: it holds a character that draws as nothing and is not part of an emoji."""
    s = str(value if value is not None else "")
    return bool(HIDDEN.search(s)) and strip(s) != s
