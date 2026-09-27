"""The app tools os-mcp gives the agent: learn the kit, write an app, see it, manage it.

No Qt here: a check runs `bombadil-app check` in a subprocess, so the MCP server stays
small and an app that crashes Qt can never take the server down with it.
"""

import base64
import json
import subprocess
import time
from pathlib import Path

from .. import apps, paths
from . import placement

CHECK_TIMEOUT = 40      # seconds for one `bombadil-app check`
OPEN_WAIT = 3.0         # seconds open_app waits for a freshly started app to report its load


class Blocks(list):
    """MCP content blocks (text and image); mcp_server passes them through as they are."""
    is_mcp_content = True


def _share() -> Path:
    local = Path(__file__).resolve().parents[3] / "share"
    return local if local.exists() else paths.share_dir() / "share"


def skill_dir() -> Path | None:
    for d in (_share() / "skills" / "bombadil-apps", paths.share_dir() / "skills" / "bombadil-apps"):
        if d.is_dir():
            return d
    return None


def guide_topics() -> tuple[list[str], list[str]]:
    """(reference topics, example app names) that app_guide can return."""
    d = skill_dir()
    if d is None:
        return [], []
    refs = sorted(p.stem for p in (d / "reference").glob("*.md"))
    examples = sorted(p.name for p in (d / "examples").iterdir() if p.is_dir()) if (d / "examples").is_dir() else []
    return refs, examples


def _topics_line() -> str:
    refs, examples = guide_topics()
    parts = [", ".join(f"'{t}'" for t in refs)] if refs else []
    if examples:
        parts.append("or an example app's full source: " + ", ".join(f"'{e}'" for e in examples))
    return ", ".join(parts)


def guide(topic: str | None = None) -> str:
    d = skill_dir()
    if d is None:
        return "The bombadil-apps skill is not installed (share/skills/bombadil-apps is missing)."
    refs, examples = guide_topics()
    footer = f"\n\n---\nMore: app_guide(topic) with topic = {_topics_line()}." if refs or examples else ""
    if not topic:
        skill = d / "SKILL.md"
        if not skill.exists():
            return f"SKILL.md is missing from {d}.{footer}"
        return skill.read_text() + footer
    topic = topic.strip().lower().removesuffix(".md")
    if topic in refs:
        return (d / "reference" / f"{topic}.md").read_text() + footer
    if topic in examples:
        root = d / "examples" / topic
        out = [f"# Example app `{topic}` ({root})"]
        for f in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in {".qml", ".js", ".py", ".toml", ".md"}):
            lang = {".qml": "qml", ".js": "js", ".py": "python", ".toml": "toml"}.get(f.suffix, "")
            out.append(f"## {f.relative_to(root)}\n```{lang}\n{f.read_text().rstrip()}\n```")
        return "\n\n".join(out) + footer
    raise ValueError(f"no guide topic {topic!r}; topics: {_topics_line() or 'none'}")


def run_check(name: str) -> dict:
    """`bombadil-app check <name> --screenshot ...` in a subprocess; its JSON result."""
    png = paths.state_dir() / "apps" / f"{name}.check.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.unlink(missing_ok=True)
    cmd = [*apps.runner(), "check", name, "--screenshot", str(png)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CHECK_TIMEOUT,
                           stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "loaded": False, "errors": [
            f"the check did not finish in {CHECK_TIMEOUT} s: an endless loop in a binding, "
            "Component.onCompleted or app.py?"]}
    try:
        result = json.loads(r.stdout)
    except ValueError:
        tail = [line for line in r.stderr.strip().splitlines() if line.strip()][-8:]
        return {"ok": False, "loaded": False,
                "errors": [f"bombadil-app check crashed (exit {r.returncode})", *tail]}
    return result if isinstance(result, dict) else {"ok": False, "errors": ["check printed no result"]}


def report(name: str, result: dict, **extra) -> Blocks:
    """The check result as the agent sees it: a text summary, then the screenshot."""
    ok = bool(result.get("ok"))
    summary = {
        "app": name,
        "path": str(apps.app_dir(name)),
        "ok": ok,
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
        "console": result.get("console", []),
        "size": result.get("size"),
        **extra,
    }
    if ok:
        head = (f"{name}: check passed. The screenshot below is what the user sees: look at it like a "
                "designer (clipped or overlapping text, empty areas, alignment, placeholder data) and fix what is off.")
    else:
        head = (f"{name}: check FAILED. The files are written; a running app keeps its last good version "
                "with a red banner (or shows the error list if it never loaded). Fix each error at its "
                "file:line and call create_app again with the complete files.")
    blocks = Blocks([{"type": "text", "text": head + "\n" + json.dumps(summary, indent=1)}])
    shot = result.get("screenshot")
    if shot and Path(shot).is_file():
        blocks.append({"type": "image", "data": base64.b64encode(Path(shot).read_bytes()).decode(),
                       "mimeType": "image/png"})
    return blocks


def _load_result(name: str, since: float, wait: float) -> dict:
    """Wait for a just-started app to write status.json, then say how its load went."""
    from . import runtime

    status_file = paths.state_dir() / "apps" / f"{name}.status.json"
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if status_file.exists() and status_file.stat().st_mtime >= since:
            break
        time.sleep(0.1)
    st = runtime.status(name)
    if "ok" not in st or not status_file.exists() or status_file.stat().st_mtime < since:
        return {"loaded": "not yet", "log": st.get("log", [])[-10:]}
    return {k: st.get(k) for k in ("ok", "errors", "warnings")}


def register(os_tools) -> None:
    """Add the app tools to an mcp_server.OsTools (replacing first-milestone versions)."""
    for old in ("create_app", "open_app", "list_apps", "app_template"):
        os_tools.tools.pop(old, None)   # so the app tools are listed together, guide first
    t = os_tools._tool

    def h():
        return os_tools.hypr

    name_arg = {"name": {"type": "string", "description": "the app's name, as list_apps shows it (e.g. password-manager)"}}
    topics = _topics_line()

    @t("app_guide",
       "How to build native apps for the user on Bombadil. Call this before writing or changing any app "
       "(once per conversation): it returns the loop, a skeleton and the rules that make an app load on the "
       "first try. " + (f"topic returns more: {topics}." if topics else ""),
       {"topic": {"type": "string", "description": "optional; omit for the main guide"}})
    def app_guide(a):
        return guide(a.get("topic"))

    @t("create_app",
       "Create or update a native app from QML with the Bombadil kit (`import Bombadil`, root `AppWindow`). "
       "Call app_guide first. Writes ~/Apps/<name>/ (name = title in kebab-case), loads it offscreen and "
       "returns ok, errors with file:line, warnings, console.log output and a screenshot; opens the app "
       "(it slides in), or hot reloads it in place if it is running (its saved state is kept). "
       "To change an app, call again with the same title and the complete new files. "
       "ok false: fix the errors and call again. ok true: look at the screenshot and fix what looks wrong.",
       {"title": {"type": "string", "description": "window title; also names the app (\"Password Manager\" -> password-manager)"},
        "qml": {"type": "string", "description": "the complete main.qml; its root object is AppWindow"},
        "files": {"type": "object", "additionalProperties": {"type": "string"},
                  "description": "extra files by relative path, e.g. {\"EntryRow.qml\": \"...\"} (then usable as "
                                 "EntryRow {} in main.qml) or {\"util.js\": \"...\"}; .qml .js .mjs .json .txt .svg "
                                 "only, never under data/"},
        "python": {"type": "string",
                   "description": "optional app.py with a Backend(QObject) class, exposed to QML as `backend`; "
                                  "re-imported on every change. Prefer the native bindings (app_guide('native'))"},
        "description": {"type": "string", "description": "one line for launchers"},
        "icon": {"type": "string", "description": "kit icon name, e.g. \"key\" or \"activity\""},
        "open": {"type": "boolean", "default": True,
                 "description": "slide the app in (default); false only writes and checks it"}},
       ["title", "qml"])
    def create_app(a):
        app = apps.create(a["title"], a["qml"], a.get("python"), a.get("description", ""),
                          a.get("files"), a.get("icon", ""))
        running = placement.is_running(app.name)
        extra: dict = {"reloaded": running}
        if a.get("open", True):
            # Before the check: the user sees the app (or its hot reload) while the check runs.
            try:
                extra["window"] = placement.show(app.name, h())
            except (OSError, RuntimeError) as e:   # the check result matters more
                extra["window"] = f"could not show it: {e}"
        return report(app.name, run_check(app.name), **extra)

    @t("check_app",
       "Check an app again: loads it offscreen and returns ok, errors (file:line), warnings, console output "
       "and a fresh screenshot, like create_app. Use after its files changed by other means, or to see it.",
       name_arg, ["name"])
    def check_app(a):
        app = apps.load(a["name"])
        return report(app.name, run_check(app.name))

    @t("open_app", "Open an app: start it (it slides in), or slide it in if it is running. Says whether it loaded.",
       name_arg, ["name"])
    def open_app(a):
        app = apps.load(a["name"])
        if placement.is_running(app.name):
            return placement.show(app.name, h())
        since = time.time() - 1
        return {"app": app.name, "window": placement.show(app.name, h()), **_load_result(app.name, since, OPEN_WAIT)}

    @t("show_app", "Slide an app's window in (starts the app if it is not running).", name_arg, ["name"])
    def show_app(a):
        return placement.show(apps.load(a["name"]).name, h())

    @t("hide_app", "Slide an app's window out. It keeps running and stays in the bar.", name_arg, ["name"])
    def hide_app(a):
        return placement.hide(apps.load(a["name"]).name, h())

    @t("close_app", "Quit an app; it saves its state first and leaves the bar.", name_arg, ["name"])
    def close_app(a):
        return placement.close(apps.load(a["name"]).name)

    @t("app_status",
       "A running app's last load result (ok, errors with file:line, warnings, console output, reloads) and "
       "the last 40 lines of its log. Use when the user says an app is broken, blank or stuck.",
       name_arg, ["name"])
    def app_status(a):
        from . import runtime
        return runtime.status(apps.load(a["name"]).name)

    @t("list_apps", "List the user's apps (~/Apps): name, title, path, running (has a window) and shown (on screen).", {})
    def list_apps(_a):
        running, shown = placement.running(), placement.shown(h())
        return [{"name": x.name, "title": x.title, "path": str(x.path),
                 "running": x.name in running, "shown": x.name in shown} for x in apps.list_apps()]

    @t("app_template", "The starter main.qml and app.py. app_guide() is the full guide; start there.", {})
    def app_template(_a):
        tpl = _share() / "app-template"
        return {"main.qml": (tpl / "main.qml").read_text(), "app.py": (tpl / "app.py").read_text()}
