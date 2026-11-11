"""The Slack setup recipe (src/bombadil/connect/slack_setup.py), run the way agentd runs it: `run_setup` blocking in a
thread, against the real browser service on a real socket, with a "person" in the test who presses what the recipe
points at.

Each walk is run on both backends where a Chromium can be started here: a real headless Chromium showing local pages
that imitate Slack's (the person finds the ring and clicks the middle of it with a real mouse event), and the fake
debugging port with models of the same pages (tests/browserd_fakes/stage.py, slack_pages.py). The connection service is
faked twice: one stand-in for the browser service (the only thing that stores a secret) and one for the recipe (which
may only ask for `connections`), sharing a store, so a test can tell which of them a token went through.

Nothing here has met Slack's real pages. Every token is built at run time.
"""

import json
import logging
import re
import threading
import time

import pytest
from browserd_fakes import chromium, slack_pages
from browserd_fakes.runner import FakeConnect, ServiceThread
from browserd_fakes.stage import ChromiumStage, FakeStage, until

from bombadil.browserd import cdp, overlay, service
from bombadil.browserd import client as browserd
from bombadil.connect import client as connect_client
from bombadil.connect import manifest, slack_setup

OPEN, APP, INSTALL = slack_setup.SAY_OPEN, slack_setup.SAY_APP, slack_setup.SAY_INSTALL
RESUME, INSTALL_AGAIN = slack_setup.SAY_RESUME, slack_setup.SAY_INSTALL_AGAIN
ALLOW, TOKEN, WALL = slack_setup.SAY_ALLOW, slack_setup.SAY_TOKEN, slack_setup.SAY_WALL
FIRST, FIRST_MISSING = slack_setup.SAY_FIRST, slack_setup.SAY_FIRST_MISSING
STOPPED = slack_setup.SAY_STOPPED
CONNECTED = {"ok": True, "say": "Slack is connected to “Acme”."}
THE_OPS = {"open", "wait", "point", "find", "read", "take", "unpoint"}


@pytest.fixture(scope="module")
def chrome_proc():
    proc = chromium.Chromium()
    try:
        proc.start()
    except chromium.ChromiumUnavailable as e:
        pytest.skip(f"no real Chromium to test against: {e}")
    yield proc
    proc.stop()


@pytest.fixture(params=["fake", "chromium"])
def make_stage(request, home, monkeypatch):
    """`make_stage(site)`: a place to run the recipe in, on this backend, closed after the test. On Chromium the
    pages are local, so the address the recipe opens is the local copy of the manifest address."""
    made = []

    def make(site=None):
        site = site or slack_pages.Site()
        if request.param == "fake":
            stage = FakeStage(site)
        else:
            stage = ChromiumStage(request.getfixturevalue("chrome_proc"), site)
            monkeypatch.setattr(manifest, "creation_url", lambda: stage.creation_url)
            monkeypatch.setattr(manifest, "apps_url", lambda: stage.apps_url)
        made.append(stage)
        return stage
    yield make
    for stage in made:
        stage.close()


@pytest.fixture
def fake_stage(home):
    """The stand-in browser alone, for what only a stand-in can be made to do (stall, go away)."""
    made = []

    def make(site=None):
        stage = FakeStage(site or slack_pages.Site())
        made.append(stage)
        return stage
    yield make
    for stage in made:
        stage.close()


class Recorder:
    """The browser service's client as agentd's recipe gets it, keeping every call and what came back."""

    BrowserdError = browserd.BrowserdError
    BrowserdUnavailable = browserd.BrowserdUnavailable

    def __init__(self):
        self.calls: list[dict] = []

    def request(self, op, timeout=None, **args):
        entry = {"op": op, "args": args}
        self.calls.append(entry)
        try:
            entry["result"] = browserd.request(op, timeout, **args)
        except browserd.BrowserdError as e:
            entry["error"] = (str(e), e.code)
            raise
        return entry["result"]

    def notify(self, op, **args):
        self.calls.append({"op": op, "args": args, "notify": True})
        return browserd.notify(op, **args)

    def ops(self) -> list[str]:
        return [c["op"] for c in self.calls]

    def pointed(self) -> list[str]:
        """The labels of the pointers that were drawn, one for each run of the same."""
        labels = [c["args"]["label"] for c in self.calls if c["op"] == "point" and (c.get("result") or {}).get("pointed")]
        return [label for i, label in enumerate(labels) if i == 0 or labels[i - 1] != label]


class Run:
    """One `run_setup` in a thread, with the browser service its calls go to."""

    def __init__(self, stage, state="ok", name="Acme", opener=None, with_service=True, store=None):
        self.stage = stage
        self.store: dict = {} if store is None else store
        self.taker = FakeConnect(self.store)                          # the browser service's: where `take` stores
        self.asked = FakeConnect(self.store, state=state, name=name)  # the recipe's own: it only asks
        self.runner = None
        if with_service:
            self.service = service.Service(devtools=stage.devtools, connect=self.taker, opener=opener)
            self.runner = ServiceThread(self.service).start()
        self.browserd = Recorder()
        self.said: list[str] = []
        self.cancel = threading.Event()
        self.result = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="setup-under-test")

    def _run(self):
        self.result = slack_setup.run_setup("slack:w1", self.said.append, self.cancel.is_set,
                                            browserd=self.browserd, connect=self.asked)

    def start(self) -> "Run":
        self.thread.start()
        return self

    def done(self, timeout: float = 40.0) -> dict:
        self.thread.join(timeout)
        assert not self.thread.is_alive(), f"the recipe did not finish in {timeout} s; it said {self.said}"
        return self.result

    def close(self) -> None:
        self.cancel.set()
        self.thread.join(10)
        if self.runner is not None:
            self.runner.stop()


@pytest.fixture
def runs():
    made = []

    def make(stage, **kw) -> Run:
        run = Run(stage, **kw)
        made.append(run)
        return run
    yield make
    for run in made:
        run.close()


# What the person presses, as the recipe asks it.

def make_the_app(stage, two_step=False):
    if two_step:
        assert stage.press_pointed("Yours: Next") == "Next"
    assert stage.press_pointed("Yours: Create") == "Create"


def make_the_app_token(stage):
    assert stage.press_pointed("Yours: Generate Token") == "Generate Token and Scopes"
    stage.press("Generate")


def install_the_app(stage):
    assert stage.press_pointed("Yours: Install") == "Install to Workspace"
    assert stage.press_pointed("Yours: Allow") == "Allow"


def the_whole_walk(stage, two_step=False):
    make_the_app(stage, two_step)
    make_the_app_token(stage)
    install_the_app(stage)


def nothing_stored(run):
    return run.store == {}


# -- the walk --

@pytest.mark.parametrize("variant", [{}, {"two_step": True}, {"token_in_text": True}],
                         ids=["in order", "a workspace to choose first", "the token as words on the page"])
def test_the_walk_in_order(make_stage, runs, variant):
    site = slack_pages.Site(**variant)
    stage = make_stage(site)
    run = runs(stage).start()
    the_whole_walk(stage, two_step=bool(variant.get("two_step")))
    assert run.done() == CONNECTED
    assert run.said == [OPEN, APP, INSTALL, ALLOW, TOKEN]
    assert run.store == {"app_token": site.app_token, "user_token": site.user_token}
    expected = ["Yours: Next"] if variant.get("two_step") else []
    assert run.browserd.pointed() == [*expected, "Yours: Create", "Yours: Generate Token", "Yours: Install",
                                      "Yours: Allow"]
    assert stage.pointer_label() is None   # and nothing is left drawn on the last page


def test_the_first_page_is_the_manifest_address_and_one_call_opens_it(make_stage, runs):
    stage = make_stage()
    run = runs(stage).start()
    until(lambda: stage.pointer_label() == "Yours: Create", 20, "the first pointer")
    opens = [c for c in run.browserd.calls if c["op"] == "open"]
    assert [c["args"] for c in opens] == [{"url": manifest.creation_url()}]
    if stage.kind == "fake":   # there the address is the real one, whole
        assert stage.opened() == [manifest.creation_url()]
        assert "manifest_json=" in stage.opened()[0] and len(stage.opened()[0]) > 900


def test_the_pattern_takes_a_token_whole_and_not_the_word_set_against_it():
    app, user = slack_pages.app_token(), slack_pages.user_token()
    for token, prefix in ((app, manifest.APP_TOKEN_PREFIX), (user, manifest.USER_TOKEN_PREFIX)):
        pattern = re.compile(slack_setup.token_pattern(prefix))
        for page in (f"Token: {token}", f"Token Generated {token}Copy", f"{token}\nDone", f"<{token}>", f"a {token}"):
            assert pattern.search(page).group(0) == token, page
        for page in ("no token here", prefix + "short", prefix + "x" * 5, token.replace(prefix, "xoxb-")):
            assert pattern.search(page) is None, page
    assert re.search(slack_setup.token_pattern(manifest.APP_TOKEN_PREFIX), user) is None   # the other kind is not it


@pytest.mark.parametrize("spelled", [
    lambda prefix: prefix * 30000,                 # one run of dashes and spellings, 150 thousand characters
    lambda prefix: (prefix + " ") * 25000,         # a spelling to each word
    lambda prefix: (prefix + "1-") * 40000,        # parts that are never long enough
    lambda prefix: prefix + "a-" * 70000,
    lambda prefix: prefix + "a" * 150000 + "A",
], ids=["one long run", "each word", "short parts", "dashes", "one long tail"])
def test_the_pattern_takes_no_longer_than_the_text_is_long(spelled):
    # It is run on the one thread that answers every caller of the browser service, over what a page holds: a page that
    # spells the prefix over and over must not hold the service for seconds (a lazy dash-spanning prefix took 13 s
    # over 80 thousand characters).
    for prefix in (manifest.APP_TOKEN_PREFIX, manifest.USER_TOKEN_PREFIX):
        pattern = re.compile(slack_setup.token_pattern(prefix))
        text = spelled(prefix)
        started = time.monotonic()
        pattern.search(text)
        assert time.monotonic() - started < 1.0, (prefix, len(text))
        real = slack_pages.app_token() if prefix == manifest.APP_TOKEN_PREFIX else slack_pages.user_token()
        assert pattern.search(text + " " + real).group(0) == real   # and the token after all of it is still found


def test_it_asks_for_nothing_that_acts_and_presses_nothing_itself(make_stage, runs, monkeypatch):
    monkeypatch.setattr(slack_setup, "CREATE_WAIT_S", 2.0)
    stage = make_stage()
    run = runs(stage).start()
    until(lambda: stage.pointer_label() == "Yours: Create", 20, "the first pointer")
    first = stage.url()
    result = run.done()
    assert result == {"ok": False, "say": "I did not see the app being made, so I stopped. Say connect Slack to try again."}
    assert set(run.browserd.ops()) <= THE_OPS
    assert stage.url() == first   # nothing was pressed, and no page was gone to
    if stage.kind == "fake":
        tab = next(iter(stage.chrome.tabs))
        assert {method for _tab, method in stage.chrome.commands} <= {"Page.enable", "Runtime.evaluate"}
        names = {overlay.parse(expression)[0] for _tab, expression in stage.chrome.evaluated}
        assert names <= {"read", "find", "point", "unpoint", "probe", "collect"}
        assert stage.chrome.page(tab).url == first


def test_the_tokens_go_through_take_and_nowhere_else(make_stage, runs, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    site = slack_pages.Site()
    stage = make_stage(site)
    run = runs(stage).start()
    the_whole_walk(stage)
    result = run.done()
    assert result == CONNECTED
    tokens = (site.app_token, site.user_token)
    assert run.store == {"app_token": tokens[0], "user_token": tokens[1]}
    # the browser service's own client of the connection service is the one that stored them, once each ...
    assert [(op, args["connection"], args["name"]) for op, args in run.taker.calls] == [
        ("store_secret", "slack:w1", "app_token"), ("store_secret", "slack:w1", "user_token")]
    # ... and the recipe's own only ever asked what became of the connection
    assert {op for op, _args in run.asked.calls} == {"connections"}
    assert "store_secret" not in run.browserd.ops()
    takes = [c for c in run.browserd.calls if c["op"] == "take"]
    stored = [c for c in takes if c["result"] == {"stored": True}]
    assert [c["args"]["into"] for c in stored] == [{"connection": "slack:w1", "name": "app_token"},
                                                   {"connection": "slack:w1", "name": "user_token"}]
    for c in takes:
        assert c["result"] in ({"stored": True}, {"stored": False, "found": False,
                                                  "why": "Nothing on the page looks like that."})
        assert set(c["args"]) == {"pattern", "into", "url"}          # the value is never an argument, only what to look for
        assert c["args"]["url"].startswith(f"/apps/{slack_pages.APP_ID}")
    assert re.fullmatch(stored[0]["args"]["pattern"], tokens[0]) and re.fullmatch(stored[1]["args"]["pattern"], tokens[1])
    out, err = capsys.readouterr()
    everything = json.dumps({"calls": run.browserd.calls, "said": run.said, "result": result,
                             "asked": run.asked.calls}, default=str) + out + err + caplog.text
    for token in tokens:
        assert token not in everything
        assert token.split("-")[-1] not in everything   # not even the long part of it


def test_pressing_in_another_order_still_ends_with_both_tokens(make_stage, runs):
    """Install is the first button on the page and Slack lets the person press it before the app token: the recipe
    takes the token that comes, points at what is left, and does not wait for the order it asked for."""
    site = slack_pages.Site()
    stage = make_stage(site)
    run = runs(stage).start()
    make_the_app(stage)
    until(lambda: stage.pointer_label() == "Yours: Generate Token", 20, "the pointer on the first token")
    stage.press("Install to Workspace")                              # not what was pointed at
    assert stage.press_pointed("Yours: Allow") == "Allow"            # and now it points at what is in front of them
    until(lambda: FIRST_MISSING in run.said, 20, "the second token to be taken")
    assert run.store == {"user_token": site.user_token}
    stage.goto("basic")                                              # back to Basic Information for the first
    make_the_app_token(stage)
    assert run.done() == CONNECTED
    assert run.said == [OPEN, APP, ALLOW, FIRST_MISSING, FIRST]
    assert run.store == {"app_token": site.app_token, "user_token": site.user_token}
    assert run.browserd.pointed() == ["Yours: Create", "Yours: Generate Token", "Yours: Allow", "Yours: Generate Token"]


def test_a_reloaded_page_gets_its_pointer_back_at_once(make_stage, runs):
    """A reload takes the pointer with it: it is drawn again on the next look, not when it would have timed out."""
    stage = make_stage()
    run = runs(stage).start()

    def back(label):
        until(lambda: not stage.is_marked_page() and stage.pointer_label() == label, 6,
              f"{label!r} to come back after the page was loaded again")
    until(lambda: stage.pointer_label() == "Yours: Create", 20, "the first pointer")
    stage.mark_page()
    stage.reload()
    back("Yours: Create")
    make_the_app(stage)
    until(lambda: stage.pointer_label() == "Yours: Generate Token", 20, "the pointer on the first token")
    stage.mark_page()
    stage.reload()
    back("Yours: Generate Token")
    make_the_app_token(stage)
    assert stage.press_pointed("Yours: Install") == "Install to Workspace"
    until(lambda: stage.pointer_label() == "Yours: Allow", 20, "the pointer on Allow")
    stage.mark_page()
    stage.reload()
    back("Yours: Allow")
    assert stage.press_pointed("Yours: Allow") == "Allow"
    assert run.done() == CONNECTED
    assert run.said == [OPEN, APP, INSTALL, ALLOW, TOKEN]   # each thing was said once, however often it was pointed


def test_a_pointer_the_person_has_not_lost_is_left_alone(make_stage, runs):
    """It is drawn again only when the page has lost it: not on every look (which would pull the page back under a
    person who scrolled)."""
    stage = make_stage()
    run = runs(stage).start()
    until(lambda: stage.pointer_label() == "Yours: Create", 20, "the first pointer")
    seen = len([c for c in run.browserd.calls if c["op"] == "find"])
    until(lambda: len([c for c in run.browserd.calls if c["op"] == "find"]) >= seen + 3, 10, "several looks")
    assert len([c for c in run.browserd.calls if c["op"] == "point"]) == 1


# -- going wrong --

def test_an_administrators_approval_wall_is_said_once_and_ends_it(make_stage, runs):
    site = slack_pages.Site(wall=True)
    stage = make_stage(site)
    run = runs(stage).start()
    make_the_app(stage)
    make_the_app_token(stage)
    # The app's own page talks about approval and administrators too; it is not a wall while there is a button.
    until(lambda: INSTALL in run.said, 20, "the first token to be taken")
    until(lambda: any(c["op"] == "read" and "administrator" in str(c.get("result")) for c in run.browserd.calls), 20,
          "the page to be read for a wall")
    assert run.thread.is_alive()
    assert stage.press_pointed("Yours: Install") == "Install to Workspace"
    result = run.done()
    assert result == {"ok": False, "say": WALL}
    assert (run.said + [result["say"]]).count(WALL) == 1
    assert run.said == [OPEN, APP, INSTALL]
    assert run.store == {"app_token": site.app_token}   # what was done stays done; the connection is left in setup
    assert "Yours: Allow" not in run.browserd.pointed()
    assert stage.pointer_label() is None


def test_the_next_run_after_the_wall_goes_on_in_the_app_it_made_not_in_a_second_one(make_stage, runs):
    site = slack_pages.Site(wall=True)
    stage = make_stage(site)
    first = runs(stage).start()
    make_the_app(stage)
    make_the_app_token(stage)
    until(lambda: INSTALL in first.said, 20, "the first token to be taken")
    assert stage.press_pointed("Yours: Install") == "Install to Workspace"
    assert first.done() == {"ok": False, "say": WALL}
    assert first.store == {"app_token": site.app_token}
    site.wall = False       # the administrator approved it
    second = runs(stage, store=first.store, with_service=False).start()   # the same browser service
    until(lambda: RESUME in second.said, 20, "the list of apps")
    assert [c["args"]["url"] for c in second.browserd.calls if c["op"] == "open"] == [manifest.apps_url()]   # not the
    # page that makes an app
    assert second.said == [RESUME]
    assert stage.press_pointed("Yours: Open") == "Bombadil"
    until(lambda: INSTALL_AGAIN in second.said, 20, "what is left to be said")
    install_the_app(stage)
    assert second.done() == CONNECTED
    assert second.said == [RESUME, INSTALL_AGAIN, ALLOW, TOKEN]
    assert second.store == {"app_token": site.app_token, "user_token": site.user_token}
    assert stage.pointer_label() is None


def test_a_run_that_has_only_the_second_token_asks_for_the_first_in_the_app_it_is_shown(make_stage, runs):
    site = slack_pages.Site()
    stage = make_stage(site)
    run = runs(stage, store={"user_token": site.user_token}).start()
    until(lambda: RESUME in run.said, 20, "the list of apps")
    assert stage.press_pointed("Yours: Open") == "Bombadil"
    until(lambda: APP in run.said, 20, "the first token to be asked for")
    make_the_app_token(stage)
    assert run.done() == CONNECTED
    assert run.store == {"app_token": site.app_token, "user_token": site.user_token}


def test_a_token_on_another_apps_page_is_not_taken_the_two_come_from_one_app(make_stage, runs):
    site = slack_pages.Site()
    stage = make_stage(site)
    run = runs(stage).start()
    make_the_app(stage)
    make_the_app_token(stage)
    until(lambda: INSTALL in run.said, 20, "the first token to be taken")
    stage.goto("other")      # the person opens another app of theirs by hand, which has a User OAuth Token on its page
    before = len(run.browserd.calls)
    until(lambda: len([c for c in run.browserd.calls[before:] if c["op"] == "take"]) >= 3, 10, "several looks")
    time.sleep(1.0)
    after = [c for c in run.browserd.calls[before:] if c["op"] == "take"]
    assert all(c["args"]["url"].startswith("/apps/A0ACME1234") for c in after)       # asked for in the app it began with
    assert not any((c.get("result") or {}).get("stored") for c in after)             # and none of them was taken
    assert "user_token" not in run.store and run.thread.is_alive()
    stage.goto("basic")      # and back to the one this was started for
    install_the_app(stage)
    assert run.done() == CONNECTED
    assert run.store == {"app_token": site.app_token, "user_token": site.user_token}
    assert site.other_user_token not in run.store.values()


@pytest.mark.parametrize("state, note, expected", [
    ("blocked", "", WALL),
    ("error", "Slack no longer accepts this connection.",
     "Slack would not take the connection: “Slack no longer accepts this connection.”"),
    ("error", "", "Slack would not take the connection."),
], ids=["blocked", "an error with its reason, quoted", "an error with none"])
def test_what_became_of_the_connection_is_what_it_ends_with(make_stage, runs, state, note, expected):
    stage = make_stage()
    run = runs(stage, state=state)
    run.asked.note = note
    run.start()
    the_whole_walk(stage)
    assert run.done() == {"ok": False, "say": expected}
    assert len(run.store) == 2


def test_a_connection_that_is_slow_to_answer_is_said_so(make_stage, runs, monkeypatch):
    monkeypatch.setattr(slack_setup, "CONNECTION_WAIT_S", 1.0)
    stage = make_stage()
    run = runs(stage, state="setup").start()
    the_whole_walk(stage)
    assert run.done() == {"ok": False, "say": "Both tokens are stored, but Slack has not answered yet. Look in "
                                              "Connected here in a moment."}


@pytest.mark.parametrize("how", ["no create", "no first token", "no install", "no second token"])
def test_a_page_that_never_comes_ends_it_in_a_sentence_after_its_time(make_stage, runs, monkeypatch, how):
    site = slack_pages.Site(blank=(how == "no create"), no_user_token=(how == "no second token"))
    for name, value in (("CREATE_WAIT_S", 1.5), ("APP_TOKEN_WAIT_S", 1.5), ("INSTALL_WAIT_S", 1.5),
                        ("USER_TOKEN_WAIT_S", 1.0), ("HINT_AFTER_S", 0.3)):
        monkeypatch.setattr(slack_setup, name, value)
    stage = make_stage(site)
    run = runs(stage).start()
    sentence = "{}I did not see {}, so I stopped. Say connect Slack to try again."
    if how == "no create":
        expected = sentence.format("", "the app being made")
    else:
        make_the_app(stage)
        if how == "no first token":
            expected = sentence.format("", "the first token")
        else:
            make_the_app_token(stage)
            if how == "no install":
                expected = sentence.format("The first token is stored. ", "the install finish")
            else:
                install_the_app(stage)
                expected = sentence.format("The first token is stored. ", "the second token")
    started = time.monotonic()
    result = run.done()
    assert result == {"ok": False, "say": expected}
    assert time.monotonic() - started < 15
    if how == "no create":   # nothing on the page to point at: it says what it is looking for, once
        assert run.said == [OPEN, "Look for Create on the page."]
    if how == "no second token":
        assert run.said[-1] == "Look for User OAuth Token on the page."
    assert stage.pointer_label() is None


def test_a_token_the_connection_service_will_not_take_ends_it_in_its_own_sentence(make_stage, runs):
    site = slack_pages.Site()
    stage = make_stage(site)
    run = runs(stage)
    run.taker.fail = connect_client.ConnectError("Slack would not take that one.", "bad_request")
    run.start()
    make_the_app(stage)
    make_the_app_token(stage)
    assert run.done() == {"ok": False, "say": "Slack would not take that one. Setup stopped."}
    assert run.store == {}


def test_a_token_in_an_error_text_does_not_reach_what_is_said(make_stage, runs, capsys):
    site = slack_pages.Site()
    stage = make_stage(site)
    run = runs(stage)
    run.taker.fail = RuntimeError(f"it broke on {site.app_token}")
    run.start()
    make_the_app(stage)
    make_the_app_token(stage)
    result = run.done()
    assert result == {"ok": False, "say": "Connections could not take it just now. Setup stopped."}
    out, err = capsys.readouterr()
    assert site.app_token not in json.dumps([run.said, result, run.browserd.calls], default=str) + out + err


def test_a_page_closed_in_the_middle_is_a_sentence_not_another_page(make_stage, runs):
    stage = make_stage()
    run = runs(stage).start()
    make_the_app(stage)
    until(lambda: stage.pointer_label() == "Yours: Generate Token", 20, "the pointer on the first token")
    stage.close_page()   # the browser has other pages open; none of them is the recipe's to read or point at
    assert run.done() == {"ok": False, "say": "That page is gone. Setup stopped."}
    assert run.said == [OPEN, APP]


def test_a_browser_that_is_not_there_is_a_sentence(fake_stage, runs):
    stage = fake_stage()

    def opener(url, devtools=None):
        raise RuntimeError("chromium is not installed")
    run = runs(stage, opener=opener).start()
    assert run.done() == {"ok": False, "say": "The browser is not installed on this computer. Setup stopped."}
    assert run.said == []


def test_a_browser_that_goes_away_in_the_middle_is_a_sentence(fake_stage, runs):
    stage = fake_stage()
    run = runs(stage).start()
    make_the_app(stage)
    until(lambda: stage.pointer_label() == "Yours: Generate Token", 20, "the pointer on the first token")
    stage.chrome.stop()
    assert run.done() == {"ok": False, "say": "The browser is not running. Setup stopped."}


def test_a_browser_service_that_is_not_running_is_a_sentence(home):
    said = []
    connect = FakeConnect()
    result = slack_setup.run_setup("slack:w1", said.append, connect=connect)
    assert result == {"ok": False, "say": "The browser service is not running. Setup stopped."}
    assert said == [] and [op for op, _args in connect.calls] == ["connections"]


def test_a_connection_service_that_is_not_running_stops_it_before_anything_opens(home):
    connect = FakeConnect()
    connect.fail = connect_client.ConnectUnavailable("no socket")
    result = slack_setup.run_setup("slack:w1", [].append, browserd=NoCalls(), connect=connect)
    assert result == {"ok": False, "say": "Connections are not running yet."}


def test_a_connection_that_is_not_there_stops_it_before_anything_opens(home):
    connect = FakeConnect()
    connect.id = "slack:w2"
    result = slack_setup.run_setup("slack:w1", [].append, browserd=NoCalls(), connect=connect)
    assert result == {"ok": False, "say": "That connection is not there any more."}


class NoCalls:
    """A service that must not be spoken to: anything it is asked is a failure."""

    BrowserdError = browserd.BrowserdError
    BrowserdUnavailable = browserd.BrowserdUnavailable

    def request(self, *args, **kw):
        raise AssertionError("the recipe spoke to a service it should not have")

    notify = request


@pytest.mark.parametrize("connection", ["mcp:linear", "../x", "", None, 5, "slack:"])
def test_only_a_slack_connection_is_set_up(home, connection):
    result = slack_setup.run_setup(connection, [].append, browserd=NoCalls(), connect=NoCalls())
    assert result == {"ok": False, "say": "That is not a Slack connection."}


@pytest.mark.parametrize("fails", [ValueError("it broke"), RuntimeError("the page said: Uncaught TypeError at x.js:1")])
def test_a_service_that_breaks_is_a_sentence_never_a_trace(home, fails):
    class Breaking(NoCalls):
        def request(self, op, *args, **kw):
            raise fails

        def notify(self, *args, **kw):
            raise fails
    result = slack_setup.run_setup("slack:w1", [].append, browserd=Breaking(), connect=FakeConnect())
    assert result == {"ok": False, "say": "The browser service did not answer as expected. Setup stopped."}
    assert str(fails) not in json.dumps(result)


class Odd(NoCalls):
    def request(self, op, *args, **kw):
        return ["not", "a", "dict"]   # an answer that is not what the service gives: it is not that service


def test_a_service_that_answers_in_another_shape_is_not_waited_for(home):
    result = slack_setup.run_setup("slack:w1", [].append, browserd=Odd(), connect=FakeConnect())
    assert result == {"ok": False, "say": "The browser service did not answer as expected. Setup stopped."}


# -- calling it off --

def test_cancelling_is_answered_within_a_second_and_takes_the_pointer_away(make_stage, runs):
    stage = make_stage()
    run = runs(stage).start()
    until(lambda: stage.pointer_label() == "Yours: Create", 20, "the first pointer")
    started = time.monotonic()
    run.cancel.set()
    run.thread.join(5)
    elapsed = time.monotonic() - started
    assert not run.thread.is_alive() and elapsed < 1.0, elapsed
    assert run.result == {"ok": False, "say": STOPPED}
    assert stage.pointer_label() is None
    assert run.said == [OPEN] and nothing_stored(run)


def test_cancelling_in_the_token_stage_is_answered_within_a_second_too(make_stage, runs):
    stage = make_stage()
    run = runs(stage).start()
    make_the_app(stage)
    until(lambda: stage.pointer_label() == "Yours: Generate Token", 20, "the pointer on the first token")
    started = time.monotonic()
    run.cancel.set()
    run.thread.join(5)
    assert not run.thread.is_alive() and time.monotonic() - started < 1.0
    assert run.result == {"ok": False, "say": STOPPED}
    assert stage.pointer_label() is None


def test_cancelled_before_it_starts_it_opens_nothing(home):
    result = slack_setup.run_setup("slack:w1", [].append, lambda: True, browserd=NoCalls(), connect=FakeConnect())
    assert result == {"ok": False, "say": STOPPED}


@pytest.mark.parametrize("stuck", ["probe", "collect"], ids=["a look for the next page", "a token being taken"])
def test_cancelling_a_call_that_does_not_answer_is_answered_within_a_second(fake_stage, runs, monkeypatch, stuck):
    """A page that does not answer (a dialog open, a hung script): the recipe's own call to the browser service is
    waiting on it, and cancelling does not wait for that. A token being taken is given a moment, not ten seconds."""
    monkeypatch.setattr(cdp, "EVALUATE_S", 3.0)
    stage = fake_stage()
    run = runs(stage).start()
    if stuck == "collect":
        make_the_app(stage)
        until(lambda: APP in run.said, 20, "the app to be made")
    else:
        until(lambda: stage.pointer_label() == "Yours: Create", 20, "the first pointer")
    stage.chrome.tab(next(iter(stage.chrome.tabs))).stall = True
    until(lambda: overlay.parse(stage.chrome.evaluated[-1][1])[0] == stuck, 10, f"a {stuck} to be asked")
    started = time.monotonic()
    run.cancel.set()
    run.thread.join(5)
    assert not run.thread.is_alive() and time.monotonic() - started < 1.0
    assert run.result == {"ok": False, "say": STOPPED}


# -- the words --

def test_what_it_says_is_plain_and_fits_a_notice():
    sentences = [getattr(slack_setup, name) for name in dir(slack_setup) if name.startswith("SAY_")]
    assert len(sentences) >= 8
    for sentence in sentences:
        assert sentence.endswith(".") and "!" not in sentence and "  " not in sentence and len(sentence) <= 200, sentence
