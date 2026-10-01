"""Findings: one per problem, counted when it counts, written down without anything of their."""

import dataclasses
import json
import re
import time
from pathlib import Path

import pytest

from bombadil.loop import findings, probes
from bombadil.loop.findings import Finding, FindingsStore, fingerprint, fingerprint_of
from bombadil.loop.probes import Observation, Result, run_probe

FIXTURES = Path(__file__).parent / "fixtures" / "loop" / "probes"
HOUR, DAY = 3600.0, 86400.0


def noon(day=0) -> float:
    """Local noon, so that "on two days" does not depend on where the tests run."""
    return time.mktime((2026, 9, 14 + day, 12, 0, 0, 0, 0, -1))


@pytest.fixture
def store(home, monkeypatch):
    monkeypatch.delenv("BOMBADIL_LOOP", raising=False)
    s = FindingsStore()
    yield s
    s.close()


def fixture(name) -> Observation:
    return Observation(**json.loads((FIXTURES / f"{name}.json").read_text()))


def result(kind="invariant", observed="2 floating apps share one position and size on a workspace",
           id="apps-stacked", component="hypr", at=None, retry_after=None, **evidence) -> Result:
    return Result(False, id, component, id, "each app has a spot of its own", observed, evidence, retry_after, kind,
                  "New apps open on top of each other.", at)


def friction(at, observed="a turn was undone within 60 seconds of finishing") -> Result:
    return result("friction", observed, "undo-soon", "turns", at=at)


def evidence_text(fp_dir: str) -> str:
    return "\n".join(p.read_text() for p in sorted(Path(fp_dir).rglob("*")) if p.is_file())


# -- the fingerprint --

def test_a_fingerprint_reads_component_rule_and_a_short_hash():
    fp = fingerprint("hypr", "apps-stacked", "2 floating apps share one position")
    assert re.fullmatch(r"hypr:apps-stacked:[0-9a-f]{6}", fp)
    assert fp == fingerprint("hypr", "apps-stacked", "2 floating apps share one position")
    assert fp != fingerprint("hypr", "apps-stacked", "a floating app sits over another")
    assert fp != fingerprint("bar", "apps-stacked", "2 floating apps share one position")
    assert fp != fingerprint("hypr", "window-oversize", "2 floating apps share one position")


def test_paths_pids_numbers_hex_ids_and_times_are_not_part_of_what_a_problem_is():
    def same(a, b):
        assert fingerprint("c", "r", a) == fingerprint("c", "r", b), (a, b)
    same("Config error in file /home/user/.config/hypr/hyprland.lua at line 12: no such function",
         "Config error in file /home/other/x/hyprland.lua at line 97: no such function")
    same("agentd[4242] died", "agentd[18] died")
    same("window 0x55a1c0 is outside", "window 0x7f00aa12 is outside")
    same("request id 9f8e7d6c-1234-4abc-8def-0123456789ab failed", "request id 00000000-1111-2222-3333-444444444444 failed")
    same("2 floating apps share one position", "17 floating apps share one position")
    same("fatal at 10:02:11.482 in libQt6Quick", "fatal at 23:59:01 in libQt6Quick")
    same("Timed out after 12.5 s", "timed out after 3 s")
    assert fingerprint("c", "r", "crashed (SIGSEGV)") != fingerprint("c", "r", "crashed (SIGABRT)")


def test_only_the_first_line_of_what_was_observed_is_the_problem():
    a = "monitor Virtual-1 is 640 logical pixels wide\n{'name': 'Virtual-1'}"
    b = "\n\nmonitor Virtual-1 is 800 logical pixels wide\nsomething else entirely"
    assert fingerprint("hypr", "monitor-narrow", a) == fingerprint("hypr", "monitor-narrow", b)
    assert fingerprint_of(result(observed=a)) == fingerprint("hypr", "apps-stacked", a)


# -- when a sighting counts --

def test_an_invariant_seen_once_is_not_counted_until_the_retry_is_red_too(store):
    first = result(retry_after=0.5)
    assert store.record(first, noon()) is None
    assert store.open_findings() == [] and store.pending() == []
    found = store.record_retry(first, result(retry_after=None), noon() + 0.5)
    assert found is not None and found.n == 1 and found.state == "open"
    assert store.get(found.fp) == found and store.open_findings() == [found]


def test_the_retry_protocol_runs_a_probe_twice_and_counts_what_is_red_both_times(store):
    obs = fixture("apps-stacked-bad")
    [first] = run_probe("apps-stacked", obs)
    assert first.retry_after == 0.5 and store.record(first, noon(), obs=obs) is None
    found = store.record_retry(first, run_probe("apps-stacked", obs, retried=True), noon() + 0.5, obs=obs)
    assert found.fp.startswith("hypr:apps-stacked:") and found.probe == "apps-stacked"
    assert found.title == "New apps open on top of each other"
    assert found.expected and found.observed == "2 floating apps share one position and size on a workspace"
    assert (found.component, found.rule, found.kind, found.fixable, found.n, found.days) == \
        ("hypr", "apps-stacked", "invariant", False, 1, 1)
    assert found.first == found.last == noon() + 0.5
    assert Path(found.evidence, "evidence.json").is_file()


def test_a_retry_that_comes_back_green_is_a_flip_not_a_finding(store):
    obs, fixed = fixture("apps-stacked-bad"), fixture("apps-stacked-fixed")
    [first] = run_probe("apps-stacked", obs)
    assert store.record_retry(first, run_probe("apps-stacked", fixed, retried=True), noon()) is None
    assert store.open_findings() == [] and store.quarantined(noon()) == set()
    # A retry that could not be checked says nothing either way.
    assert store.record_retry(first, run_probe("apps-stacked", Observation(), retried=True), noon()) is None
    assert store.record_retry(first, [], noon()) is None
    assert store.conn.execute("SELECT COUNT(*) FROM probe_flips").fetchone()[0] == 1


def test_a_retried_result_counts_when_recorded_directly(store):
    obs = fixture("drawer-bad")
    [again] = run_probe("drawer-focus", obs, retried=True)
    assert again.retry_after is None and store.record(again, noon()) is not None


def test_a_crash_and_an_event_count_on_their_first_sighting(store):
    crash = result("crash", "agentd crashed (SIGSEGV)", "coredump", "agentd", at=noon() - 600)
    found = store.record(crash, noon())
    assert found.n == 1 and found.first == noon() - 600 and found.component == "agentd"
    event = result("event", "a summon got no focus_ack within 1.5 s", "summon-focus", "bar", at=noon() - 5)
    assert store.record(event, noon()) is not None
    drift = result("drift", "claude sent message types Bombadil does not know: hologram", "provider-drift",
                   "providers", at=noon() - 5)
    assert store.record(drift, noon()) is not None
    assert len(store.open_findings()) == 3


def test_green_and_not_checked_results_write_nothing(store):
    assert store.record(Result(True, "x", "hypr", "x"), noon()) is None
    assert store.record(Result(None, "x", "hypr", "x", observed="not checked: no clients"), noon()) is None
    assert store.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == 0


# -- repeats --

def test_a_state_seen_on_every_run_for_an_hour_is_one_sighting(store):
    first = store.record(result(), noon())
    assert first.n == 1
    for minute in range(1, 61):
        assert store.record(result(), noon() + minute * 60) is None            # the same episode
    again = store.get(first.fp)
    assert again.n == 1 and again.last == noon() + 3600 and again.first == noon()


def test_the_same_state_after_a_gap_is_another_time_and_another_day_is_another_day(store):
    fp = store.record(result(), noon()).fp
    assert store.record(result(), noon() + 2 * HOUR).n == 2
    third = store.record(result(), noon() + DAY)
    assert (third.n, third.days, third.first, third.last) == (3, 2, noon(), noon() + DAY)
    assert store.record(result(observed="2 floating apps share one position and size on a workspace"),
                        noon() + DAY + 10 * 60) is None
    assert store.get(fp).n == 3


def test_the_same_past_fact_is_one_sighting_however_often_it_is_read(store):
    crash = result("crash", "quickshell crashed (SIGSEGV)", "coredump", "bar", at=noon() - 90)
    assert store.record(crash, noon()).n == 1
    assert store.record(crash, noon() + 60) is None and store.record(crash, noon() + 3 * DAY) is None
    later = store.record(dataclasses.replace(crash, at=noon() - 30), noon() + 60)
    assert later.n == 2 and later.days == 1
    assert store.get(later.fp).first == noon() - 90


def test_different_problems_are_different_findings(store):
    a = store.record(result(), noon())
    b = store.record(result(observed="a floating app sits over another"), noon())
    c = store.record(result(id="window-oversize", observed="2 floating apps share one position and size on a workspace"),
                     noon())
    assert len({a.fp, b.fp, c.fp}) == 3 and len(store.open_findings()) == 3


# -- friction --

def test_a_friction_counts_at_three_sightings_on_two_days(store):
    assert store.record(friction(noon()), noon() + 5) is None
    assert store.record(friction(noon() + 600), noon() + 700) is None
    assert store.record(friction(noon() + 1200), noon() + 1300) is None       # three, but all on one day
    assert store.open_findings() == []
    [waiting] = store.pending()
    assert (waiting.n, waiting.days) == (3, 1)
    found = store.record(friction(noon() + DAY), noon() + DAY + 5)
    assert (found.n, found.days, found.first, found.last) == (4, 2, noon(), noon() + DAY)
    assert store.pending() == [] and store.open_findings() == [found]


def test_two_on_one_day_and_one_on_the_next_is_three_on_two_days(store):
    assert store.record(friction(noon()), noon()) is None
    assert store.record(friction(noon() + 600), noon()) is None
    assert store.record(friction(noon() + DAY), noon()).n == 3


def test_two_sightings_on_two_days_are_not_yet_enough(store):
    assert store.record(friction(noon()), noon()) is None
    assert store.record(friction(noon() + DAY), noon()) is None
    assert [f.n for f in store.pending()] == [2]


def test_a_friction_seen_once_counts_when_a_probe_fired_within_five_minutes(store):
    assert store.record(result("event", "a summon got no focus_ack within 1.5 s", "summon-focus", "bar",
                               at=noon() + 100), noon() + 120) is not None
    found = store.record(friction(noon() + 350), noon() + 360)                 # 250 s later
    assert found is not None and found.n == 1 and found.kind == "friction"
    assert store.record(friction(noon() + 900, "a turn was stopped within 5 seconds of starting"),
                        noon() + 910) is None                                   # 800 s later: nothing to go with


def test_a_probe_that_fires_after_a_friction_brings_it_in_too(store):
    assert store.record(friction(noon()), noon()) is None
    assert store.open_findings() == [] and len(store.pending()) == 1
    store.record(result("crash", "quickshell crashed (SIGSEGV)", "coredump", "bar", at=noon() + 200), noon() + 210)
    kinds = sorted(f.kind for f in store.open_findings())
    assert kinds == ["crash", "friction"] and store.pending() == []


def test_a_finding_about_the_loop_itself_is_not_the_probe_a_friction_waits_for(store):
    store.record(result("event", "boom raised KeyError: 'x'", "boom", "loop", at=noon()), noon())
    assert store.record(friction(noon() + 60), noon() + 60) is None


def test_a_friction_nobody_repeated_is_forgotten_after_a_month(store):
    store.record(friction(noon()), noon())
    assert len(store.pending()) == 1
    [waiting] = store.pending()
    store.record(result("crash", "agentd crashed (SIGSEGV)", "coredump", "agentd", at=noon() + 40 * DAY),
                 noon() + 40 * DAY)
    assert store.pending() == [] and not Path(waiting.evidence).exists()


# -- what they said no to --

def test_a_finding_he_said_never_to_is_not_raised_again_until_he_brings_it_back(store):
    fp = store.record(result(), noon()).fp
    assert store.mark(fp, "never").state == "never"
    assert store.record(result(), noon() + 2 * DAY) is None
    assert store.record_retry(result(retry_after=0.5), result(), noon() + 2 * DAY) is None
    got = store.get(fp)
    assert got.state == "never" and got.n == 1 and got.last == noon()
    assert store.open_findings() == [] and [f.fp for f in store.all(["dismissed", "never"])] == [fp]
    assert store.mark(fp, "open").state == "open"                               # Bring back
    assert store.record(result(), noon() + 3 * DAY).n == 2


def test_a_crash_he_said_never_to_stays_quiet_even_when_it_happens_again(store):
    crash = result("crash", "quickshell crashed (SIGSEGV)", "coredump", "bar", at=noon())
    fp = store.record(crash, noon()).fp
    store.mark(fp, "never")
    assert store.record(dataclasses.replace(crash, at=noon() + DAY), noon() + DAY) is None


def test_what_he_did_with_a_finding_survives_more_sightings(store):
    fp = store.record(result(), noon()).fp
    assert store.mark(fp, "reported").state == "reported"
    again = store.record(result(), noon() + DAY)
    assert again.state == "reported" and again.n == 2
    assert [f.fp for f in store.open_findings()] == [fp]                        # reported still waits for them
    store.mark(fp, "sent")
    assert store.open_findings() == [] and [f.fp for f in store.all(["sent"])] == [fp]
    assert store.record(result(), noon() + 2 * DAY).n == 3                      # and is still counted


def test_a_state_he_cannot_choose_changes_nothing(store, capsys):
    fp = store.record(result(), noon()).fp
    assert store.mark(fp, "fixed") is None and store.get(fp).state == "open"
    assert "findings: mark" in capsys.readouterr().err
    assert store.mark("hypr:no-such:000000", "sent") is None


def test_clear_what_it_found_keeps_what_he_said_no_to(store):
    shown = store.record(result(), noon())
    dismissed = store.record(result(observed="a floating app sits over another"), noon())
    store.mark(dismissed.fp, "dismissed")
    store.record(friction(noon() + 6 * HOUR), noon() + 6 * HOUR)               # pending, not yet a finding
    assert len(store.pending()) == 1
    report = findings.paths.loop_dir() / "reports"
    report.mkdir(parents=True)
    (report / f"{shown.fp}.md").write_text("held")
    assert Path(shown.evidence).is_dir()
    assert store.clear_found() == 1
    assert store.open_findings() == [] and store.pending() == []
    assert [f.fp for f in store.all()] == [dismissed.fp]
    assert not Path(shown.evidence).exists() and not (report / f"{shown.fp}.md").exists()
    assert Path(dismissed.evidence).is_dir()
    assert store.clear_found() == 0


def test_clearing_brings_back_a_state_that_is_still_wrong_but_not_a_crash_already_read(store):
    crash = result("crash", "quickshell crashed (SIGSEGV)", "coredump", "bar", at=noon())
    store.record(crash, noon())
    store.record(result(), noon())
    store.clear_found()
    assert store.record(crash, noon() + 60) is None                             # the dump is still in coredumpctl
    back = store.record(result(), noon() + 60)
    assert back.n == 1 and back.first == noon() + 60                            # counted from the clear, not before


# -- flaky probes --

def flip(store, when, probe_id="drawer-focus"):
    bad, fixed = fixture("drawer-bad"), fixture("drawer-fixed")
    [first] = run_probe(probe_id, bad)
    return store.record_retry(first, run_probe(probe_id, fixed, retried=True), when)


def test_a_probe_that_flips_three_times_in_a_week_is_quarantined_and_becomes_a_finding(store):
    assert flip(store, noon()) is None and flip(store, noon() + 1 * DAY) is None
    assert store.quarantined(noon() + 2 * DAY) == set()
    about = flip(store, noon() + 2 * DAY)
    assert about is not None and about.fp.startswith("loop:flaky-probe:")
    assert (about.component, about.rule, about.kind, about.probe) == ("loop", "flaky-probe", "event", "drawer-focus")
    assert about.fixable is False and about.report_only is True and about.state == "open"
    assert "drawer-focus" in about.title and "drawer-focus" in about.observed and "3 times in a week" in about.observed
    assert store.quarantined(noon() + 2 * DAY) == {"drawer-focus"}
    assert [f.fp for f in store.open_findings()] == [about.fp]


def test_a_quarantined_probe_is_left_out_and_its_results_are_not_recorded(store):
    for d in range(3):
        flip(store, noon() + d * DAY)
    skip = store.quarantined(noon() + 3 * DAY)
    assert "drawer-focus" not in {r.id for r in probes.run_all(fixture("drawer-bad"), skip=skip)}
    [red] = run_probe("drawer-focus", fixture("drawer-bad"), retried=True)
    assert store.record(red, noon() + 3 * DAY) is None and len(store.open_findings()) == 1
    other = result(id="apps-stacked")
    assert store.record(other, noon() + 3 * DAY) is not None                    # the others carry on


def test_flips_more_than_a_week_apart_do_not_add_up_and_a_quarantine_lifts_by_itself(store):
    for d in (0, 1, 2):
        flip(store, noon() + d * DAY)
    assert store.quarantined(noon() + 6 * DAY) == {"drawer-focus"}
    assert store.quarantined(noon() + 7.5 * DAY) == set()                       # the first flip is a week old
    assert flip(store, noon() + 20 * DAY) is None                               # one flip again: not three
    store.release("drawer-focus")
    assert store.conn.execute("SELECT COUNT(*) FROM probe_flips").fetchone()[0] == 0


def test_a_probe_that_raises_is_a_finding_about_the_loop_and_never_fixable(store, monkeypatch):
    monkeypatch.setitem(probes.PROBES, "boom", probes.Probe("boom", "hypr", "invariant", "Boom.", "raises to see",
                                                            (), lambda obs: {}["missing"]))
    [broken] = run_probe("boom", Observation())
    found = store.record(broken, noon())
    assert found.component == "loop" and found.rule == "probe-raised" and found.fixable is False
    assert found.report_only and found.probe == "boom" and found.observed == "boom raised KeyError: 'missing'"
    assert found.title == "One of Bombadil's own checks (boom) broke, so it found nothing this time."


def test_nothing_about_the_loop_is_ever_fixable_not_even_when_the_row_says_so(store):
    about = store.record(result("event", "x raised KeyError", "x", "loop"), noon())
    plain = store.record(result(), noon())
    store.conn.execute("UPDATE findings SET fixable=1")      # what the mender will do, one day, to what it can fix
    assert store.get(about.fp).fixable is False and store.get(about.fp).report_only is True
    assert store.get(plain.fp).fixable is True and store.get(plain.fp).report_only is False


# -- the evidence bundle --

SECRETS = {
    "title": "Top secret budget 2026.xlsx - LibreOffice Calc",
    "prompt": "show me my passwords for the bank and move the savings money",
    "answer": "Your bank password is on the second page of the vault",
    "summary": "Moved the savings money",
}


def secret_observation(home) -> Observation:
    obs = fixture("drawer-bad")
    obs.clients[0]["title"] = obs.clients[0]["initialTitle"] = SECRETS["title"]
    obs.ledger = [{"t": 1, "prompt": SECRETS["prompt"], "result": SECRETS["answer"], "summary": SECRETS["summary"],
                   "id": "a-1", "ok": True, "origin": "typed", "session": "8f14e45f-ceea-467f-a0e6-5b7d2b7e5c3c",
                   "details": f"{home}/.local/state/bombadil/turns/a-1.jsonl"}]
    return obs


def test_the_evidence_holds_nothing_of_his(store, home):
    obs = secret_observation(home)
    turn = {**obs.ledger[0], "started": 0.0, "seconds": 9.5, "provider": "claude", "model": "m", "v": 2,
            "tools": {"n": 2, "names": ["Bash", "mcp__bombadil-os__show_panel"]}, "snapshot": 4}
    log = [f"agentd: turn a-1 started: {SECRETS['prompt']}", f"opened {home}/Apps/passwords/main.qml",
           "Traceback in /home/user/src/bombadil/agentd.py line 40", f"focus went to {SECRETS['title']}",
           f"the answer was {SECRETS['answer']} for {SECRETS['prompt']}", f"HOME is {home}", "all fine"]
    log = [f"line {i}" for i in range(100)] + log
    tools = [{"name": "mcp__bombadil-os__show_panel", "ok": False, "error": f"no panel at {home}/x\nsecond line"},
             {"tool": "Bash", "ok": True, "text": "not kept"}]
    versions = {"build": "d9dde3b", "hyprland": "0.56.2", "quickshell": f"0.3.1 ({home}/bin/qs)"}
    obs.bar = {"pid": 1871, "alive_at": 5, "connected_at": 1, "build": "d9dde3b",
               "screens": {"Virtual-1": {"w": 1920, "h": 1080, "rects": [{"name": "pill", "x": 510, "y": 1016, "w": 900,
                                                                          "h": 52}]}}}
    bad = Result(False, "drawer-focus", "hypr", "drawer-focus", "bombadil-details is the active window",
                 f"the drawer is open and the active window is none ({SECRETS['title']})",
                 {"active": "none", "title": SECRETS["title"], "note": f"{SECRETS['prompt']} {home}/x",
                  "nested": {"prompt": SECRETS["prompt"], "keep": 3}}, None, "invariant",
                 "The details drawer takes no keyboard")
    found = store.record(bad, noon(), obs=obs, log=log, turn=turn, tools=tools, versions=versions)
    files = sorted(p.name for p in Path(found.evidence).rglob("*"))
    assert files == ["evidence.json"]                                           # no screenshot, nothing else
    text = evidence_text(found.evidence)
    card = json.dumps({**found.to_dict(), "evidence": ""})                      # the folder's own name is under home
    rows = json.dumps([list(r)[:-1] for r in store.conn.execute("SELECT * FROM findings")])   # all but the folder
    for words in (*SECRETS.values(), "secret", "user@bombadil", "vault", "8f14e45f"):
        assert words not in text + card + rows, words
    for words in ("/home/", str(home), str(home).lstrip("/")):
        assert words not in text + card, words
    for shape in (r"/home/\S", r"~/\S", r"\.jsonl"):
        assert not re.search(shape, text + card), shape

    bundle = json.loads(text)
    assert bundle["command"] == "bombadil probe drawer-focus" and bundle["probe"] == "drawer-focus"
    assert bundle["expected"] and bundle["observed"].startswith("the drawer is open and the active window is none")
    assert bundle["evidence"] == {"active": "none", "note": "… <path>", "nested": {"keep": 3}}
    assert [w["class"] for w in bundle["windows"]] == ["other", "bombadil-details"]
    assert all("title" not in w and "initialTitle" not in w and "pid" not in w and "address" not in w
               for w in bundle["windows"])
    assert bundle["windows"][1] == {"class": "bombadil-details", "at": [120, 60], "size": [1680, 780],
                                    "floating": False, "mapped": True, "hidden": False, "fullscreen": 0,
                                    "workspace": "special:details", "monitor": 0}
    assert bundle["monitors"][0]["special"] == "special:details" and bundle["monitors"][0]["width"] == 1920
    assert {"monitor": "Virtual-1", "level": "3", "namespace": "bombadil-bar"}.items() <= bundle["layers"][0].items()
    assert bundle["bar"]["screens"]["Virtual-1"]["rects"][0] == {"name": "pill", "x": 510, "y": 1016, "w": 900, "h": 52}
    assert "pid" not in bundle["bar"]
    assert bundle["log"][0] == "line 67" and len(bundle["log"]) == 40 and bundle["log"][-1] == "all fine"
    assert bundle["turn"] == {"id": "a-1", "t": 1, "started": 0.0, "seconds": 9.5, "origin": "typed", "ok": True,
                              "provider": "claude", "model": "m", "v": 2, "snapshot": 4,
                              "tools": {"n": 2, "names": ["Bash", "mcp__bombadil-os__show_panel"]}}
    assert bundle["tool_events"] == [{"name": "mcp__bombadil-os__show_panel", "ok": False, "error": "no panel at <path>"},
                                     {"name": "Bash", "ok": True, "error": ""}]
    assert bundle["versions"] == {"build": "d9dde3b", "hyprland": "0.56.2", "quickshell": "0.3.1 (<path>)"}


def test_the_evidence_of_a_failed_turn_keeps_the_error_and_not_the_ask(store, home):
    asked = "transfer the money from savings to checking"
    row = {"t": noon(), "prompt": asked, "result": f"could not parse: {asked}", "ok": False, "id": "a-1",
           "provider": "claude", "origin": "typed", "seconds": 4.0, "started": noon() - 4}
    obs = Observation(ledger=[row], now=noon())
    [failed] = run_probe("turn-failed", obs)
    assert store.record(failed, noon(), obs=obs, turn=row) is None               # friction: not yet
    [found] = store.pending()
    assert asked not in evidence_text(found.evidence) + found.observed + found.title
    assert "could not parse" in found.observed and "…" in found.observed


def test_a_log_of_more_than_forty_lines_keeps_the_last_forty(store):
    found = store.record(result(), noon(), log=[f"l{i}" for i in range(41)])
    assert json.loads(evidence_text(found.evidence))["log"] == [f"l{i}" for i in range(1, 41)]


def test_later_sightings_refresh_the_bundle_and_the_folder_is_per_fingerprint(store):
    a = store.record(result(), noon(), log=["first"])
    b = store.record(result(observed="a floating app sits over another"), noon(), log=["other"])
    store.record(result(), noon() + DAY, log=["second"])
    assert a.evidence != b.evidence and a.evidence.endswith(a.fp) and Path(a.evidence).parent.name == "findings"
    assert json.loads(evidence_text(a.evidence))["log"] == ["second"]
    assert json.loads(evidence_text(b.evidence))["log"] == ["other"]


def test_words_of_trouble_are_added_to_the_evidence_of_a_finding_a_probe_fired_for(store):
    found = store.record(result(), noon())
    store.record(result(), noon() + 2 * HOUR)                                   # a second time, for the sighting times
    prompts = [{"t": noon() + 60, "prompt": "it is still stuck, again"}, {"t": noon() + 5 * HOUR, "prompt": "still broken"},
               {"t": noon() + 2 * HOUR + 100, "prompt": "it won't open"}]
    assert store.add_words(found.fp, prompts) == ["again", "still", "stuck", "won't"]
    bundle = json.loads(evidence_text(found.evidence))
    assert bundle["words"] == ["again", "still", "stuck", "won't"]
    assert "it is still" not in json.dumps(bundle)
    assert store.add_words("hypr:none:000000", prompts) == []


# -- robustness --

def test_nothing_the_store_is_given_makes_it_raise(store):
    weird = result(evidence_is_odd={1, 2}, thing=object(), nan=float("nan"), title="SECRET", deep={"a": {"b": {"c": {
        "d": {"e": {"f": {"g": 1}}}}}}}, raw=b"bytes")
    weird.observed = "x" * 10_000
    found = store.record(weird, noon())
    assert found is not None and len(found.observed) <= 300
    bundle = json.loads(evidence_text(found.evidence))
    assert "title" not in bundle["evidence"] and bundle["evidence"]["thing"] is None
    assert bundle["evidence"]["nan"] is None and bundle["evidence"]["raw"] is None
    for junk in (None, "x", 5, [], {}, Result(True, "a", "b", "c"), Result(None, "a", "b", "c")):
        assert store.record(junk, noon()) is None
    assert store.record(result(observed="another"), None) is not None           # no clock: the machine's
    assert store.record_retry("x", None, noon()) is None
    assert store.record(result(observed="third"), noon(), obs="not an observation", log="not a list",
                        turn="nope", tools=5, versions=[1]) is not None


def test_a_database_that_cannot_be_written_costs_a_line_and_no_more(store, capsys):
    store.conn.close()
    assert store.record(result(), noon()) is None
    assert store.open_findings() == [] and store.get("x") is None and store.quarantined() == set()
    assert store.clear_found() == 0 and store.mark("x", "sent") is None and store.all() == []
    err = capsys.readouterr().err
    assert "findings: record: ProgrammingError" in err
    assert err.count("findings: record:") == 1                                  # said once, not every call


def test_a_bundle_that_cannot_be_written_does_not_lose_the_finding(store, monkeypatch, capsys):
    def no(*args, **kwargs):
        raise OSError("read-only file system")
    monkeypatch.setattr(findings, "write_json_atomic", no)
    found = store.record(result(), noon())
    assert found is not None and store.get(found.fp) == found
    assert "evidence for hypr:apps-stacked" in capsys.readouterr().err


def test_two_stores_on_one_file_see_the_same_findings(home):
    a, b = FindingsStore(), FindingsStore()
    found = a.record(result(), noon())
    assert b.get(found.fp) == found
    b.mark(found.fp, "never")
    assert a.record(result(), noon() + DAY) is None


def test_a_finding_knows_the_probe_and_the_words_for_the_card_and_a_report():
    names = [f.name for f in dataclasses.fields(Finding)]
    assert names[:13] == ["fp", "component", "rule", "title", "expected", "observed", "first", "last", "n", "days",
                          "state", "fixable", "evidence"]
    f = Finding("hypr:apps-stacked:3c91a0", "hypr", "apps-stacked", "t", "e", "o", 1.0, 2.0, 3, 2)
    assert f.state == "open" and f.fixable is False and f.report_only is True
    assert f.to_dict()["fp"] == "hypr:apps-stacked:3c91a0" and f.to_dict()["n"] == 3
    assert findings.STATES == ("open", "reported", "sent", "dismissed", "never")


def test_the_store_needs_no_connection_of_its_own(tmp_path):
    from bombadil.loop import db
    conn = db.connect(tmp_path / "other.db")
    s = FindingsStore(conn)
    assert s.record(result(), noon()) is not None
    assert FindingsStore(conn).get(fingerprint_of(result())) is not None       # schema applied twice is fine
    conn.close()
