"""Generated native apps.

An app is a directory under ~/Apps/<name>/ with:
  main.qml   the UI (Qt Quick), hot reloaded while it is edited
  app.py     optional Python behind it; a `Backend` class is exposed to QML as `backend`
  app.toml   name, description, icon

`bombadil-app run <name>` opens it as a real Wayland window; no build step, no ports.
"""

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import paths

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


@dataclass
class App:
    name: str
    path: Path
    title: str
    description: str = ""


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    if not NAME_RE.match(s):
        raise ValueError(f"cannot make an app name from {title!r}")
    return s


def app_dir(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError(f"bad app name {name!r}")
    return paths.apps_dir() / name


def create(title: str, qml: str, python: str | None = None, description: str = "") -> App:
    """Write the app files. The agent calls this (via os-mcp) with QML it wrote."""
    name = slug(title)
    d = app_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.qml").write_text(qml)
    if python is not None:
        (d / "app.py").write_text(python)
    # json.dumps gives a valid TOML basic string for any title (quotes, newlines).
    (d / "app.toml").write_text(f"title = {json.dumps(title)}\ndescription = {json.dumps(description)}\n")
    _write_desktop_entry(name, title, description)
    return App(name, d, title, description)


def _write_desktop_entry(name: str, title: str, description: str) -> None:
    apps = Path(os.environ.get("XDG_DATA_HOME", paths.home() / ".local/share")) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / f"bombadil-app-{name}.desktop").write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={title}\nComment={description}\nExec=bombadil-app run {name}\n"
        "Categories=Bombadil;\n"
    )


def load(name: str) -> App:
    d = app_dir(name)
    if not (d / "main.qml").exists():
        raise FileNotFoundError(f"no app named {name!r} in {paths.apps_dir()}")
    meta = tomllib.loads((d / "app.toml").read_text()) if (d / "app.toml").exists() else {}
    return App(name, d, meta.get("title", name), meta.get("description", ""))


def list_apps() -> list[App]:
    root = paths.apps_dir()
    if not root.exists():
        return []
    return [load(p.name) for p in sorted(root.iterdir()) if (p / "main.qml").exists()]


def log_path(name: str) -> Path:
    return paths.state_dir() / "apps" / f"{name}.log"


def run(name: str, detach: bool = True) -> subprocess.Popen:
    app = load(name)
    runner = shutil.which("bombadil-app") or str(Path(__file__).resolve().parents[2] / "bin" / "bombadil-app")
    cmd = [runner, "run", app.name]
    if detach:
        # QML errors land here, so the agent can read why a window did not appear.
        log = log_path(app.name)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as out:
            return subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT)
    return subprocess.Popen(cmd)
