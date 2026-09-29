"""The browser panel: Chromium in its own profile, opened on a URL, and its tabs.

Chromium runs with Bombadil's own profile (a dedicated --user-data-dir, which the debugging
port needs anyway) and the managed policy in /etc/chromium/policies/managed/bombadil.json, so
a page opened here is the only thing on screen: no welcome or what's-new tab, no
default-browser bar, no keyring prompt.

Every link on the system comes here: $BROWSER and xdg-open's https handler are
bombadil-browser, which asks agentd to open the URL, which calls `open_url`. The tabs are read
through Chromium's debugging port (the HTTP side of the DevTools protocol, no websocket):
that is how the sign-in finds its tab, notices it was closed, and reads a code from its address.
"""

import http.client
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import paths

CLASS = "bombadil-browser"
DEBUG_PORT = 9222
# Not answering: closed, not up yet, or something else on the port that is not Chromium.
DOWN = (OSError, urllib.error.URLError, ValueError, http.client.HTTPException)


def profile_dir() -> Path:
    return paths.data_dir() / "browser"


def binary() -> str:
    return shutil.which("chromium") or "chromium"


def command() -> list[str]:
    """How the browser panel starts. hyprland.lua puts the window (class bombadil-browser) in
    the panel; the rest keeps anything but the page off the screen."""
    return [binary(), "--ozone-platform=wayland", f"--class={CLASS}",
            f"--user-data-dir={profile_dir()}", f"--remote-debugging-port={DEBUG_PORT}",
            "--no-first-run", "--no-default-browser-check",
            "--password-store=basic", "--hide-crash-restore-bubble"]


class DevTools:
    """Chromium's tabs, over the HTTP endpoints of its debugging port."""

    def __init__(self, port: int = DEBUG_PORT, host: str = "127.0.0.1"):
        self.base = f"http://{host}:{port}"

    def _get(self, path: str, method: str = "GET", timeout: float = 5.0):
        req = urllib.request.Request(self.base + path, method=method)
        # The debugging port is on localhost; never send it through a proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode(errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    @property
    def up(self) -> bool:
        try:
            return isinstance(self._get("/json/version", timeout=3.0), dict)
        except DOWN:
            return False

    def tabs(self) -> list[dict]:
        """Open pages, newest first: [{"id", "url", "title", ...}]. Raises OSError when the
        browser is not running."""
        out = self._get("/json/list")
        return [t for t in out if isinstance(t, dict) and t.get("type") == "page"] if isinstance(out, list) else []

    def new_tab(self, url: str) -> dict:
        # Chromium only takes PUT here, keeps the query up to its first "&" and unescapes it:
        # the whole URL goes percent-encoded, or a sign-in page loses all but its first field.
        # (Opening a tab is slow on a slow machine: an emulated CPU takes seconds.)
        return self._get("/json/new?" + urllib.parse.quote(url, safe=""), method="PUT", timeout=20.0)

    def activate(self, tab_id: str) -> None:
        self._get(f"/json/activate/{tab_id}")

    def close(self, tab_id: str) -> None:
        self._get(f"/json/close/{tab_id}")


def same_url(a: str, b: str) -> bool:
    """The address as given and as Chromium shows it ("https://example.com" is ".../")."""
    def norm(u: str):
        x = urllib.parse.urlsplit(u)
        return (x.scheme, x.netloc, x.path or "/", x.query, x.fragment)
    return norm(a) == norm(b)


def _new_tab(devtools: DevTools, url: str) -> dict | None:
    """A new tab on `url`. When the browser is too slow to answer in time, the tab may exist
    anyway: look before opening a second."""
    for _ in range(2):
        try:
            tab = devtools.new_tab(url)
        except DOWN:
            tab = find_tab(devtools, lambda u: same_url(u, url))
        if isinstance(tab, dict) and tab.get("id"):
            return tab
    return None


def open_url(url: str, hyprland=None, devtools: DevTools | None = None, spawn=subprocess.Popen,
             wait: float = 20.0) -> dict | None:
    """Open `url` in the browser panel and slide the panel in. Returns the tab ({"id", "url"})
    when the debugging port could say which it is, else None. Sliding the panel in can fail
    (a busy compositor); the page is open then, and the panel offers itself again (`show`)."""
    from . import hypr

    hyprland = hyprland or hypr.Hyprland()
    devtools = devtools or DevTools()
    tab = None
    if not devtools.up and running():
        # Starting (or slow): it takes the page as a new tab once its port is up. Starting a
        # second Chromium with the URL would only hand the URL to this one.
        deadline = time.monotonic() + wait
        while not devtools.up and running() and time.monotonic() < deadline:
            time.sleep(0.25)
    if devtools.up:
        tab = _new_tab(devtools, url)
        if tab is not None:
            try:
                devtools.activate(tab["id"])
            except DOWN:
                pass
    elif not running():
        if shutil.which(binary()) is None:
            raise RuntimeError("chromium is not installed")
        # A fresh start opens that page alone.
        spawn([*command(), url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, start_new_session=True)
        # The page may redirect at once (a sign-in page to its login form), so its tab is the
        # one showing when the port comes up.
        deadline = time.monotonic() + wait
        while tab is None and time.monotonic() < deadline:
            time.sleep(0.25)
            tab = find_tab(devtools, lambda u: True)
    if hyprland.available:
        try:
            hyprland.panel("browser", show=True, wait=wait)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            print(f"browser: could not slide the panel in: {e}", file=sys.stderr)
    return tab


def find_tab(devtools: DevTools, match) -> dict | None:
    try:
        return next((t for t in devtools.tabs() if match(t.get("url", ""))), None)
    except DOWN:
        return None


class Panel:
    """The browser panel as the sign-in sees it. Blocking; agentd calls it in a thread."""

    def __init__(self, hyprland=None, devtools: DevTools | None = None):
        from . import hypr
        self.hypr = hyprland or hypr.Hyprland()
        self.devtools = devtools or DevTools()

    def open(self, url: str) -> dict | None:
        return open_url(url, self.hypr, self.devtools)

    def tabs(self) -> list[dict] | None:
        """The open pages; [] when the browser is not running; None when it runs but cannot
        say (its debugging port is not up yet)."""
        try:
            return self.devtools.tabs()
        except DOWN:
            return None if running() else []

    def shown(self) -> bool | None:
        """Is the browser panel on screen? None without Hyprland to ask."""
        if not self.hypr.available:
            return None
        try:
            monitors = json.loads(self.hypr.request("j/monitors"))
        except (OSError, RuntimeError, ValueError):
            return None
        return any((m.get("specialWorkspace") or {}).get("name") == "special:browser" for m in monitors)

    def show(self) -> None:
        if self.hypr.available:
            self.hypr.panel("browser", show=True)

    def hide(self) -> None:
        if self.hypr.available:
            self.hypr.panel("browser", show=False)

    def close_tab(self, tab_id: str) -> None:
        """Close a tab, but never the last one: that would quit the browser."""
        tabs = self.tabs() or []
        if any(t.get("id") == tab_id for t in tabs) and len(tabs) > 1:
            self.devtools.close(tab_id)


def running() -> bool:
    """Is the panel's Chromium running (perhaps still starting)?"""
    return subprocess.run(["pgrep", "-f", "--", f"--class={CLASS}"], capture_output=True,
                          check=False).returncode == 0
