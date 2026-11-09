#!/usr/bin/env python3
"""Check Bombadil's documentation: relative links and anchors, Mermaid diagrams, and leaks.

    python3 docs/tools/check_docs.py [--mermaid] [--private-terms FILE] [--soft-terms FILE] [PATH ...]

PATHs are files or directories under the repo root (default: docs, README.md, CONTRIBUTING.md).
--mermaid renders every diagram with Mermaid's CLI (`mmdc`; set MMDC to its path and MMDC_CONFIG to a
puppeteer config file if the default browser needs flags such as --no-sandbox).
Exit status is 1 when a link or anchor is broken, a diagram does not parse, or a hard leak is found.

Hard leaks (always checked): e-mail addresses, session/thread/message ids, claude.ai working URLs,
the shared project folder's path, Windows user paths, and token shapes. Names of people or machines
are not in this file: give them with --private-terms (one per line, case-insensitive, whole words,
a hit is a failure) and --soft-terms (a hit is listed for a human to judge, never a failure).
"""
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

HARD = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")),
    ("session id", re.compile(r"\b(?:cse|cmsg|chan|user)_[0-9A-Za-z]{12,}\b|\bsession_(?=[0-9A-Za-z]*\d)(?=[0-9A-Za-z]*[A-Za-z])[0-9A-Za-z]{16,}\b")),
    ("session url", re.compile(r"claude\.ai/(?:code|chat|project|artifact)/[A-Za-z0-9_-]+")),
    ("shared folder path", re.compile(r"/mnt/(?:project-files|wslg|c|user-data)\b")),
    ("user home path", re.compile(r"[A-Za-z]:\\Users\\|(?-i:/Users/(?!user/)[A-Za-z][\w.-]*/)|/home/(?!user\b)[a-z][a-z0-9_-]*/", re.I)),
    ("token", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abp]-[0-9A-Za-z-]{10,}")),
]
# GitHub no-reply addresses, the Claude commit trailer address and the example addresses in docs
# (example.com, acme.*, and the reserved .example, .test and .invalid names) are not leaks.
EMAIL_OK = re.compile(
    r"^noreply@anthropic\.com$"
    r"|@(?:[a-z0-9-]+\.)*(?:example\.(?:com|org|net)|acme\.[a-z]+|users\.noreply\.github\.com|example|test|invalid)$",
    re.I,
)

TEXT_SUFFIXES = {".md", ".html", ".htm", ".txt", ".json", ".svg", ".toml", ".qml", ".py", ".sh", ".log", ".patch"}


def slug(heading: str) -> str:
    h = re.sub(r"`([^`]*)`", r"\1", heading)
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = h.replace("*", "").replace("~", "")
    h = h.strip().lower()
    h = re.sub(r"[^\w\- ]", "", h, flags=re.UNICODE)
    return h.replace(" ", "-")


def md_lines(text: str):
    """Yield (line number, line, in_code): in_code is true for fence lines and the lines between them."""
    fence = None  # (character, length) of the open fence
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                yield n, line, True
                continue
            yield n, line, False
        else:
            yield n, line, True
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and re.match(r"^\s*[`~]+\s*$", line):
                fence = None


def md_anchors(text: str) -> set[str]:
    out: set[str] = set()
    seen: dict[str, int] = {}
    for _, line, code in md_lines(text):
        if code:
            continue
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            s = slug(m.group(2))
            n = seen.get(s, 0)
            seen[s] = n + 1
            out.add(s if n == 0 else f"{s}-{n}")
        for a in re.findall(r'<a\s+(?:name|id)="([^"]+)"', line):
            out.add(a)
    return out


def srcset_urls(value: str) -> list[str]:
    return [part.split()[0] for part in value.split(",") if part.split()]


class HtmlRefs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for k in ("href", "src", "poster"):
            if d.get(k):
                self.refs.append(d[k])
        if d.get("srcset"):
            self.refs.extend(srcset_urls(d["srcset"]))
        if d.get("id"):
            self.ids.add(d["id"])
        if d.get("name") and tag == "a":
            self.ids.add(d["name"])


# A link may wrap over several lines of a paragraph; its text stops at a blank line.
LINK_RX = re.compile(r"!?\[(?:[^\]\n]|\n(?!\s*\n))*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
TAG_RX = re.compile(r"<(?:a|img|source|link|video|audio|iframe|script)\s[^>]*>", re.I)
ATTR_RX = re.compile(r"\b(href|src|poster|srcset)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)


def md_links(text: str) -> list[tuple[int, str]]:
    """Targets of the links and images in Markdown prose (not in fences or code spans), with line numbers."""
    out: list[tuple[int, str]] = []
    prose = []
    for _, line, code in md_lines(text):
        prose.append("" if code else re.sub(r"`[^`]*`", "", line))
    flat = "\n".join(prose)

    def line_of(pos: int) -> int:
        return flat.count("\n", 0, pos) + 1

    for m in LINK_RX.finditer(flat):
        out.append((line_of(m.start()), m.group(1)))
    for t in TAG_RX.finditer(flat):
        for a in ATTR_RX.finditer(t.group(0)):
            value = a.group(2) if a.group(2) is not None else a.group(3)
            for ref in srcset_urls(value) if a.group(1).lower() == "srcset" else [value]:
                out.append((line_of(t.start()), ref))
    for n, line in enumerate(prose, 1):
        m = re.match(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", line)
        if m:
            out.append((n, m.group(1)))
    return out


def mermaid_blocks(text: str) -> list[tuple[int, str]]:
    blocks, cur, start, fence = [], [], 0, None
    for n, line in enumerate(text.splitlines(), 1):
        if fence is None:
            m = re.match(r"^\s*(`{3,}|~{3,})\s*mermaid\s*$", line)
            if m:
                fence, cur, start = (m.group(1)[0], len(m.group(1))), [], n
            else:
                m = re.match(r"^\s*(`{3,}|~{3,})", line)
                if m:  # some other fenced block: skip to its end
                    fence, cur, start = (m.group(1)[0], len(m.group(1))), None, n
        else:
            m = re.match(r"^\s*(`+|~+)\s*$", line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                if cur is not None:
                    blocks.append((start, "\n".join(cur)))
                fence = None
            elif cur is not None:
                cur.append(line)
    if fence is not None and cur is not None:
        blocks.append((start, "\n".join(cur) + "\n%% UNCLOSED FENCE"))
    return blocks


def load_terms(path: str | None) -> list[re.Pattern]:
    if not path or not os.path.exists(path):
        return []
    pats = []
    for line in Path(path).read_text().splitlines():
        t = line.strip()
        if t and not t.startswith("#"):
            pats.append(re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", re.I))
    return pats


def iter_files(root: Path, targets: list[str]):
    for t in targets:
        p = root / t
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts:
                    yield f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--mermaid", action="store_true", help="render every mermaid block with mmdc ($MMDC, $MMDC_CONFIG)")
    ap.add_argument("--private-terms")
    ap.add_argument("--soft-terms")
    ap.add_argument("--no-links", action="store_true")
    ap.add_argument("--no-leaks", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    targets = a.paths or ["docs", "README.md", "CONTRIBUTING.md"]
    hard_terms, soft_terms = load_terms(a.private_terms), load_terms(a.soft_terms)
    files = [f for f in iter_files(root, targets)]
    errors: list[str] = []
    notes: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}

    def anchors_of(p: Path) -> set[str]:
        if p not in anchor_cache:
            try:
                txt = p.read_text(errors="replace")
            except OSError:
                txt = ""
            if p.suffix == ".md":
                anchor_cache[p] = md_anchors(txt)
            elif p.suffix in (".html", ".htm"):
                hp = HtmlRefs()
                hp.feed(txt)
                anchor_cache[p] = hp.ids
            else:
                anchor_cache[p] = set()
        return anchor_cache[p]

    def check_ref(src: Path, line: int, ref: str):
        ref = html.unescape(ref.strip())
        if not ref:
            return
        u = urlparse(ref)
        if u.scheme in ("http", "https", "mailto", "data", "tel", "javascript"):
            return
        if u.scheme:
            return
        path = unquote(u.path)
        frag = unquote(u.fragment)
        if path == "":
            target = src
        elif path.startswith("/"):
            target = root / path.lstrip("/")
        else:
            target = (src.parent / path)
        target = Path(os.path.normpath(target))
        rel = src.relative_to(root)
        if not target.exists():
            errors.append(f"{rel}:{line}: broken link -> {ref}")
            return
        try:
            target.relative_to(root)
        except ValueError:
            errors.append(f"{rel}:{line}: link leaves the repository -> {ref}")
            return
        if frag and target.suffix in (".md", ".html", ".htm") and not frag.startswith(":~:"):
            if frag not in anchors_of(target) and frag.lower() not in {x.lower() for x in anchors_of(target)}:
                errors.append(f"{rel}:{line}: missing anchor #{frag} in {target.relative_to(root)}")

    mmd_jobs: list[tuple[Path, int, str]] = []
    for f in files:
        rel = f.relative_to(root)
        suffix = f.suffix.lower()
        is_text = suffix in TEXT_SUFFIXES or f.name in ("LICENSE",)
        if not is_text:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if not a.no_links:
            if suffix == ".md":
                for n, r in md_links(text):
                    check_ref(f, n, r)
            elif suffix in (".html", ".htm"):
                hp = HtmlRefs()
                hp.feed(text)
                for r in hp.refs:
                    if r.startswith("#"):
                        if r[1:] and r[1:] not in hp.ids:
                            errors.append(f"{rel}: missing in-page anchor {r}")
                        continue
                    check_ref(f, 0, r)
        if suffix == ".md" and a.mermaid:
            for n, b in mermaid_blocks(text):
                mmd_jobs.append((f, n, b))
        if not a.no_leaks:
            for n, line in enumerate(text.splitlines(), 1):
                for name, rx in HARD:
                    for m in rx.finditer(line):
                        if name == "email" and EMAIL_OK.search(m.group(0)):
                            continue
                        errors.append(f"{rel}:{n}: LEAK ({name}): {m.group(0)[:60]}")
                for rx in hard_terms:
                    m = rx.search(line)
                    if m:
                        errors.append(f"{rel}:{n}: LEAK (private term {m.group(0)!r}): {line.strip()[:110]}")
                for rx in soft_terms:
                    m = rx.search(line)
                    if m:
                        notes.append(f"{rel}:{n}: review ({m.group(0)!r}): {line.strip()[:110]}")

    if a.mermaid and mmd_jobs:
        mmdc = os.environ.get("MMDC", "mmdc")
        cfg = os.environ.get("MMDC_CONFIG")
        with tempfile.TemporaryDirectory() as td:
            for i, (f, n, b) in enumerate(mmd_jobs):
                src = Path(td) / f"d{i}.mmd"
                src.write_text(b + "\n")
                cmd = [mmdc, "-q", "-i", str(src), "-o", str(Path(td) / f"d{i}.svg")]
                if cfg:
                    cmd[1:1] = ["-p", cfg]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if r.returncode != 0:
                    msg = (r.stderr or r.stdout).strip().splitlines()
                    short = next((l for l in msg if "error" in l.lower() or "Parse" in l), msg[0] if msg else "failed")
                    errors.append(f"{f.relative_to(root)}:{n}: mermaid does not render: {short[:200]}")
        notes.append(f"mermaid: {len(mmd_jobs)} diagram(s) rendered")

    for e in errors:
        print("ERROR", e)
    for n in notes:
        print("NOTE ", n)
    print(f"checked {len(files)} files: {len(errors)} error(s), {len([n for n in notes if 'review' in n])} item(s) to judge")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
