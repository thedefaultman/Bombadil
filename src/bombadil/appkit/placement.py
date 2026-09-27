"""Slide app windows in and out: each app lives in its own Hyprland special workspace.

`prepare()` adds a runtime window rule before the app's window first maps, so it opens
floating, centered, at its own size (shrunk by `fit()` when the screen is too small), straight
into `special:app-<name>` (and therefore slides in). After that, showing and hiding is showing and hiding that special workspace.
Everything here returns a short sentence and is a no-op without Hyprland (dev machines,
tests). No Qt: os-mcp and the bar's `bombadil-app close` use this too.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time

from .. import apps, hypr

NO_HYPRLAND = "Hyprland is not running"
MARGIN = 24           # logical px kept free around a window that had to be shrunk to fit
MIN_SIZE = (240, 160)
_RUN_RE = re.compile(r"bombadil-app run ([a-z0-9][a-z0-9-]*)$")


def _check(name: str) -> str:
    if not apps.NAME_RE.match(name):
        raise ValueError(f"bad app name {name!r}")
    return name


def lua_str(s: str) -> str:
    """A double-quoted Lua string literal for any text."""
    out = []
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def workspace(name: str) -> str:
    return f"special:app-{_check(name)}"


def rule_lua(name: str, w: int, h: int) -> str:
    """The runtime window rule. Named per app, so calling it again updates the same rule."""
    cls = f"^(bombadil-app-{_check(name)})$"
    return (f"hl.window_rule({{ name = {lua_str('bombadil-app-' + name)}, match = {{ class = {lua_str(cls)} }}, "
            f"float = true, center = true, size = {{ {int(w)}, {int(h)} }}, workspace = {lua_str(workspace(name))} }})")


def show_lua(name: str) -> str:
    # focus() shows a special workspace without toggling it (a no-op when already shown).
    return f"hl.dsp.focus({{ workspace = {lua_str(workspace(name))} }})"


def toggle_lua(name: str) -> str:
    return f"hl.dsp.workspace.toggle_special({lua_str('app-' + _check(name))})"


def _hypr(h: hypr.Hyprland | None) -> hypr.Hyprland:
    return h if h is not None else hypr.Hyprland()


def _send(h: hypr.Hyprland, command: str, tries: int = 3) -> str:
    """One IPC request; a busy compositor sometimes drops one, so retry briefly."""
    err: Exception | None = None
    for attempt in range(tries):
        try:
            reply = h.request(command).strip()
        except (OSError, RuntimeError) as e:
            err = e
        else:
            if reply and "timed out" not in reply:
                return reply
            err = RuntimeError(reply or "no reply")
        time.sleep(0.1 * (attempt + 1))
    raise RuntimeError(f"Hyprland did not take {command[:60]!r}: {err}")


def _dispatch(h: hypr.Hyprland, expr: str) -> str:
    # With a Lua config, a raw `dispatch X` request runs `return hl.dispatch(X)`.
    reply = _send(h, f"dispatch {expr}")
    if reply != "ok":
        raise RuntimeError(f"hl.dispatch({expr}): {reply}")
    return reply


def _monitors(h: hypr.Hyprland) -> list[dict]:
    try:
        return json.loads(_send(h, "j/monitors"))
    except (RuntimeError, ValueError):
        return []


def running() -> dict[str, list[int]]:
    """Running apps: name -> pids of their `bombadil-app run <name>` processes (not this one)."""
    try:
        r = subprocess.run(["pgrep", "-a", "-f", r"bombadil-app run [a-z0-9-]+$"],
                           capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    found: dict[str, list[int]] = {}
    for line in r.stdout.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        m = _RUN_RE.search(cmd)
        if m and pid.isdigit() and int(pid) != os.getpid():
            found.setdefault(m.group(1), []).append(int(pid))
    return found


def is_running(name: str) -> bool:
    return _check(name) in running()


def shown(h: hypr.Hyprland | None = None) -> set[str]:
    """Apps whose drawer is on screen (on any monitor)."""
    h = _hypr(h)
    if not h.available:
        return set()
    names = {m.get("specialWorkspace", {}).get("name", "") for m in _monitors(h)}
    return {n.removeprefix("special:app-") for n in names if n.startswith("special:app-")}


def usable_area(h: hypr.Hyprland | None = None) -> tuple[int, int] | None:
    """The focused monitor's room for a window in logical px: without the bar, less a margin."""
    h = _hypr(h)
    if not h.available:
        return None
    mons = [m for m in _monitors(h) if isinstance(m, dict)]
    m = next((m for m in mons if m.get("focused")), mons[0] if mons else None)
    if m is None:
        return None
    try:
        scale = float(m.get("scale") or 1)
        w, h_ = float(m["width"]) / scale, float(m["height"]) / scale
        if int(m.get("transform") or 0) % 2:   # rotated a quarter turn
            w, h_ = h_, w
        left, top, right, bottom = ([float(x) for x in m.get("reserved") or []] + [0.0] * 4)[:4]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return int(w - left - right - 2 * MARGIN), int(h_ - top - bottom - 2 * MARGIN)


def fit(w: int, h_: int, room: tuple[int, int] | None) -> tuple[int, int]:
    """w x h, shrunk to `room` (from usable_area) where it is bigger; as it is without one."""
    if room is None:
        return int(w), int(h_)
    return min(int(w), max(room[0], MIN_SIZE[0])), min(int(h_), max(room[1], MIN_SIZE[1]))


def prepare(name: str, w: int, h_: int, h: hypr.Hyprland | None = None) -> str:
    """Before the window first maps: open it floating, centered, w x h, in its drawer.
    Never raises: the app opens as a plain window when Hyprland does not answer."""
    h = _hypr(h)
    if not h.available:
        return f"{NO_HYPRLAND}; {name} opens as a plain window"
    try:
        reply = _send(h, "eval " + rule_lua(name, w, h_))
    except (OSError, RuntimeError) as e:
        return f"could not add the window rule for {name}, it opens as a plain window: {e}"
    if reply != "ok":
        return f"could not add the window rule for {name}: {reply}"
    return f"{name} opens in its drawer at {int(w)}x{int(h_)}"


def show(name: str, h: hypr.Hyprland | None = None, start: bool = True) -> str:
    """Slide the app in, starting it first if it is not running (unless start=False:
    the app itself asking, which is running by definition)."""
    _check(name)
    if start and not is_running(name):
        apps.run(name)
        return f"started {name}; it slides in when its window opens"
    h = _hypr(h)
    if not h.available:
        return f"{NO_HYPRLAND}; {name} is running"
    _dispatch(h, show_lua(name))
    return f"{name} shown"


def hide(name: str, h: hypr.Hyprland | None = None) -> str:
    """Slide the app out if it is on screen. It keeps running."""
    h = _hypr(h)
    ws = workspace(name)
    if not h.available:
        return f"{NO_HYPRLAND}; nothing to hide"
    on = [m for m in _monitors(h) if m.get("specialWorkspace", {}).get("name") == ws]
    if not on:
        return f"{name} is not on screen"
    # toggle_special acts on the focused monitor; elsewhere it would move the drawer here.
    if not on[0].get("focused", True):
        _dispatch(h, f"hl.dsp.focus({{ monitor = {lua_str(on[0].get('name', ''))} }})")
    _dispatch(h, toggle_lua(name))
    return f"{name} hidden"


def toggle(name: str, h: hypr.Hyprland | None = None, start: bool = True) -> str:
    h = _hypr(h)
    if _check(name) in shown(h):
        return hide(name, h)
    return show(name, h, start)


def _signal_all(pids: list[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _gone(name: str, wait: float) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if name not in running():
            return True
        time.sleep(0.1)
    return name not in running()


def close(name: str, wait: float = 3.0) -> str:
    """Quit the app. SIGTERM lets it save its state first; an app that does not quit in
    `wait` seconds (stuck in a loop, so its handler never runs) is killed."""
    pids = running().get(_check(name), [])
    if not pids:
        return f"{name} is not running"
    _signal_all(pids, signal.SIGTERM)
    if _gone(name, wait):
        return f"{name} closed"
    _signal_all(running().get(name, []), signal.SIGKILL)
    if _gone(name, 2.0):
        return f"{name} did not quit within {wait:g} s (stuck?) and was killed; unsaved changes are lost"
    return f"{name} did not quit within {wait:g} s and could not be killed"


def open_url(url: str, h: hypr.Hyprland | None = None) -> str:
    """Open a link in the browser panel (Chromium), and slide the panel in."""
    url = url.strip()
    if not re.match(r"^(https?|file)://", url, re.IGNORECASE):
        # Anything else with a scheme (javascript:, chrome:) or that Chromium would read as a flag.
        if not url or url.startswith("-") or re.match(r"^[a-z][a-z0-9+.-]*:(?!\d)", url, re.IGNORECASE):
            raise ValueError(f"not a web address: {url!r}")
        url = "https://" + url
    h = _hypr(h)
    if not h.available:
        return f"{NO_HYPRLAND}; not opening {url}"
    cmd = hypr.PANELS["browser"]
    if shutil.which(cmd[0]) is None:
        return f"{cmd[0]} is not installed; not opening {url}"
    try:
        has_window = any(c.get("workspace", {}).get("name") == "special:browser" for c in h.clients())
    except (OSError, RuntimeError, ValueError):
        has_window = False
    # A running Chromium hands the URL to its window as a new tab; otherwise this starts it.
    subprocess.Popen([*cmd, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    if has_window:
        h.panel("browser", show=True)
    else:
        # The panel's window rule puts the new window into special:browser; show it now.
        _dispatch(h, 'hl.dsp.focus({ workspace = "special:browser" })')
    return f"opened {url} in the browser panel"
