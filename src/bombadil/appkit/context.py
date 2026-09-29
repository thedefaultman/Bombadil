"""Which app is running, and where its files live."""

from dataclasses import dataclass
from pathlib import Path

from .. import apps, paths


@dataclass
class AppContext:
    name: str
    title: str
    dir: Path
    main: Path          # the QML file to load
    check: bool = False  # loaded by `check`: offscreen and read-only (nothing is written)

    @property
    def data_dir(self) -> Path:
        # A built-in app's folder is read-only; its state lives with the other apps' state.
        if apps.is_builtin(self.dir):
            return self.state_dir / self.name / "data"
        return self.dir / "data"

    @property
    def state_dir(self) -> Path:
        return paths.state_dir() / "apps"

    @property
    def log_path(self) -> Path:
        return self.state_dir / f"{self.name}.log"

    @property
    def status_path(self) -> Path:
        return self.state_dir / f"{self.name}.status.json"

    def resolve(self, path: str) -> Path:
        """`~` is home; a relative path lives in the app's data directory."""
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.data_dir / p


def for_app(name: str, check: bool = False) -> AppContext:
    app = apps.load(name)
    return AppContext(app.name, app.title, app.path, app.path / "main.qml", check)


def for_target(target: str, check: bool = False) -> AppContext:
    """An app name, an app directory, or a single .qml file (for kit galleries and tests)."""
    p = Path(target).expanduser()
    if p.suffix == ".qml" and p.is_file():
        return AppContext(p.stem.lower(), p.stem, p.parent.resolve(), p.resolve(), check)
    if p.is_dir() and (p / "main.qml").exists():
        meta = apps.read_meta(p)
        return AppContext(p.name, meta.get("title", p.name), p.resolve(), p.resolve() / "main.qml", check)
    return for_app(target, check)
