"""The browser service (src/bombadil/browserd): the real service on a real socket, asked through the blocking client,
first against a stand-in for Chromium's debugging port (tests/browserd_fakes/fake_cdp.py: it speaks the DevTools
commands the service sends, over bombadil.ws.accept), then, where a Chromium can be started here, against a real
headless one showing local pages that imitate Slack's.

Every token in here is built at run time. The connection service's client is faked: `take` is the one thing that
reaches it.
"""

import asyncio
import json
import os
import socket
import threading
import time

import pytest
from browserd_fakes import chromium, slack_pages
from browserd_fakes.fake_cdp import FakeChrome, FakeElement, FakePage
from browserd_fakes.runner import FakeConnect, ServiceThread
from browserd_fakes.stage import until

from bombadil import browser, paths
from bombadil.browserd import cdp, overlay, service
from bombadil.browserd import client as browserd
from bombadil.connect import client as connect_client

TOKEN = slack_pages.app_token()
USER = slack_pages.user_token()
APP_PATTERN = r"xap" + r"p-[A-Za-z0-9-]{20,}"
APP_INTO = {"connection": "slack:w1", "name": "app_token"}


class Served:
    def __init__(self, chrome, connect, svc, runner):
        self.chrome, self.connect, self.service, self.runner = chrome, connect, svc, runner

    def request(self, op, timeout=10.0, **args):
        return browserd.request(op, timeout, **args)

    def refused(self, op, **args) -> browserd.BrowserdError:
        with pytest.raises(browserd.BrowserdError) as e:
            self.request(op, **args)
        return e.value

    def open_page(self, page: FakePage) -> str:
        return self.chrome.add_tab(page)


@pytest.fixture
def served(home):
    chrome = FakeChrome().start()
    connect = FakeConnect()
    svc = service.Service(devtools=browser.DevTools(port=chrome.port), connect=connect)
    runner = ServiceThread(svc).start()
    yield Served(chrome, connect, svc, runner)
    runner.stop()
    chrome.stop()


def make_page(text="Make the app", **kw) -> FakePage:
    page = FakePage("https://api.slack.test/apps?new_app=1", "Create app", text, **kw)
    page.elements = [FakeElement("Create", (700, 500, 90, 34)), FakeElement("Cancel", (580, 500, 90, 34))]
    return page


# -- the stand-in browser --

def test_status_and_tabs_say_what_the_browser_has(served):
    assert served.request("status") == {"up": True, "tabs": 0}
    first = served.open_page(make_page())
    second = served.open_page(FakePage("https://api.slack.test/other", "Other"))
    assert served.request("status") == {"up": True, "tabs": 2}
    tabs = served.request("tabs")["tabs"]
    assert {t["id"] for t in tabs} == {first, second}
    assert {"id": first, "url": "https://api.slack.test/apps?new_app=1", "title": "Create app"} in tabs


def test_open_is_the_panels_open_url_and_its_tab_is_the_one_asked_about_after(served):
    other = served.open_page(FakePage("https://api.slack.test/other", "Other", "Not this one"))
    served.chrome.page_factory = lambda url: make_page()
    url = "https://api.slack.test/apps?new_app=1&manifest_json=%7B%22a%22%3A1%7D&more=1"
    tab = served.request("open", url=url)["tab"]
    assert served.chrome.opened == [url]   # whole, not cut at its first "&"
    assert tab["id"] in served.chrome.tabs and tab["id"] != other
    assert served.request("read")["text"] == "Make the app"   # no `tab`: the one `open` made
    assert served.request("read", tab=other)["text"] == "Not this one"


def test_open_asks_the_browser_through_browser_open_url(home):
    """Not a stand-in of its own: the service calls `browser.open_url` with its DevTools, and says what it said."""
    calls = []

    def opener(url, devtools=None):
        calls.append((url, devtools))
        return {"id": "T9", "url": url}
    chrome = FakeChrome().start()
    chrome.add_tab(FakePage("https://example.test/", "x"))
    try:
        svc = service.Service(devtools=browser.DevTools(port=chrome.port), connect=FakeConnect())
        assert svc.opener is browser.open_url
        svc.opener = opener
        runner = ServiceThread(svc).start()
        try:
            assert browserd.request("open", url="https://example.test/a")["tab"] == {"id": "T9", "url": "https://example.test/a"}
            assert calls == [("https://example.test/a", svc.devtools)]
        finally:
            runner.stop()
    finally:
        chrome.stop()


def test_open_says_in_a_sentence_when_the_browser_will_not_open_a_page(served):
    def missing(url, devtools=None):
        raise RuntimeError("chromium is not installed")

    def stuck(url, devtools=None):
        raise RuntimeError("the browser is still starting")
    served.service.opener = missing
    e = served.refused("open", url="https://example.test/")
    assert (str(e), e.code) == ("The browser is not installed on this computer.", "no_browser")
    served.service.opener = stuck
    e = served.refused("open", url="https://example.test/")
    assert (str(e), e.code) == ("The browser would not open the page.", "no_browser")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "https://",
                                 "https://a.test/ b", "", None, 5, "https://a.test/\n", "x" * 9000])
def test_open_takes_web_addresses_only(served, url):
    assert served.refused("open", url=url).code == "bad_request"
    assert served.chrome.opened == []


def test_read_gives_the_visible_text_as_other_peoples_words(served):
    text = "Hello​ there‮\x07 \nsecond line\n\n\n\nthird"
    served.open_page(make_page(text, selectors={"#note": "A note"}))
    page = served.request("read")
    assert page["untrusted"] is True and page["url"].startswith("https://api.slack.test/")
    assert page["title"] == "Create app"
    assert page["text"] == "Hello there\nsecond line\n\nthird"   # made plain: nothing invisible or reordering
    assert served.request("read", selector="#note")["text"] == "A note"
    assert served.refused("read", selector="#nothing").code == "not_found"
    assert served.refused("read", selector="::bad").code == "bad_request"


def test_read_is_cut_at_two_hundred_thousand_characters(served):
    served.open_page(make_page("word " * 100_000))
    assert len(served.request("read")["text"]) <= 200_000


def test_read_hides_anything_shaped_like_a_slack_token(served):
    served.open_page(make_page(f"Your token is {TOKEN} and then {USER}, keep it."))
    text = served.request("read")["text"]
    assert TOKEN not in text and USER not in text
    assert text == "Your token is [token hidden] and then [token hidden], keep it."


def test_find_gives_the_rect_of_the_element_with_the_words(served):
    served.open_page(make_page())
    assert served.request("find", text="create") == {"found": True, "rect": {"x": 700.0, "y": 500.0, "w": 90.0, "h": 34.0}}
    assert served.request("find", text="Nothing like this") == {"found": False, "rect": None}
    assert served.refused("find", text="").code == "bad_request"
    assert served.refused("find").code == "bad_request"
    assert served.refused("find", text="x" * 300).code == "bad_request"


def test_point_draws_one_pointer_and_unpoint_takes_it_away(served):
    tab = served.open_page(make_page())
    assert served.request("point", text="Create", label="Yours: Create") == {"pointed": True}
    first = served.chrome.overlay(tab)
    assert first["label"] == "Yours: Create" and first["element"].label == "Create"
    assert served.request("point", text="Cancel", label="Yours: Cancel") == {"pointed": True}
    assert served.chrome.overlay(tab)["label"] == "Yours: Cancel"      # one pointer, not two
    assert served.request("point", text="Not there", label="Yours: x") == {"pointed": False}
    assert served.chrome.overlay(tab) is None                          # and not a stale one for something else
    served.request("point", text="Create", label="Yours: Create")
    assert served.request("unpoint") == {}
    assert served.chrome.overlay(tab) is None


def test_point_by_selector_and_what_it_will_not_take(served):
    tab = served.open_page(make_page())
    served.chrome.page(tab).elements.append(FakeElement("x", (1, 1, 10, 10), selector="#save"))
    assert served.request("point", selector="#save", label="Yours: Save") == {"pointed": True}
    for args in ({"label": "Yours"}, {"text": "Create", "selector": "#save", "label": "Yours"},
                 {"text": "Create"}, {"text": "Create", "label": "x" * 80}, {"text": "Create", "label": "Y", "ttl": 0},
                 {"text": "Create", "label": "Y", "ttl": 9999}, {"text": "Create", "label": "Y", "ttl": "20"},
                 {"selector": "javascript:alert(1)", "label": "Y"}, {"selector": "a" * 300, "label": "Y"},
                 {"selector": "a\nb", "label": "Y"}):
        assert served.refused("point", **args).code == "bad_request", args


def test_a_pointer_goes_at_its_time_and_when_the_page_is_replaced(served):
    tab = served.open_page(make_page())
    served.request("point", text="Create", label="Yours: Create", ttl=0.5)
    until(lambda: served.chrome.overlay(tab) is None, 5, "the pointer to go at its time")
    until(lambda: not served.runner.call(lambda: dict(served.service._timers)), 5, "the service's timer to be done")
    served.request("point", text="Create", label="Yours: Create", ttl=60)
    assert served.runner.call(lambda: list(served.service._timers)) == [tab]
    served.chrome.navigate(tab, FakePage("https://api.slack.test/apps/A0ACME1234/", "App"))
    assert served.chrome.overlay(tab) is None
    until(lambda: not served.runner.call(lambda: dict(served.service._timers)), 5,
          "the navigation to be heard and the timer put away")


def test_a_page_whose_own_timers_are_held_back_still_loses_the_pointer(served):
    """The page takes the pointer away itself; this is for a page (a hidden tab) whose timers do not run."""
    tab = served.open_page(make_page())
    served.chrome.page(tab).hold_timers = True
    served.request("point", text="Create", label="Yours: Create", ttl=0.5)
    assert served.chrome.overlay(tab) is not None
    until(lambda: served.chrome.overlay(tab) is None, 5, "the service to take the pointer away")


def test_wait_returns_when_the_page_is_there_and_says_no_when_it_is_not(served):
    tab = served.open_page(make_page())
    started = time.monotonic()
    assert served.request("wait", url=r"/apps\?new_app", seconds=3)["matched"] is True
    assert time.monotonic() - started < 1.5
    started = time.monotonic()
    assert served.request("wait", url=r"/apps/A[A-Z0-9]+", seconds=0.7) == {
        "matched": False, "url": "https://api.slack.test/apps?new_app=1"}
    assert 0.6 < time.monotonic() - started < 3
    threading.Timer(0.5, served.chrome.navigate, (tab, FakePage("https://api.slack.test/apps/A0ACME1234/", "App",
                                                                 "Basic Information"))).start()
    got = served.request("wait", url=r"/apps/A[A-Z0-9]+", text="basic information", seconds=10)
    assert got == {"matched": True, "url": "https://api.slack.test/apps/A0ACME1234/"}


def test_wait_needs_something_to_wait_for_and_a_pattern_that_is_one(served):
    served.open_page(make_page())
    assert served.refused("wait", seconds=1).code == "bad_request"
    assert served.refused("wait", url="(", seconds=1).code == "bad_request"
    assert served.refused("wait", url="a" * 400, seconds=1).code == "bad_request"
    assert served.request("wait", text="make the app", seconds=0)["matched"] is True   # one look, no waiting


def test_take_hands_the_first_match_to_the_connection_service_and_nothing_comes_back(served, capsys):
    served.open_page(make_page(f"Nothing here.\nToken: {TOKEN}\n", inputs=["", USER]))
    answers = [served.request("take", pattern=APP_PATTERN, into=APP_INTO)]
    answers.append(served.request("take", pattern=r"xox" + r"p-[A-Za-z0-9-]{20,}",
                                  into={"connection": "slack:w1", "name": "user_token"}))
    assert answers == [{"stored": True}, {"stored": True}]
    assert served.connect.calls == [("store_secret", {"connection": "slack:w1", "name": "app_token", "value": TOKEN}),
                                    ("store_secret", {"connection": "slack:w1", "name": "user_token", "value": USER})]
    seen = capsys.readouterr()
    for secret in (TOKEN, USER):
        assert secret not in json.dumps(answers) + seen.out + seen.err
        assert secret not in repr(vars(served.service))   # and nothing keeps it


def test_take_finds_a_token_in_a_field_the_text_does_not_show(served):
    served.open_page(make_page("Nothing to see.", inputs=[TOKEN]))
    assert served.request("take", pattern=APP_PATTERN, into=APP_INTO) == {"stored": True}
    assert served.connect.calls[0][1]["value"] == TOKEN


def test_take_says_what_it_could_not_do_in_a_sentence(served):
    served.open_page(make_page("Nothing here."))
    assert served.request("take", pattern=APP_PATTERN, into=APP_INTO) == {
        "stored": False, "found": False, "why": "Nothing on the page looks like that."}
    assert served.connect.calls == []
    served.chrome.page(next(iter(served.chrome.tabs))).text = f"Token {TOKEN}"
    served.connect.fail = connect_client.ConnectError("Slack would not take that one.", "bad_request")
    assert served.request("take", pattern=APP_PATTERN, into=APP_INTO) == {
        "stored": False, "found": True, "why": "Slack would not take that one."}
    served.connect.fail = connect_client.ConnectUnavailable("no socket")
    assert served.request("take", pattern=APP_PATTERN, into=APP_INTO)["why"] == "Connections are not running yet."


def test_a_value_never_comes_back_even_when_the_connection_service_says_it(served, capsys):
    served.open_page(make_page(f"Token {TOKEN}"))
    served.connect.fail = connect_client.ConnectError(f"Not this one: {TOKEN}", "bad_request")
    got = served.request("take", pattern=APP_PATTERN, into=APP_INTO)
    assert got == {"stored": False, "found": True, "why": "Connections would not take it."}
    served.connect.fail = RuntimeError(f"it broke on {TOKEN}")
    got = served.request("take", pattern=APP_PATTERN, into=APP_INTO)
    assert got == {"stored": False, "found": True, "why": "Connections could not take it just now."}
    seen = capsys.readouterr()
    assert TOKEN not in seen.out + seen.err and "RuntimeError" in seen.err   # the kind of failure is logged, not its text


def test_take_checks_what_it_is_asked_to_do(served):
    served.open_page(make_page(f"Token {TOKEN}"))
    for args in ({"pattern": "("}, {"pattern": ""}, {"pattern": "a" * 400},
                 {"pattern": APP_PATTERN, "into": {"connection": "../x", "name": "app_token"}},
                 {"pattern": APP_PATTERN, "into": {"connection": "slack:w1", "name": "App Token"}},
                 {"pattern": APP_PATTERN, "into": {"connection": "slack:w1", "name": "app_token", "extra": 1}},
                 {"pattern": APP_PATTERN, "into": "slack:w1"}, {"pattern": APP_PATTERN, "into": None}):
        args.setdefault("into", APP_INTO)
        assert served.refused("take", **args).code == "bad_request", args
    assert served.connect.calls == []


def test_a_match_too_long_to_be_a_token_is_not_taken(served):
    served.open_page(make_page("a" * 5000))
    assert served.request("take", pattern="a+", into=APP_INTO)["found"] is False
    assert served.connect.calls == []


def test_a_peer_of_another_uid_is_refused(served, monkeypatch):
    served.open_page(make_page())
    assert oct(paths.browserd_socket().stat().st_mode & 0o777) == "0o600"
    real_peer_uid = service.peer_uid
    monkeypatch.setattr(service, "peer_uid", lambda sock: os.getuid() + 1)
    commands = len(served.chrome.commands)
    with pytest.raises(browserd.BrowserdUnavailable):
        served.request("read")
    assert served.runner.call(lambda: len(served.service.clients)) == 0
    assert len(served.chrome.commands) == commands   # nothing reached the browser
    monkeypatch.setattr(service, "peer_uid", lambda sock: -1)   # the kernel could not say
    with pytest.raises(browserd.BrowserdUnavailable):
        served.request("tabs")
    monkeypatch.setattr(service, "peer_uid", real_peer_uid)
    assert served.request("status")["up"] is True


def test_the_peer_uid_is_the_kernels_word_for_who_is_on_the_other_end(tmp_path):
    a, b = socket.socketpair(socket.AF_UNIX)
    with a, b:
        assert service.peer_uid(a) == os.getuid()
    assert service.peer_uid(object()) == -1


def test_callers_pass_data_never_code(served):
    served.open_page(make_page())
    for key in ("script", "expression", "js", "code", "eval", "function"):
        assert served.refused("read", **{key: "document.cookie"}).code == "bad_request"
        assert served.refused("find", text="Create", **{key: "1"}).code == "bad_request"
    assert served.chrome.evaluated == []   # not one of them reached the page
    hostile = '"); window.pwned = 1; ("'
    served.request("find", text=hostile)
    served.request("point", text=hostile, label=hostile[:50], ttl=1)
    served.request("wait", text=hostile, seconds=0)
    assert served.refused("read", selector="javascript:alert(1)").code == "bad_request"
    assert served.refused("read", selector="a" * 300).code == "bad_request"
    seen = served.chrome.evaluated
    assert len(seen) == 3
    for _tab, expression in seen:
        name, args, function = overlay.parse(expression)
        assert function == overlay.SCRIPTS[name]   # the fixed script, as it is in overlay.py
        assert hostile in args.values() or hostile[:50] in args.values()
        assert expression.isascii() and expression.count("\n") == overlay.SCRIPTS[name].count("\n") + 2
        assert expression == overlay.expression(name, args)   # and the data is the one JSON literal after it


def test_a_script_the_stand_in_does_not_know_is_not_run_by_it(served):
    """The stand-in only runs the fixed scripts: that is what the test above leans on."""
    tab = served.open_page(make_page())

    async def go():
        session = await cdp.Session.open(tab, f"ws://127.0.0.1:{served.chrome.tab(tab).port}/x", lambda *a: None)
        try:
            with pytest.raises(cdp.Failed):
                await session.evaluate("document.cookie")
        finally:
            await session.close()
    asyncio.run(go())


def test_a_tab_that_is_gone_is_a_sentence(served):
    assert (str(served.refused("read")), served.refused("read").code) == ("There is no page open.", "no_tab")
    tab = served.open_page(make_page())
    assert served.request("read")["text"] == "Make the app"   # the connection to the tab is made now
    served.chrome.close_tab(tab)
    for op, args in (("read", {}), ("find", {"text": "Create"}), ("wait", {"text": "x", "seconds": 1}),
                     ("unpoint", {}), ("point", {"text": "Create", "label": "Y"}),
                     ("take", {"pattern": "a", "into": APP_INTO})):
        e = served.refused(op, tab=tab, **args)
        assert (str(e), e.code) == ("That page is gone.", "no_tab"), op
    e = served.refused("read", tab="NOPE")
    assert (str(e), e.code) == ("That page is gone.", "no_tab")
    assert served.refused("read", tab="../x").code == "bad_request"


def test_a_tab_closed_while_waiting_ends_the_wait_with_a_sentence(served):
    tab = served.open_page(make_page())
    served.request("read")
    threading.Timer(0.5, served.chrome.close_tab, (tab,)).start()
    e = served.refused("wait", text="never there", seconds=10, tab=tab)
    assert e.code == "no_tab"


def test_no_browser_running_is_a_sentence_and_a_code(home):
    port = chromium.free_port()   # nothing listens
    svc = service.Service(devtools=browser.DevTools(port=port), connect=FakeConnect())
    runner = ServiceThread(svc).start()
    try:
        assert browserd.request("status") == {"up": False, "tabs": 0}
        for op, args in (("tabs", {}), ("read", {}), ("find", {"text": "x"}), ("wait", {"text": "x", "seconds": 0}),
                         ("point", {"text": "x", "label": "y"}), ("take", {"pattern": "a", "into": APP_INTO})):
            with pytest.raises(browserd.BrowserdError) as e:
                browserd.request(op, **args)
            assert (str(e.value), e.value.code) == ("The browser is not running.", "no_browser"), op
    finally:
        runner.stop()


def test_a_browser_that_goes_away_mid_session(served):
    served.open_page(make_page())
    assert served.request("read")["text"] == "Make the app"
    served.chrome.stop()
    assert served.request("status") == {"up": False, "tabs": 0}
    assert served.refused("read").code == "no_browser"


def test_a_page_that_does_not_answer_is_a_sentence_and_a_wait_goes_on_to_its_end(served, monkeypatch):
    monkeypatch.setattr(cdp, "EVALUATE_S", 0.4)
    tab = served.open_page(make_page())
    served.chrome.tab(tab).stall = True
    e = served.refused("read")
    assert (str(e), e.code) == ("The page did not answer.", "page")
    assert served.request("wait", text="x", seconds=0.5)["matched"] is False
    served.chrome.tab(tab).stall = False
    assert served.request("read")["text"] == "Make the app"


def test_a_page_that_is_being_replaced_under_a_script_is_asked_again(served):
    tab = served.open_page(make_page())
    served.chrome.tab(tab).errors = ["Execution context was destroyed, most likely because of a navigation."]
    assert served.request("read")["text"] == "Make the app"
    served.chrome.tab(tab).errors = ["Something else broke"] * 3
    e = served.refused("read")
    assert (str(e), e.code) == ("The page could not be read just now.", "page")


def test_only_a_debugging_address_on_this_computer_is_connected_to(served):
    served.open_page(make_page())
    served.chrome.ws_host = "192.0.2.7"
    e = served.refused("read")
    assert (str(e), e.code) == ("That page cannot be reached.", "no_tab")


def test_lines_that_are_not_requests_are_answered_not_fatal(served):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(5)
        s.connect(str(paths.browserd_socket()))
        f = s.makefile("rwb")
        for line, code in ((b"not json\n", "bad_request"), (b"[1]\n", "bad_request"),
                           (b'{"id": 1, "op": "click"}\n', "bad_request"),
                           (b'{"id": 2, "op": "read", "tab": 5}\n', "bad_request")):
            f.write(line)
            f.flush()
            answer = json.loads(f.readline())
            assert answer["ok"] is False and answer["code"] == code
            assert answer["error"].endswith(".") and "!" not in answer["error"]
        f.write(b'{"id": 3, "op": "status"}\n')
        f.flush()
        assert json.loads(f.readline()) == {"id": 3, "ok": True, "result": {"up": True, "tabs": 0}}
        f.write(b"x" * ((1 << 20) + 10) + b"\n")
        f.flush()
        assert json.loads(f.readline())["error"] == "That request is too long."


def test_one_service_per_socket(served):
    with pytest.raises(service.AlreadyRunning):
        asyncio.run(service.Service(devtools=served.service.devtools, connect=FakeConnect()).serve())
    assert browserd.request("status")["up"] is True   # and the first is untouched


def test_the_socket_is_taken_away_when_the_service_stops(home):
    chrome = FakeChrome().start()
    try:
        runner = ServiceThread(service.Service(devtools=browser.DevTools(port=chrome.port))).start()
        assert paths.browserd_socket().is_socket()
        runner.stop()
        assert not paths.browserd_socket().exists()
    finally:
        chrome.stop()


# -- the client --

def test_the_client_says_so_when_the_service_is_not_running(home):
    with pytest.raises(browserd.BrowserdUnavailable) as e:
        browserd.request("status")
    assert str(e.value) == "The browser service is not running."
    assert browserd.notify("unpoint") is False


def test_the_client_wait_takes_its_length_as_seconds_and_its_own_limit_from_it(served, monkeypatch):
    sent = []
    real = browserd.Connection.request

    def spy(self, op, timeout=None, **args):
        sent.append((op, timeout, args))
        return real(self, op, timeout, **args)
    monkeypatch.setattr(browserd.Connection, "request", spy)
    served.open_page(make_page())
    assert browserd.request("wait", text="make the app", seconds=1.5)["matched"] is True
    assert browserd.request("wait", 4.0, text="make the app", seconds=1)["matched"] is True
    assert sent[0][0] == "wait" and sent[0][1] == pytest.approx(6.5, abs=0.1)
    assert sent[0][2] == {"text": "make the app", "seconds": 1.5}
    assert sent[1][1] == pytest.approx(4.0, abs=0.1) and sent[1][2] == {"text": "make the app", "seconds": 1}


def test_the_client_times_out_on_a_service_that_does_not_answer(tmp_path):
    path = tmp_path / "quiet.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(1)
    try:
        with browserd.Connection(path, timeout=0.5) as conn:
            started = time.monotonic()
            with pytest.raises(browserd.BrowserdUnavailable):
                conn.request("status", 0.4)
            assert time.monotonic() - started < 2
            with pytest.raises(browserd.BrowserdUnavailable):   # and the connection is closed, not confused
                conn.request("status")
    finally:
        server.close()


# -- a real Chromium --

PAGES = {
    "/make": f"""<!doctype html><html><head><title>Make the app</title></head><body style="margin:0">
<h1>Make the app</h1>
<button id="create" style="margin:40px 120px;padding:10px 20px" onclick="window.clicks=(window.clicks||0)+1">Create</button>
<button id="ghost" style="display:none">Create</button><button id="invisible" style="visibility:hidden">Create now</button>
<button id="long">Create a new app from scratch</button>
<button id="cancel">Cancel</button><a id="link" href="/other">Install to Workspace</a>
<input id="name" placeholder="Token Name"><label>Scope <input id="scope"></label>
<input id="tok" readonly value="{TOKEN}"><textarea id="note">{USER}</textarea>
<p id="inline">The token is <code>{USER}</code> <button id="copy">Copy</button></p>
<div style="height:2500px"></div><button id="far" style="margin-left:60px">Generate Token and Scopes</button>
</body></html>""",
    "/other": "<!doctype html><title>Other page</title><h1>Other page</h1><button>Back</button>",
}


def render(path, query):
    return (200, PAGES[path]) if path in PAGES else (404, "<h1>Not found</h1>")


@pytest.fixture(scope="module")
def chrome_proc():
    proc = chromium.Chromium()
    try:
        proc.start()
    except chromium.ChromiumUnavailable as e:
        pytest.skip(f"no real Chromium to test against: {e}")
    yield proc
    proc.stop()


class Real:
    def __init__(self, chrome_proc, home):
        self.proc = chrome_proc
        self.pages = chromium.Pages(render)
        self.connect = FakeConnect()
        self.service = service.Service(devtools=chrome_proc.devtools, connect=self.connect)
        self.runner = ServiceThread(self.service).start()
        self.cdps: list[chromium.Cdp] = []

    def request(self, op, timeout=20.0, **args):
        return browserd.request(op, timeout, **args)

    def refused(self, op, **args) -> browserd.BrowserdError:
        with pytest.raises(browserd.BrowserdError) as e:
            self.request(op, **args)
        return e.value

    def open(self, path="/make") -> str:
        tab = self.request("open", url=self.pages.base + path)["tab"]["id"]
        until(lambda: self.request("wait", url=path, seconds=1)["matched"], 20, "the page to load")
        until(lambda: self.cdp(tab).evaluate("document.readyState") == "complete", 20, "the page to be ready")
        return tab

    def cdp(self, tab: str) -> chromium.Cdp:
        for c in self.cdps:
            if c.tab == tab:
                return c
        found = next(t for t in self.proc.devtools.tabs() if t["id"] == tab)
        c = chromium.Cdp(found["webSocketDebuggerUrl"])
        c.tab = tab
        self.cdps.append(c)
        return c

    def pointer(self, tab: str):
        return self.cdp(tab).evaluate("""(function () {
          var o = document.getElementById('bombadil-pointer');
          if (!o) return null;
          var r = o.getBoundingClientRect(), cs = getComputedStyle(o), chip = o.firstChild;
          return {x: r.left, y: r.top, w: r.width, h: r.height, label: chip.textContent, position: cs.position,
                  events: cs.pointerEvents, z: cs.zIndex, ring: cs.outlineColor, visibility: cs.visibility,
                  count: document.querySelectorAll('[id=bombadil-pointer]').length,
                  chipEvents: getComputedStyle(chip).pointerEvents};
        })()""")

    def rect_of(self, tab: str, element_id: str) -> dict:
        return self.cdp(tab).evaluate(f"(function(){{var r=document.getElementById('{element_id}').getBoundingClientRect();"
                                      "return {x: r.left, y: r.top, w: r.width, h: r.height}})()")

    def close(self):
        self.runner.stop()
        for c in self.cdps:
            c.close()
        for tab in self.proc.devtools.tabs():
            if tab["url"].startswith(self.pages.base) and len(self.proc.devtools.tabs()) > 1:
                self.proc.devtools.close(tab["id"])
        self.pages.stop()


@pytest.fixture
def real(home, chrome_proc):
    env = Real(chrome_proc, home)
    yield env
    env.close()


def near(a: dict, b: dict, slack: float = 1.5) -> bool:
    return all(abs(a[k] - b[k]) <= slack for k in ("x", "y", "w", "h"))


def test_real_open_makes_a_tab_with_the_whole_address(real):
    url = real.pages.base + "/make?" + "&".join(f"k{i}=%7B%22v%22%3A{i}%7D" for i in range(120))
    assert len(url) > 1500
    tab = real.request("open", url=url)["tab"]
    until(lambda: real.request("wait", url=r"/make\?k0=", seconds=1)["matched"], 20, "the page to load")
    assert next(t for t in real.request("tabs")["tabs"] if t["id"] == tab["id"])["url"] == url
    assert real.request("status")["up"] is True


def test_real_read_gives_the_visible_text_only(real):
    real.open()
    page = real.request("read")
    assert page["title"] == "Make the app" and page["url"].endswith("/make") and page["untrusted"] is True
    assert "Make the app" in page["text"] and "Generate Token and Scopes" in page["text"]
    assert "Create now" not in page["text"]   # visibility: hidden is not visible text
    assert TOKEN not in page["text"] and USER not in page["text"]   # a token on the page is not given out
    assert "[token hidden]" in page["text"]
    assert real.request("read", selector="h1")["text"] == "Make the app"
    assert real.request("read", selector="#tok")["text"] == "[token hidden]"
    assert real.refused("read", selector="#nothing-like-this").code == "not_found"
    assert real.refused("read", selector="div[").code == "bad_request"


def test_real_find_picks_the_visible_element_with_the_best_match(real):
    tab = real.open()
    found = real.request("find", text="create")   # case-insensitive; the exact "Create" beats "Create a new app..."
    assert found["found"] and near(found["rect"], real.rect_of(tab, "create"))
    found = real.request("find", text="Create a new")   # part of the words still finds the one that has them
    assert near(found["rect"], real.rect_of(tab, "long"))
    assert real.request("find", text="token name")["found"]   # a placeholder
    assert near(real.request("find", text="Scope")["rect"], real.rect_of(tab, "scope"))   # a label
    assert real.request("find", text="Install to Workspace")["found"]   # a link
    assert real.request("find", text="Create now") == {"found": False, "rect": None}   # not visible
    assert real.request("find", text="Nothing like this") == {"found": False, "rect": None}


def test_real_find_takes_words_as_words_not_as_patterns_or_code(real):
    tab = real.open()
    for hostile in ('"); window.pwned = 1; ("', "a.*b", "(", "[", "\\", "Create (", "x'; alert(1); '"):
        assert real.request("find", text=hostile) == {"found": False, "rect": None}
    assert real.cdp(tab).evaluate("typeof window.pwned") == "undefined"


def test_real_point_draws_one_ring_over_the_element_and_does_not_take_a_click(real):
    tab = real.open()
    assert real.request("point", text="Create", label="Yours: Create", ttl=30) == {"pointed": True}
    ring = real.pointer(tab)
    assert near(ring, real.rect_of(tab, "create"))
    assert (ring["position"], ring["events"], ring["chipEvents"], ring["label"]) == (
        "fixed", "none", "none", "Yours: Create")
    assert int(ring["z"]) >= 2147483647 and ring["ring"] == "rgb(217, 119, 87)" and ring["count"] == 1
    # another point replaces it: still one
    real.request("point", text="Cancel", label="Yours: Cancel", ttl=30)
    ring = real.pointer(tab)
    assert ring["label"] == "Yours: Cancel" and ring["count"] == 1 and near(ring, real.rect_of(tab, "cancel"))
    # a real click in the middle of the ring goes through to the button under it
    real.request("point", text="Create", label="Yours: Create", ttl=30)
    ring = real.pointer(tab)
    real.cdp(tab).click(ring["x"] + ring["w"] / 2, ring["y"] + ring["h"] / 2)
    assert real.cdp(tab).evaluate("window.clicks") == 1
    assert real.pointer(tab) is not None   # and it is still there
    assert real.request("unpoint") == {}
    assert real.pointer(tab) is None
    assert real.request("point", text="Nothing like this", label="Yours: x") == {"pointed": False}


def test_real_pointer_goes_at_its_time(real):
    tab = real.open()
    started = time.monotonic()
    real.request("point", text="Create", label="Yours: Create", ttl=1.5)
    assert real.pointer(tab) is not None
    until(lambda: real.pointer(tab) is None, 8, "the pointer to go")
    assert 1.2 < time.monotonic() - started < 6
    until(lambda: not real.runner.call(lambda: dict(real.service._timers)), 5, "the service's timer to be done")


def test_real_pointer_goes_when_the_page_is_replaced(real):
    tab = real.open()
    real.request("point", text="Create", label="Yours: Create", ttl=60)
    real.cdp(tab).evaluate("location.hash = 'moved'")   # an address change the page makes itself
    until(lambda: real.pointer(tab) is None, 5, "the pointer to go when the address changed")
    real.request("point", text="Create", label="Yours: Create", ttl=60)
    real.cdp(tab).evaluate("location.href = '/other'")
    until(lambda: real.request("wait", url="/other", seconds=1)["matched"], 20, "the other page")
    assert real.pointer(tab) is None
    until(lambda: not real.runner.call(lambda: dict(real.service._timers)), 5, "the timer to be put away")


def test_real_pointer_follows_a_page_that_scrolls_and_one_that_changes(real):
    tab = real.open()
    real.request("point", text="Create", label="Yours: Create", ttl=60)
    before = real.pointer(tab)
    real.cdp(tab).evaluate("window.scrollTo(0, 25)")
    until(lambda: real.pointer(tab)["y"] < before["y"] - 20, 5, "the pointer to follow the scroll")
    assert near(real.pointer(tab), real.rect_of(tab, "create"))
    real.cdp(tab).evaluate("document.getElementById('create').style.marginLeft = '300px'")
    until(lambda: near(real.pointer(tab), real.rect_of(tab, "create")), 5, "the pointer to follow the button")
    real.cdp(tab).evaluate("document.getElementById('create').style.width = '240px'")
    until(lambda: near(real.pointer(tab), real.rect_of(tab, "create")), 5, "the pointer to follow its size")
    # the element drawn again by the page (a framework does): the pointer finds the new one
    real.cdp(tab).evaluate("(function(){var b=document.getElementById('create');var n=b.cloneNode(true);"
                           "n.id='create2';b.parentNode.replaceChild(n,b)})()")
    until(lambda: real.pointer(tab) is not None and near(real.pointer(tab), real.rect_of(tab, "create2")), 5,
          "the pointer to find the button drawn again")


def test_real_pointer_scrolls_to_a_button_nobody_can_see_and_points_at_it(real):
    tab = real.open()
    assert real.cdp(tab).evaluate("window.scrollY") == 0
    assert real.request("point", text="Generate Token and Scopes", label="Yours: Generate", ttl=30) == {"pointed": True}
    assert real.cdp(tab).evaluate("window.scrollY") > 1000
    ring = real.pointer(tab)
    assert ring["visibility"] == "visible" and near(ring, real.rect_of(tab, "far"))
    assert 0 <= ring["y"] < 800


def test_real_pointer_by_selector(real):
    tab = real.open()
    assert real.request("point", selector="#cancel", label="Yours: Cancel", ttl=30) == {"pointed": True}
    assert near(real.pointer(tab), real.rect_of(tab, "cancel"))
    assert real.request("point", selector="#ghost", label="Yours: x") == {"pointed": False}   # not visible
    assert real.refused("point", selector="div[", label="Yours: x").code == "bad_request"


def test_real_wait_for_an_address_and_for_words(real):
    tab = real.open()
    assert real.request("wait", url=r"/make$", text="generate token", seconds=3)["matched"] is True
    started = time.monotonic()
    assert real.request("wait", url=r"/apps/A", seconds=0.8)["matched"] is False
    assert 0.7 < time.monotonic() - started < 5
    threading.Timer(0.6, lambda: real.cdp(tab).evaluate("location.href = '/other'")).start()
    got = real.request("wait", url="/other$", text="other page", seconds=20)
    assert got["matched"] is True and got["url"].endswith("/other")


def test_real_take_reads_a_token_off_the_page_into_the_connection_service(real, capsys):
    real.open()
    answers = [real.request("take", pattern=APP_PATTERN, into=APP_INTO),
               real.request("take", pattern=r"xox" + r"p-[A-Za-z0-9-]{20,}",
                            into={"connection": "slack:w1", "name": "user_token"})]
    assert answers == [{"stored": True}, {"stored": True}]
    assert [c[1]["value"] for c in real.connect.calls] == [TOKEN, USER]   # the page's own values, whole
    seen = capsys.readouterr()
    assert TOKEN not in json.dumps(answers) + seen.out + seen.err
    assert real.request("take", pattern=r"xoxb-[A-Za-z0-9-]{20,}", into=APP_INTO)["found"] is False


def test_real_a_tab_closed_is_a_sentence(real):
    tab = real.open("/other")
    real.request("read", tab=tab)
    real.proc.devtools.close(tab)
    until(lambda: all(t["id"] != tab for t in real.proc.devtools.tabs()), 10, "the tab to close")
    e = real.refused("read", tab=tab)
    assert (str(e), e.code) == ("That page is gone.", "no_tab")


def test_real_no_browser_is_a_sentence(home):
    svc = service.Service(devtools=browser.DevTools(port=chromium.free_port()), connect=FakeConnect())
    runner = ServiceThread(svc).start()
    try:
        with pytest.raises(browserd.BrowserdError) as e:
            browserd.request("read")
        assert (str(e.value), e.value.code) == ("The browser is not running.", "no_browser")
    finally:
        runner.stop()
