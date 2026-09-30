"""`Highlighter`: syntax colors for a TextEdit/TextArea's document, in the kit's dark palette.

Each line is split into spans that swallow everything inside them (strings, comments) and
the gaps between, where keywords, numbers and names are colored. Constructs that span lines
(block comments, triple-quoted strings, code fences) carry over through the block state.
"""

import re

from PySide6.QtCore import Property, QObject, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtQuick import QQuickTextDocument

from ..context import AppContext
from . import MAJOR, MINOR, URI

# Matches share/qml/Bombadil/Theme.qml: accent keywords, calm greens, ambers and blues.
PALETTE = {
    "keyword": ("#d97757", False, False),
    "string": ("#9fcf8a", False, False),
    "number": ("#d9b44a", False, False),
    "constant": ("#d9b44a", False, False),
    "comment": ("#5c636b", False, True),
    "type": ("#6aa5e8", False, False),
    "function": ("#e3c08d", False, False),
    "property": ("#8fbfe0", False, False),
    "decorator": ("#b793d6", False, False),
    "variable": ("#8fbfe0", False, False),
    "heading": ("#d97757", True, False),
    "bold": ("#e6e8eb", True, False),
    "italic": ("#e6e8eb", False, True),
    "code": ("#9fcf8a", False, False),
    "link": ("#6aa5e8", False, False),
    "quote": ("#8b939c", False, True),
    "punct": ("#8b939c", False, False),
}

NUMBER = r"\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b"
DQ = r'"(?:[^"\\]|\\.)*"?'
SQ = r"'(?:[^'\\]|\\.)*'?"
BT = r"`(?:[^`\\]|\\.)*`?"


def words(*ws: str) -> str:
    return r"\b(?:" + "|".join(ws) + r")\b"


JS_KEYWORDS = words("async", "await", "break", "case", "catch", "class", "const", "continue", "debugger",
                    "default", "delete", "do", "else", "export", "extends", "finally", "for", "function",
                    "if", "import", "in", "instanceof", "let", "new", "of", "return", "static", "super",
                    "switch", "this", "throw", "try", "typeof", "var", "void", "while", "with", "yield")
QML_KEYWORDS = words("property", "readonly", "required", "signal", "alias", "default", "component",
                     "pragma", "as", "on", "enum")
CONSTANTS = words("true", "false", "null", "undefined", "NaN", "Infinity")

# Per language: spans (name, start regex, end regex or None for single-line) and word rules.
# A span with an end regex may run over several lines.
LANGUAGES = {
    "python": {
        "spans": [("comment", r"#.*", None),
                  ("string", r"[rRbBuUfF]{0,2}'''", r"'''"),
                  ("string", r'[rRbBuUfF]{0,2}"""', r'"""'),
                  ("string", r"[rRbBuUfF]{0,2}" + DQ, None),
                  ("string", r"[rRbBuUfF]{0,2}" + SQ, None)],
        "words": [("decorator", r"^\s*@[\w.]+"),
                  ("keyword", words("and", "as", "assert", "async", "await", "break", "class", "continue",
                                    "def", "del", "elif", "else", "except", "finally", "for", "from",
                                    "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or",
                                    "pass", "raise", "return", "try", "while", "with", "yield", "match",
                                    "case")),
                  ("constant", words("True", "False", "None", "self", "cls")),
                  ("type", words("int", "float", "str", "bytes", "bool", "list", "dict", "set", "tuple",
                                 "object", "type") + r"|\b[A-Z][A-Za-z0-9_]*\b"),
                  ("function", r"(?<=\bdef )\w+|\b\w+(?=\()"),
                  ("number", NUMBER)],
    },
    "javascript": {
        "spans": [("comment", r"//.*", None), ("comment", r"/\*", r"\*/"),
                  ("string", DQ, None), ("string", SQ, None), ("string", r"`", r"`")],
        "words": [("keyword", JS_KEYWORDS), ("constant", CONSTANTS), ("type", r"\b[A-Z][A-Za-z0-9_]*\b"),
                  ("function", r"\b[a-zA-Z_$][\w$]*(?=\s*\()"), ("number", NUMBER)],
    },
    "qml": {
        "spans": [("comment", r"//.*", None), ("comment", r"/\*", r"\*/"),
                  ("string", DQ, None), ("string", SQ, None), ("string", r"`", r"`")],
        "words": [("keyword", JS_KEYWORDS + "|" + QML_KEYWORDS), ("constant", CONSTANTS),
                  ("property", r"^\s*[a-z_][\w.]*(?=\s*:(?!:))|\bid(?=\s*:)"),
                  ("type", r"\b[A-Z][A-Za-z0-9_]*\b"), ("function", r"\b[a-z_$][\w$]*(?=\s*\()"),
                  ("number", NUMBER)],
    },
    "json": {
        "spans": [("property", DQ + r"(?=\s*:)", None), ("string", DQ, None), ("comment", r"//.*", None)],
        "words": [("constant", CONSTANTS), ("number", r"-?" + NUMBER)],
    },
    "shell": {
        "spans": [("comment", r"(?:^|(?<=\s))#.*", None), ("string", DQ, None), ("string", SQ, None),
                  ("string", BT, None)],
        "words": [("keyword", words("if", "then", "else", "elif", "fi", "for", "while", "until", "do",
                                    "done", "case", "esac", "in", "function", "return", "local", "export",
                                    "select", "time", "readonly", "declare", "set", "unset", "source",
                                    "exit", "shift", "trap")),
                  ("variable", r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@#?$!*-]"),
                  ("function", r"^\s*[A-Za-z_][\w-]*(?=\s*\(\))"),
                  ("punct", r"(?<=\s)--?[A-Za-z][\w-]*"), ("number", NUMBER)],
    },
    "toml": {
        "spans": [("comment", r"#.*", None), ("string", r'"""', r'"""'), ("string", r"'''", r"'''"),
                  ("string", DQ, None), ("string", SQ, None)],
        "words": [("heading", r"^\s*\[\[?[^\]]*\]\]?"), ("property", r"^\s*[\w.\-]+(?=\s*=)"),
                  ("constant", words("true", "false", "inf", "nan")),
                  ("number", r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:\d{2})?)?\b|[+-]?" + NUMBER)],
    },
    "ini": {
        "spans": [("comment", r"^\s*[;#].*", None), ("string", DQ, None)],
        "words": [("heading", r"^\s*\[[^\]]*\]"), ("property", r"^\s*[^=\s;#][^=]*?(?=\s*=)"),
                  ("constant", words("true", "false", "yes", "no", "on", "off")), ("number", NUMBER)],
    },
    "markdown": {
        "spans": [("code", r"^\s*(?:```|~~~).*", r"^\s*(?:```|~~~)\s*$"), ("code", r"`[^`]+`", None)],
        "words": [("heading", r"^#{1,6}\s.*$|^(?:=+|-{3,})\s*$"), ("quote", r"^\s*>.*$"),
                  ("keyword", r"^\s*(?:[-*+]|\d+[.)])(?=\s)"),
                  ("bold", r"\*\*[^*\n]+\*\*|__[^_\n]+__"),
                  ("italic", r"(?<![*\w])\*[^*\s][^*\n]*\*(?!\*)|(?<![_\w])_[^_\s][^_\n]*_(?![_\w])"),
                  ("link", r"!?\[[^\]\n]*\]\([^)\n]*\)|<https?://[^>\s]+>|\bhttps?://\S+")],
    },
}
# `language` also takes a file name or extension ("notes.md", ".md", "md").
EXTENSIONS = {".py": "python", ".md": "markdown", ".markdown": "markdown", ".json": "json", ".qml": "qml",
              ".js": "javascript", ".mjs": "javascript", ".ts": "javascript", ".sh": "shell", ".bash": "shell",
              ".zsh": "shell", ".toml": "toml", ".ini": "ini", ".conf": "ini", ".cfg": "ini",
              ".desktop": "ini", ".service": "ini"}


def compile_language(spec: dict):
    spans = [(name, re.compile(start), re.compile(end) if end else None) for name, start, end in spec["spans"]]
    combined = re.compile("|".join(f"(?P<s{i}>{start.pattern})" for i, (_, start, _) in enumerate(spans))
                          ) if spans else None
    rules = [(name, re.compile(pattern, re.M)) for name, pattern in spec["words"]]
    return spans, combined, rules


_compiled: dict[str, tuple] = {}


def compiled(language: str):
    if language not in _compiled:
        _compiled[language] = compile_language(LANGUAGES[language])
    return _compiled[language]


def char_format(name: str) -> QTextCharFormat:
    color, bold, italic = PALETTE[name]
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if bold:
        fmt.setFontWeight(QFont.Weight.DemiBold)
    if italic:
        fmt.setFontItalic(True)
    return fmt


class _Syntax(QSyntaxHighlighter):
    def __init__(self, parent: QObject):
        super().__init__(parent)
        self.language = "plain"
        self.formats = {name: char_format(name) for name in PALETTE}

    def highlightBlock(self, text: str):
        if self.language not in LANGUAGES:
            return
        spans, combined, rules = compiled(self.language)
        # QTextDocument counts UTF-16 units; Python counts code points.
        wide = any(ord(c) > 0xFFFF for c in text)
        offsets = None
        if wide:
            offsets = [0]
            for c in text:
                offsets.append(offsets[-1] + (2 if ord(c) > 0xFFFF else 1))

        def paint(start: int, end: int, name: str):
            if end > start:
                s, e = (offsets[start], offsets[end]) if wide else (start, end)
                self.setFormat(s, e - s, self.formats[name])

        pos = 0
        self.setCurrentBlockState(-1)
        state = self.previousBlockState()
        if state >= 0:  # inside a span that began on an earlier line
            name, _, end = spans[state]
            m = end.search(text)
            if m is None:
                paint(0, len(text), name)
                self.setCurrentBlockState(state)
                return
            paint(0, m.end(), name)
            pos = m.end()

        while pos <= len(text) and combined is not None:
            m = combined.search(text, pos)
            gap_end = m.start() if m else len(text)
            self._words(text, pos, gap_end, rules, paint)
            if m is None:
                break
            index = int(m.lastgroup[1:])
            name, _, end = spans[index]
            if end is None:
                paint(m.start(), m.end(), name)
                pos = m.end() if m.end() > m.start() else m.end() + 1
                continue
            close = end.search(text, m.end())
            if close is None:
                paint(m.start(), len(text), name)
                self.setCurrentBlockState(index)
                return
            paint(m.start(), close.end(), name)
            pos = close.end()
        if combined is None:
            self._words(text, 0, len(text), rules, paint)

    @staticmethod
    def _words(text: str, start: int, end: int, rules, paint):
        if end <= start:
            return
        taken = [False] * (end - start)
        for name, rx in rules:
            for m in rx.finditer(text, start, end):
                a, b = m.start(), m.end()
                if b <= a or any(taken[a - start:b - start]):
                    continue
                for i in range(a - start, b - start):
                    taken[i] = True
                paint(a, b, name)


class Highlighter(QObject):
    textDocumentChanged = Signal()
    languageChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = None
        self._language = "plain"
        self._syntax = _Syntax(self)

    def _set_document(self, doc):
        if doc is self._doc:
            return
        self._doc = doc
        text_doc = doc.textDocument() if isinstance(doc, QQuickTextDocument) else None
        self._syntax.setDocument(text_doc)
        self.textDocumentChanged.emit()

    def _set_language(self, language: str):
        language = (language or "plain").strip().lower()
        if language not in LANGUAGES:
            ext = language[language.rfind("."):] if "." in language else "." + language
            language = EXTENSIONS.get(ext, "plain")
        if language != self._language:
            self._language = language
            self._syntax.language = language
            self._syntax.rehighlight()
            self.languageChanged.emit()

    textDocument = Property(QObject, lambda self: self._doc, _set_document, notify=textDocumentChanged)
    language = Property(str, lambda self: self._language, _set_language, notify=languageChanged)


def register(ctx: AppContext) -> None:
    from PySide6.QtQml import qmlRegisterType

    qmlRegisterType(Highlighter, URI, MAJOR, MINOR, "Highlighter")
