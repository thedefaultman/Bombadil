"""The browser panel's DevTools client against a stand-in for Chromium's debugging port.

The stand-in parses /json/new the way Chromium does (checked in devtools_http_handler.cc and
live on 2026-09-27): PUT only, the query cut at its first "&", then unescaped.
"""

import http.server
import json
import threading
import urllib.parse

import pytest

from bombadil import browser


class Chromium(http.server.BaseHTTPRequestHandler):
    tabs: list[dict] = []

    def log_message(self, *a):
        pass

    def _reply(self, status, body):
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/json/version":
            return self._reply(200, {"Browser": "Chromium/153"})
        if path == "/json/list":
            return self._reply(200, self.tabs)
        if path == "/json/new":
            return self._reply(405, "Using unsafe HTTP verb GET to invoke /json/new. This action supports only PUT verb.")
        if path.startswith("/json/close/"):
            self.tabs[:] = [t for t in self.tabs if t["id"] != path.rsplit("/", 1)[1]]
            return self._reply(200, "Target is closing")
        if path.startswith("/json/activate/"):
            return self._reply(200, "Target activated")
        self._reply(404, "")

    def do_PUT(self):
        path, _, query = self.path.partition("?")
        if path != "/json/new":
            return self.do_GET()
        url = urllib.parse.unquote(query.split("&")[0])
        tab = {"id": f"T{len(self.tabs) + 1}", "type": "page", "url": url, "title": ""}
        self.tabs.insert(0, tab)
        self._reply(200, tab)


@pytest.fixture
def devtools():
    Chromium.tabs = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Chromium)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield browser.DevTools(port=server.server_port)
    server.shutdown()


class NoHyprland:
    available = False


SIGNIN = ("https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e"
          "&response_type=code&redirect_uri=http%3A%2F%2Flocalhost%3A44241%2Fcallback&scope=user%3Ainference"
          "&state=G85xIrldESHXAlDvUTWVQPrEjnVEtE0bIWkEtFWzXUs#frag")


def test_a_sign_in_page_opens_whole_in_a_new_tab(devtools):
    assert devtools.up
    tab = browser.open_url(SIGNIN, hyprland=NoHyprland(), devtools=devtools)
    assert tab["url"] == SIGNIN   # not cut at its first "&"
    assert [t["url"] for t in devtools.tabs()] == [SIGNIN]


def test_the_last_tab_is_never_closed(devtools):
    panel = browser.Panel(hyprland=NoHyprland(), devtools=devtools)
    first = panel.open("https://example.com/a")
    second = panel.open("https://example.com/b")
    panel.close_tab(second["id"])
    assert [t["id"] for t in panel.tabs()] == [first["id"]]
    panel.close_tab(first["id"])   # closing it would quit Chromium
    assert [t["id"] for t in panel.tabs()] == [first["id"]]


def test_a_browser_that_is_not_running_has_no_tabs(monkeypatch):
    monkeypatch.setattr(browser, "running", lambda: False)
    panel = browser.Panel(hyprland=NoHyprland(), devtools=browser.DevTools(port=9))
    assert not panel.devtools.up and panel.tabs() == []
    monkeypatch.setattr(browser, "running", lambda: True)   # starting: its port is not up yet
    assert panel.tabs() is None


def test_the_command_keeps_its_own_profile_and_no_first_run(home):
    cmd = browser.command()
    assert f"--user-data-dir={browser.profile_dir()}" in cmd and "--no-first-run" in cmd
    assert "--class=bombadil-browser" in cmd and "--remote-debugging-port=9222" in cmd


class Hyprland:
    """Records the panel sliding in (True) and out (False)."""
    available = True

    def __init__(self, on_show=None):
        self.calls = []
        self.on_show = on_show

    def panel(self, name, show, wait=None):
        self.calls.append(show)
        if show and self.on_show:
            self.on_show()


def test_a_browser_that_runs_but_never_answers_is_an_error_not_a_quiet_success(monkeypatch):
    monkeypatch.setattr(browser, "running", lambda: True)
    with pytest.raises(RuntimeError, match="still starting"):
        browser.open_url(SIGNIN, hyprland=NoHyprland(), devtools=browser.DevTools(port=9), wait=0.3)


def test_a_browser_that_will_not_make_the_tab_is_an_error(devtools, monkeypatch):
    def refuse(url):
        raise OSError("500")
    monkeypatch.setattr(devtools, "new_tab", refuse)
    with pytest.raises(RuntimeError, match="would not open"):
        browser.open_url(SIGNIN, hyprland=NoHyprland(), devtools=devtools)


def test_a_browser_that_closes_as_it_starts_is_an_error(monkeypatch):
    monkeypatch.setattr(browser, "running", lambda: False)
    monkeypatch.setattr(browser.shutil, "which", lambda b: "/usr/bin/chromium")
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    clock = iter(x * 0.6 for x in range(1, 200))
    monkeypatch.setattr(browser.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="closed as it started"):
        browser.open_url(SIGNIN, hyprland=NoHyprland(), devtools=browser.DevTools(port=9),
                         spawn=lambda *a, **k: None, wait=20)


def test_a_page_nobody_wants_any_more_never_slides_the_panel_in(devtools, monkeypatch):
    Chromium.tabs = [{"id": "Tother", "type": "page", "url": "about:blank", "title": ""}]
    abandon = threading.Event()
    orig = devtools.new_tab

    def slow_new_tab(url):   # the sign-in is called off while the tab is being made
        tab = orig(url)
        abandon.set()
        return tab
    monkeypatch.setattr(devtools, "new_tab", slow_new_tab)
    hypr = Hyprland()
    assert browser.open_url(SIGNIN, hyprland=hypr, devtools=devtools, abandon=abandon) is None
    assert hypr.calls == []                                       # it never slid in
    assert [t["id"] for t in devtools.tabs()] == ["Tother"]       # and its tab went again


def test_a_panel_that_slid_in_as_the_sign_in_was_called_off_slides_out_again(devtools):
    Chromium.tabs = [{"id": "Tother", "type": "page", "url": "about:blank", "title": ""}]
    abandon = threading.Event()
    hypr = Hyprland(on_show=abandon.set)
    browser.open_url(SIGNIN, hyprland=hypr, devtools=devtools, abandon=abandon)
    assert hypr.calls == [True, False]
    assert [t["id"] for t in devtools.tabs()] == ["Tother"]


def test_a_called_off_sign_in_does_not_even_start_opening(devtools):
    abandon = threading.Event()
    abandon.set()
    assert browser.open_url(SIGNIN, hyprland=NoHyprland(), devtools=devtools, abandon=abandon) is None
    assert devtools.tabs() == []
