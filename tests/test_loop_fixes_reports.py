"""What a report carries, and what is counted: the cases the first tests did not plant.

A kept log line loses links, addresses and keys; a path with a space in its name goes whole; an app's
own log is never in a report; a provider's echo of their words goes however it is quoted; a button's own
word does not garble a finding; a secret they typed is never counted; the browser panel gets the whole
issue link; a closed issue is not "already reported"; and what they send is the text they read.
"""

import http.server
import json
import threading
import time
import types
import urllib.parse
from pathlib import Path
from typing import ClassVar

import pytest

from bombadil.loop import findings, habits, probes, report, service
from bombadil.loop import store as loop_store
from bombadil.loop.findings import Finding, FindingsStore
from bombadil.loop.ledger import Request
from bombadil.loop.probes import Observation, run_probe

NOW = time.mktime((2026, 9, 14, 12, 0, 0, 0, 0, -1))
DAY = 86400.0
T0 = 1_790_680_000.0
SHARE = Path(__file__).resolve().parents[1] / "share" / "apps" / "noticed"


@pytest.fixture(autouse=True)
def his_machine(home, monkeypatch):
    monkeypatch.setenv("HOME", "/home/alice")
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("LOGNAME", "alice")
    monkeypatch.setenv("BOMBADIL_STATE", str(home / "state"))
    monkeypatch.delenv("BOMBADIL_LOOP", raising=False)


@pytest.fixture
def store(home):
    s = FindingsStore()
    yield s
    s.close()


def noon(day=0) -> float:
    return time.mktime((2026, 9, 14 + day, 12, 0, 0, 0, 0, -1))


def texts(rep) -> str:
    """Everything a report can be read as, and the links made from it, in one string."""
    link = report.issue_url(rep, copy=None)
    return "\n".join([rep.render(), json.dumps(report.preview(rep).to_dict()), json.dumps(rep.to_dict()),
                      link.url, urllib.parse.unquote(link.url)])


def a_finding(component="hypr", **kw) -> Finding:
    return Finding(**{"fp": f"{component}:a-rule:3c91a0", "component": component, "rule": "a-rule",
                      "title": "The bar lost its connection.", "expected": "the bar stays connected",
                      "observed": "the bar was not connected", "first": NOW, "last": NOW + 60, "n": 1,
                      "days": 1, **kw})


# -- a kept log line (findings 0, 32) --

def fake_key() -> str:
    return "sk-" + "ant-" + "api03-" + "A1b2C3d4" * 3


@pytest.mark.parametrize("line, gone", [
    ("request failed for https://api.example.com/v1/me?token=abcd1234secretXYZ from now", ["example", "abcd1234"]),
    ("user bob@example.com denied", ["bob", "example.com"]),
    (f"denied, Bearer {fake_key()} failed", ["api03", "A1b2C3d4"]),
    (f"Authorization: Bearer {'x' * 8}9{'y' * 8} rejected", ["xxxxxxxx9"]),
    ("connect to 10.1.2.3 refused", ["10.1.2.3"]),
    ("connect to [2001:db8::ff00:42:8329] and ::1 refused", ["2001", "ff00", "::1"]),
    ("login with password=hunter2 failed", ["hunter2"]),
    ("api_key: " + "q" * 6 + "7" * 6 + " was refused", ["qqqqqq"]),
    ("saw 9f8e7d6c-1234-4abc-8def-0123456789ab and " + "Zz9" * 11, ["9f8e7d6c", "Zz9Zz9"]),
    ("GET api.example.com/v1/me?token=abcd1234secretXYZ&x=1 failed", ["abcd1234", "x=1"]),
])
def test_a_kept_log_line_loses_links_addresses_emails_and_keys(line, gone):
    f, bundle = a_finding(), {"log": [line]}
    rep = report.build(f, bundle)
    assert len(rep.log) == 1
    for word in gone:
        assert word not in texts(rep), word


def test_what_a_line_says_besides_stays_readable():
    line = "QObject::connect: Cannot connect (null)::foo at 10:02:11, mcp__bombadil-os__create_app, Hyprland 0.56.2"
    assert report._line(line) == line
    assert report._line("failed to reach <https://x.test/a?b=c> twice") == "failed to reach <<url>> twice"


# -- a path with a space in its name (findings 0, 32) --

PATHS = [
    ("could not open /home/alice/My Documents/tax return 2024.pdf", ["Documents", "tax", "2024"]),
    ("ENOENT: no such file or directory, open '/home/alice/Documents/Tax Return 2025.pdf'", ["Tax", "2025"]),
    ("file:///home/alice/x y/z.txt not found", ["y/z"]),
    ("error reading /mnt/usb/Family Photos/IMG 1.jpg", ["Photos", "IMG", "jpg"]),
    ("error reading '/mnt/usb/Family Photos/IMG 1.jpg'", ["Photos", "IMG", "jpg"]),
    ("cannot open ~/Health Records/2024 jane.txt: permission denied", ["Records", "jane"]),
]


@pytest.mark.parametrize("line, gone", PATHS)
def test_a_path_with_a_space_in_its_name_is_taken_whole(line, gone):
    for scrubbed in (report._line(line), probes.scrub_text(line), findings._redact(line, [])):
        for word in gone:
            assert word not in scrubbed, (scrubbed, word)
    for word in gone:
        assert word not in probes.strip_line(line)


def test_the_words_after_a_path_that_are_an_errors_own_survive_its_colon():
    line = "cannot open /home/alice/My Docs/a.txt: permission denied"
    assert report._line(line) == "cannot open ~: permission denied"
    assert probes.scrub_text(line) == "cannot open <path>: permission denied"
    assert probes.scrub_text("failed 12:30: /mnt/usb/A B/c.txt") == "failed 12:30: <path>"


def test_a_system_path_stays_but_a_path_after_it_does_not():
    out = report._line("load /usr/lib/qt6/plugins/x.so and /mnt/data/Tax Papers/2025.pdf failed")
    assert out == "load /usr/lib/qt6/plugins/x.so and <path>"


# -- an app's own log is not in a report (findings 0, 28) --

APP_LINE = ("FATAL: could not parse Maria's tax-return.csv, balance 1523.20, in "
            "/home/alice/My Documents/Finance 2024/x.csv (user bob@example.com from 10.1.2.3)")


def app_health(store, line, now=NOW):
    log = ["--- 10:00:01 bombadil-app run notes (pid 4242)", "loaded the notes", line]
    obs = Observation(apps=[{"ok": True, "running": True, "log": log}], now=now)
    first = run_probe("app-health", obs)[0]
    return store.record_retry(first, run_probe("app-health", obs, retried=True), now, obs=obs, log=log)


def test_an_apps_fatal_line_is_in_no_sentence_and_its_log_is_not_in_the_report(store):
    found = app_health(store, APP_LINE)
    assert found.observed == "an app's log has a fatal line"
    rep = report.build(found)
    assert rep.log == () and "Log:" not in rep.render()
    assert not any("of the log" in goes for goes in report.preview(rep).goes)
    everything = texts(rep)
    for word in ("Maria", "maria", "tax", "Finance", "balance", "bob", "10.1.2.3", "1523", "fatal line:"):
        assert word not in everything, word
    # the app's log stays in the evidence on this machine, which is where the maintainers cannot see it
    kept = json.loads((Path(found.evidence) / "evidence.json").read_text())
    assert kept["log"] and "fp_line" not in json.dumps(kept)


def test_the_log_of_the_other_parts_is_still_kept_in_a_report():
    for component in ("hypr", "agentd", "bar"):
        rep = report.build(a_finding(component), {"log": ["hyprland: error: it broke", "all fine"]})
        assert rep.log == ("hyprland: error: it broke",)


def test_two_fatal_lines_are_two_findings_and_the_same_line_with_other_numbers_is_one(store):
    one = app_health(store, "FATAL: segmentation fault in libqtquick at 0x55a1c0", noon())
    same = app_health(store, "FATAL: segmentation fault in libqtquick at 0x7ffc20", noon(1))
    other = app_health(store, "FATAL: out of memory loading the model", noon(2))
    assert one.fp == same.fp != other.fp and same.n == 2
    assert one.observed == other.observed == "an app's log has a fatal line"


# -- a provider's echo of their words, however it is quoted (finding 4) --

@pytest.mark.parametrize("prompt, error, gone", [
    ("write to dr smith\nabout jane doe's prescription", "bad request: write to dr smith about jane doe's prescription",
     ["smith", "jane"]),
    ("it's the acme \"merger\" memo please", 'request failed: "it\'s the acme \\"merger\\" memo please"',
     ["acme", "merger"]),
    ("summarise the quarterly acme merger memo", "Error: prompt too long: summarise the quarterly acme merger",
     ["acme", "quarterly"]),
    ("Write To Dr Smith About Jane", "invalid input: write to dr smith about jane", ["smith", "jane"]),
    ("dear dr smith\nplease renew my prescription", "codex: invalid input: dear dr smith\nplease renew", ["smith"]),
    ("tell my lawyer about the acme merger plans", "bad: " + "padding " * 14 + "tell my lawyer about the acme", ["acme"]),
])
def test_an_echo_of_the_prompt_is_taken_out_whatever_its_shape(prompt, error, gone):
    out = probes._redacted(error, [prompt])
    assert "…" in out
    for word in gone:
        assert word not in out.lower(), (out, word)


def test_only_the_echo_goes_so_the_error_still_says_what_went_wrong():
    out = probes._redacted('{"error": "invalid_request", "message": "bad input: show my acme merger memo"}',
                           ["show my acme merger memo"])
    assert out == '{"error": "invalid_request", "message": "bad input: …"}'


def test_a_short_ask_is_taken_out_as_a_whole_word_only():
    assert probes._redacted("cannot reboot now, rebooting later", ["reboot"]) == "cannot … now, rebooting later"


def test_the_loops_own_rows_are_not_asks_that_a_provider_could_echo():
    local = {"t": T0 - 5, "kind": "local", "prompt": "undo", "action": "undo", "result": "ok", "ok": True}
    turn = {"t": T0 - 100, "prompt": "show me how full my disk is", "result": "could not undo the change",
            "ok": False, "id": "a-1", "provider": "claude", "started": T0 - 110, "seconds": 10.0}
    [red] = [r for r in run_probe("turn-failed", Observation(ledger=[turn, local])) if r.ok is False]
    assert red.observed == "a turn ended with an error: could not undo the change"


def test_a_multi_line_echo_does_not_reach_the_report(store):
    asked = "write to dr smith\nabout jane doe's prescription renewal"
    row = {"t": NOW - 100, "prompt": asked, "result": "", "ok": False, "id": "a-1", "provider": "claude",
           "started": NOW - 110, "seconds": 10.0, "origin": "typed"}
    obs = Observation(ledger=[row], turn_errors=[{"turn": "a-1", "t": NOW - 100,
                                                  "text": "invalid input: write to dr smith about jane"}], now=NOW)
    [red] = [r for r in run_probe("turn-failed", obs) if r.ok is False]
    for day in range(3):                       # friction counts at three sightings on two days
        found = store.record(probes.Result(**{**red.__dict__, "at": noon(day)}), noon(day), obs=obs)
    assert found is not None
    everything = texts(report.build(found))
    assert "smith" not in everything and "jane" not in everything and "invalid input" in everything


# -- a button's own word does not garble a finding (finding 26) --

def turn_row(tid, t, **kw):
    return {"t": t, "prompt": "show me how full my disk is", "result": "You have 212 GB free.", "ok": True,
            "snapshot": 41, "provider": "claude", "stopped": False, "id": tid, "started": t - 10, "seconds": 10.0,
            "origin": "typed", "tools": {"n": 1, "names": ["Bash"]}, "v": 2, **kw}


def local_row(verb, t, **kw):
    return {"t": t, "kind": "local", "prompt": verb, "action": verb, "result": "done", "ok": True, "v": 2,
            "verb": verb, "via": "typed", **kw}


def three_days(store, probe_id, ledger_of):
    found = None
    for day in range(3):
        obs = Observation(ledger=ledger_of(noon(day)), now=noon(day) + 200)
        for red in (r for r in run_probe(probe_id, obs) if r.ok is False):
            found = store.record(red, noon(day) + 200, obs=obs) or found
    return found


def test_an_undone_turn_and_a_stopped_one_read_as_what_they_are(store):
    undone = three_days(store, "undo-soon", lambda t: [turn_row("a-1", t, result="done"),
                                                       local_row("undo", t + 30, of="a-1")])
    assert undone.observed == "a turn was undone within 60 seconds of finishing"
    assert undone.title == "You undid a change within a minute"
    stopped = three_days(store, "stop-soon", lambda t: [turn_row("a-1", t, seconds=3.0, stopped=True, ok=None),
                                                       local_row("stop", t + 3, of="a-1")])
    assert stopped.observed == "a turn was stopped within 5 seconds of starting"
    assert stopped.title == "You stopped a turn within seconds"
    assert "…" not in report.build(undone).render() + report.build(stopped).render()


def test_a_short_secret_is_only_taken_out_as_a_whole_word():
    assert findings._redact("a turn was undone", ["undo"]) == "a turn was undone"
    assert findings._redact("a turn was stopped (stop)", ["stop"]) == "a turn was stopped (…)"
    assert findings._redact("saw hunter2x and hunter2.", ["hunter2"]) == "saw hunter2x and …."
    assert findings._redact("saw xhunter2 and (hunter2)", ["hunter2"]) == "saw xhunter2 and (…)"
    long = "the quarterly acme merger memo"
    assert findings._redact(f"re:{long}!", [long]) == "re:…!"                  # a long one goes wherever it is


def test_the_probes_own_title_and_expected_are_never_his_words(store):
    obs = Observation(ledger=[{"t": 1, "prompt": "the bar is not connected to the shell", "result": "", "id": "a-1"}])
    r = probes.Result(False, "bar-alive", "bar", "bar-alive", "the bar is not connected to the shell",
                      "the bar is not connected to the shell", {}, None, "event", "The bar is not connected to the shell.")
    found = store.record(r, NOW, obs=obs)
    assert found.title == "The bar is not connected to the shell." and found.expected == r.expected
    assert found.observed == "…"           # what it saw is still taken out: that is where their words would be


def test_a_local_rows_own_word_is_not_a_secret_but_what_he_typed_to_it_is():
    obs = Observation(ledger=[local_row("undo", 1), {**local_row("app", 2), "prompt": "open the tax notes", "action": "app"}])
    secrets = findings._secrets(obs, None)
    assert "undo" not in secrets and "open the tax notes" in secrets


# -- typed secrets that avoid the keyword list (finding 1) --

def fake(*parts) -> str:
    return "".join(parts)


SECRET_ASKS = [
    "my password is hunter2", fake("sk-", "ant-", "api03-", "ABCDEFGHIJKLMNOP", "QRSTUVWXYZ012345"),
    "login as bob pass 12345", "my pin is 4921", "ssn 123-45-6789", "sudo password swordfish",
    "my bank login is bob / s3cret", fake("here: AKIA", "IOSFODNN7EXAMPLE"), fake("ghp_", "a1B2" * 6),
    fake("xoxb-", "123456789012-abcdefghij"), fake("eyJhbGciOiJIUzI1NiJ9", ".", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", ".sig"),
    "pay with 4111 1111 1111 1111", "key " + "a1" * 20, "the passcode is 8472",
]
HARMLESS_ASKS = [
    "sign in to my email", "log in to github", "pin this window", "pin the browser to the right",
    "show me my pinned apps", "unpin everything", "pass the file to the printer", "the login page is slow",
    "show me my passwords", "open the password manager", "sign in and open the password manager",
    "show me files from 2024-06-14", "summarise the sk-learn notes", "what is my battery at",
]


def ask(text, **kw):
    return Request(id="r1", t=T0, day="2026-09-27", text=text, seconds=5.0, ended=T0 + 5, ok=True, **kw)


@pytest.mark.parametrize("text", SECRET_ASKS)
def test_a_secret_he_types_is_never_counted_or_kept(text):
    assert habits.is_private_text(text)
    assert habits.counted(ask(text)) == (False, "private")
    assert "private" not in loop_store._TEXT_KEPT       # a private ask's words are not written to loop.db


@pytest.mark.parametrize("text", HARMLESS_ASKS)
def test_the_words_login_pin_and_passwords_alone_still_count(text):
    assert not habits.is_private_text(text)
    assert habits.counted(ask(text)) == (True, "")


# -- the Send card says what is true (finding 7) --

def test_the_send_card_does_not_say_nothing_leaves_when_the_link_carries_the_report():
    card = (SHARE / "SendCard.qml").read_text()
    assert "Nothing leaves this machine" not in card
    assert "sends it to GitHub as part of the address; nothing is posted until you press Submit" in card
    row = service.LoopService._found_row(findings.Finding(**{**a_finding().to_dict(), "state": "reported"}))
    assert "sends it to GitHub" in row["what"] and "nothing is posted until you press Submit" in row["what"]


# -- the browser panel gets the whole link (finding 25) --

class Chromium(http.server.BaseHTTPRequestHandler):
    """/json/new as Chromium has it: PUT only, the query cut at its first "&", then unescaped."""

    seen: ClassVar[list] = []

    def do_PUT(self):
        path, _, query = self.path.partition("?")
        tab = {"id": "T1", "type": "page", "url": urllib.parse.unquote(query.split("&")[0])}
        Chromium.seen.append((self.command, path, tab["url"]))
        data = json.dumps(tab).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class NoPanel:
    available = False


def test_the_report_body_and_label_reach_the_tab_chromium_opens(monkeypatch):
    Chromium.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), Chromium)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(report, "BROWSER_DEBUG_PORT", server.server_address[1])
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")        # the debugging port is never asked through one
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    try:
        rep = report.build(a_finding(), {"log": ["error: a & b = c?d#e"]})
        url = report.issue_url(rep, copy=None).url
        assert report.open_issue_page(url, NoPanel(), xdg=lambda u: 1 / 0) == "panel"
    finally:
        server.shutdown()
    assert Chromium.seen == [("PUT", "/json/new", url)]
    opened = urllib.parse.urlsplit(Chromium.seen[0][2])
    query = urllib.parse.parse_qs(opened.query)
    assert query["body"] == [rep.body()] and query["labels"] == [report.LABELS] and query["title"] == [rep.title]


def test_a_browser_that_does_not_answer_is_not_a_page_that_opened(monkeypatch):
    monkeypatch.setattr(report, "BROWSER_DEBUG_PORT", 9)         # nothing listens there
    assert report._put_new_tab("https://github.com/x/y/issues/new?title=a&body=b") is False


# -- a closed issue is not "already reported" (finding 31) --

def test_a_bug_that_came_back_after_its_fix_is_not_already_reported():
    def hit(*items):
        return lambda url, timeout: {"items": [{"title": "Bar lost it [fp 3c91a0]", **i} for i in items]}

    assert report.already_reported("hypr:a-rule:3c91a0", fetch=hit({"number": 12, "state": "closed"})) is None
    assert report.already_reported("hypr:a-rule:3c91a0", fetch=hit({"number": 12, "state": "closed"},
                                                                    {"number": 15, "state": "open"})) == 15
    assert report.already_reported("hypr:a-rule:3c91a0", fetch=hit({"number": 15, "state": "open"})) == 15


# -- the flaky-probe report says which build (finding 33) --

def test_a_flaky_probe_finding_carries_the_versions_it_was_seen_on(store):
    obs = Observation(clients=[], monitors=[], activewindow={})
    first = probes.Result(False, "drawer-focus", "hypr", "drawer-focus", "x", "the drawer is open", {}, 0.5, "invariant", "T.")
    green = [probes.Result(True, "drawer-focus", "hypr", "drawer-focus")]
    versions = {"build": "abc1234", "Hyprland": "0.56.2", "machine": "VM"}
    found = None
    for day in range(3):
        found = store.record_retry(first, green, noon(day), obs=obs, versions=versions) or found
    assert found.rule == "flaky-probe" and found.fp.startswith("loop:flaky-probe:")
    rep = report.build(found)
    assert rep.build == "abc1234" and "build abc1234" in rep.seen and ("Hyprland", "0.56.2") in rep.versions
    bundle = json.loads((Path(found.evidence) / "evidence.json").read_text())
    assert set(bundle) <= {"fp", "probe", "component", "rule", "kind", "at", "expected", "observed", "evidence",
                           "command", "versions"}                  # nothing of the failing window comes with it


def test_a_flaky_probe_without_versions_still_gets_its_report(store):
    first = probes.Result(False, "drawer-focus", "hypr", "drawer-focus", "x", "the drawer is open", {}, 0.5, "invariant", "T.")
    green = [probes.Result(True, "drawer-focus", "hypr", "drawer-focus")]
    found = None
    for day in range(3):
        found = store.record_retry(first, green, noon(day), versions="not a dict") or found
    assert found is not None and report.build(found).build == ""


# -- what they send is what they read --

def held_finding(store):
    result = probes.Result(False, "bar-alive", "bar", "bar-alive", "the bar answers", "the bar did not answer",
                           {}, None, "event", "The bar lost its connection to Bombadil.")
    found = store.record(result, NOW, versions={"build": "abc1234"})
    rep = report.build(found)
    report.hold(rep)
    store.mark(found.fp, "reported")
    return store.get(found.fp), rep


def test_the_held_report_is_what_is_shown_and_sent_whatever_was_seen_since(store):
    found, rep = held_finding(store)
    assert report.held(found.fp) == rep
    assert report.shown(found).render() == rep.render()
    # the problem is seen again after they read the report, with another build
    store.record(probes.Result(False, "bar-alive", "bar", "bar-alive", "the bar answers", "the bar did not answer",
                               {}, None, "event", "The bar lost its connection to Bombadil.", noon(1)),
                 noon(1), versions={"build": "newer99"})
    later = store.get(found.fp)
    assert later.n == 2 and "newer99" in report.build(later).render()      # a new report would be different
    assert report.shown(later).render() == rep.render() and "newer99" not in report.shown(later).render()
    assert report.preview(report.shown(later)).text == rep.render()
    stub = types.SimpleNamespace(_findings=store)
    assert service.LoopService._w_reported(stub, found.fp).render() == rep.render()
    link = report.issue_url(service.LoopService._w_reported(stub, found.fp), copy=None)
    assert urllib.parse.unquote(link.url).count("build abc1234") == 1 and "newer99" not in link.url


def test_a_report_that_was_not_held_is_built_from_the_evidence_as_it_is(store):
    result = probes.Result(False, "bar-alive", "bar", "bar-alive", "x", "y", {}, None, "event", "The bar is gone.")
    found = store.record(result, NOW, versions={"build": "abc1234"})
    assert report.held(found.fp) is None
    assert report.shown(found).render() == report.build(found).render()
    store.mark(found.fp, "reported")                                       # reported, but the copy is gone
    assert report.shown(store.get(found.fp)).render() == report.build(store.get(found.fp)).render()


def test_a_held_copy_that_cannot_be_read_is_not_used(store):
    found, _ = held_finding(store)
    copy = Path(found.evidence) / "report.json"
    for broken in ("", "{", "[]", json.dumps({"fp": found.fp}), json.dumps({**json.loads(copy.read_text()), "x": 1})):
        copy.write_text(broken)
        assert report.held(found.fp) is None
        assert report.shown(found).render() == report.build(found).render()
