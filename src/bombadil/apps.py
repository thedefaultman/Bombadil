"""Generated native apps.

An app is a directory under ~/Apps/<name>/ with:
  main.qml   the UI (Qt Quick), hot reloaded while it is edited
  *.qml/.js  optional extra components and libraries next to it
  app.py     optional Python behind it; a `Backend` class is exposed to QML as `backend`
  app.toml   title, description, icon
  data/      the app's saved state; create() never writes there

`bombadil-app run <name>` opens it as a real Wayland window; no build step, no ports.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import paths

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
# Extra files create() accepts next to main.qml: text the QML can import or read.
EXTRA_SUFFIXES = {".qml", ".js", ".mjs", ".json", ".txt", ".svg"}


@dataclass
class App:
    name: str
    path: Path
    title: str
    description: str = ""
    icon: str = ""


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    if not NAME_RE.match(s):
        raise ValueError(f"cannot make an app name from {title!r}")
    return s


def app_dir(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError(f"bad app name {name!r}")
    return paths.apps_dir() / name


def _toml_str(s: str) -> str:
    """A TOML basic string. JSON escapes are valid TOML except for surrogate pairs and DEL."""
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def _extra_path(rel: str) -> PurePosixPath:
    """Validate one `files` key: a relative path inside the app, never under data/."""
    p = PurePosixPath(rel.replace("\\", "/"))
    if not rel or p.is_absolute() or any(part in ("", ".", "..") for part in rel.replace("\\", "/").split("/")):
        raise ValueError(f"files: {rel!r} must be a plain relative path like 'EntryRow.qml' or 'lib/util.js'")
    if p.parts[0] == "data":
        raise ValueError(f"files: {rel!r} is under data/, which holds the app's saved state and is never written")
    if any(part.startswith(".") for part in p.parts):
        raise ValueError(f"files: {rel!r}: hidden files are not allowed")
    if p.suffix not in EXTRA_SUFFIXES:
        raise ValueError(f"files: {rel!r}: only {', '.join(sorted(EXTRA_SUFFIXES))} files can be added")
    if str(p) == "main.qml":
        raise ValueError("files: pass main.qml as `qml`, not in `files`")
    return p


def _write(path: Path, text: str) -> None:
    """Write via a hidden temp file and rename, so the hot reloader never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def create(title: str, qml: str, python: str | None = None, description: str = "",
           files: dict[str, str] | None = None, icon: str = "") -> App:
    """Write the app files. The agent calls this (via os-mcp) with QML it wrote."""
    name = slug(title)
    d = app_dir(name)
    extra = {_extra_path(rel): text for rel, text in (files or {}).items()}
    for rel, text in extra.items():
        if not isinstance(text, str):
            raise ValueError(f"files: {rel} must be text")
    d.mkdir(parents=True, exist_ok=True)
    # Components first and main.qml last, so a reload sees a complete set.
    for rel, text in extra.items():
        _write(d / rel, text)
    if python is not None:
        _write(d / "app.py", python)
    _write(d / "app.toml", f"title = {_toml_str(title)}\ndescription = {_toml_str(description)}\n"
                           f"icon = {_toml_str(icon)}\n")
    _write(d / "main.qml", qml)
    _write_desktop_entry(name, title, description, icon)
    return App(name, d, title, description, icon)


def _write_desktop_entry(name: str, title: str, description: str, icon: str = "") -> None:
    apps = Path(os.environ.get("XDG_DATA_HOME", paths.home() / ".local/share")) / "applications"
    apps.mkdir(parents=True, exist_ok=True)

    def one_line(s: str) -> str:
        # Desktop entry values: one line, no control characters, backslashes escaped.
        return "".join(ch for ch in " ".join(s.split()) if ch.isprintable()).replace("\\", "\\\\")

    svg = Path(__file__).resolve().parents[2] / "share" / "qml" / "Bombadil" / "icons" / f"{icon}.svg"
    (apps / f"bombadil-app-{name}.desktop").write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={one_line(title)}\nComment={one_line(description)}\nExec=bombadil-app run {name}\n"
        + (f"Icon={svg}\n" if icon and svg.exists() else "")
        + "Categories=Bombadil;\n"
    )


def read_meta(d: Path) -> dict:
    """app.toml as a dict; {} when missing or unreadable (a half-written file mid-edit)."""
    try:
        return tomllib.loads((d / "app.toml").read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def load(name: str) -> App:
    d = app_dir(name)
    if not (d / "main.qml").exists():
        raise FileNotFoundError(f"no app named {name!r} in {paths.apps_dir()}")
    meta = read_meta(d)
    return App(name, d, str(meta.get("title", name)), str(meta.get("description", "")), str(meta.get("icon", "")))


def list_apps() -> list[App]:
    root = paths.apps_dir()
    if not root.exists():
        return []
    return [load(p.name) for p in sorted(root.iterdir())
            if NAME_RE.match(p.name) and (p / "main.qml").exists()]


def log_path(name: str) -> Path:
    return paths.state_dir() / "apps" / f"{name}.log"


def runner() -> list[str]:
    """How to invoke bombadil-app: this checkout's own copy first, so dev trees test their code."""
    local = Path(__file__).resolve().parents[2] / "bin" / "bombadil-app"
    if local.exists():
        return [sys.executable, str(local)]
    return [shutil.which("bombadil-app") or "bombadil-app"]


def run(name: str, detach: bool = True) -> subprocess.Popen:
    app = load(name)
    cmd = [*runner(), "run", app.name]
    if detach:
        # QML errors land here, so the agent can read why a window did not appear.
        log = log_path(app.name)
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > 1_000_000:
            log.replace(log.with_suffix(".log.1"))
        with log.open("ab") as out:
            return subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT)
    return subprocess.Popen(cmd)
