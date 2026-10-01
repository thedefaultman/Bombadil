"""The probes: each one red on the state a bug left behind and green on the fixed one, reading only what it
is given, and never raising however little (or however much garbage) it is given."""

import dataclasses
import json
from pathlib import Path

import pytest

from bombadil.loop import probes
from bombadil.loop.probes import PROBES, Observation, Result, run_all, run_probe

FIXTURES = Path(__file__).parent / "fixtures" / "loop"
T0 = 1_790_680_000.0

BRIEF_IDS = [
    "drawer-focus", "summon-focus", "apps-stacked", "window-oversize", "window-under-bar", "monitor-narrow",
    "hypr-config", "bar-layer", "agentd-ping", "bar-alive", "bar-restarts", "coredump", "turn-failed", "os-tools",
    "tool-errors", "app-check", "undo-soon", "stop-soon", "rephrase", "slow-turn", "app-health", "provider-drift",
]


def fixture(name: str) -> Observation:
    return Observation(**json.loads((FIXTURES / "probes" / f"{name}.json").read_text()))


def ledger() -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / "ledger_v2.jsonl").read_text().splitlines()]


def one(probe_id: str, obs: Observation, **kw) -> Result:
    results = run_probe(probe_id, obs, **kw)
    assert len(results) == 1, results
    return results[0]


def reds(probe_id: str, obs: Observation) -> list[Result]:
    return [r for r in run_probe(probe_id, obs) if r.ok is False]


def events(*rows) -> Observation:
    return Observation(events=list(rows), now=T0)


# -- the registry --

def test_every_probe_the_brief_names_exists():
    assert set(BRIEF_IDS) <= set(PROBES)
    assert probes.ids() == list(PROBES)
    assert probes.get("drawer-focus") is PROBES["drawer-focus"]
    assert probes.get("nope") is None


def test_every_probe_says_in_plain_words_what_it_is():
    fields = {f.name for f in dataclasses.fields(Observation)}
    for p in PROBES.values():
        assert p.kind in probes.KINDS, p.id
        assert p.title[0].isupper() and ":" not in p.title, p.id     # a sentence for him, not "Name: explainer"
        assert len(p.what) > 20 and p.component, p.id
        assert set(p.needs) <= fields, p.id


def test_an_invariant_asks_for_a_retry_and_nothing_else_does():
    for p in PROBES.values():
        for r in run_probe(p, Observation()):
            assert r.retry_after is None    # not checked: nothing to look at again
    bad = fixture("apps-stacked-bad")
    first = one("apps-stacked", bad)
    assert first.ok is False and first.retry_after == 0.5 and first.kind == "invariant"
    assert one("apps-stacked", bad, retried=True).retry_after is None
    summon = one("summon-focus", fixture("super-tap-bad"))
    assert summon.ok is False and summon.retry_after is None and summon.kind == "event"


# -- the six the laptop VM found by hand: red on the bad state, green on the fixed one --

SIX = [
    ("drawer", "drawer-focus", "hypr"),                     # PR #6: the drawer opened without the keyboard
    ("super-tap", "summon-focus", "bar"),                   # PR #7: a Super tap left the pill without the keyboard
    ("line-over-app", "window-under-bar", "hypr"),          # thread 13: the finished line covered a new app
    ("apps-stacked", "apps-stacked", "hypr"),               # thread 13: every new app opened at one spot
    ("monitor-640", "monitor-narrow", "hypr"),              # PR #7: QEMU's screen stuck at 640x480
    ("monitor-640", "window-oversize", "hypr"),             # ... which leaves a 540x660 app taller than it
]


@pytest.mark.parametrize("name, probe_id, component", SIX)
def test_the_probe_is_red_on_the_known_bad_state(name, probe_id, component):
    bad = reds(probe_id, fixture(f"{name}-bad"))
    assert bad, f"{probe_id} did not see what the VM saw"
    assert bad[0].component == component and bad[0].id == probe_id and bad[0].rule == probe_id
    assert bad[0].expected and bad[0].observed and bad[0].title


@pytest.mark.parametrize("name, probe_id, component", SIX)
def test_the_probe_is_green_on_the_fixed_state(name, probe_id, component):
    assert one(probe_id, fixture(f"{name}-fixed")).ok is True


@pytest.mark.parametrize("name", ["drawer", "super-tap", "line-over-app", "apps-stacked", "monitor-640"])
def test_nothing_else_is_red_on_the_bad_state_or_the_fixed(name):
    expected = {pid for n, pid, _ in SIX if n == name}
    assert {r.id for r in run_all(fixture(f"{name}-bad")) if r.ok is False} == expected
    assert [r for r in run_all(fixture(f"{name}-fixed")) if r.ok is False] == []


def test_the_drawer_reads_as_the_bug_said_it_did():
    bad = fixture("drawer-bad")
    r = one("drawer-focus", bad)
    assert r.observed == "the drawer is open and the active window is none"
    # Focus left on another window, or on the bar as a window, is the same bug.
    for klass, shown in (("bombadil-bar", "bombadil-bar"), ("foot", "other"), ("bombadil-app-notes", "bombadil-app")):
        bad.activewindow = {"class": klass, "title": "whatever he was doing", "focusHistoryID": 0}
        r = one("drawer-focus", bad)
        assert r.ok is False and r.observed == f"the drawer is open and the active window is {shown}"
    assert "whatever he was doing" not in json.dumps(dataclasses.asdict(r))


def test_the_drawer_probe_has_nothing_to_judge_unless_the_drawer_is_open_and_old_enough():
    obs = fixture("drawer-bad")
    obs.monitors[0]["specialWorkspace"] = {"id": 0, "name": ""}      # slid away
    assert one("drawer-focus", obs).ok is True
    obs = fixture("drawer-bad")
    obs.details_at = T0 - 1.0
    r = one("drawer-focus", obs)
    assert r.ok is None and "less than 2 seconds" in r.observed
    obs.details_at = T0 - 2.0
    assert one("drawer-focus", obs).ok is False
    obs.details_at = None               # when nobody saw it open, it has been open long enough
    assert one("drawer-focus", obs).ok is False


def test_a_summoned_pill_holds_the_keyboard_on_purpose():
    obs = fixture("drawer-bad")
    obs.events = [{"t": T0 - 3, "kind": "summon", "id": 7}]
    r = one("drawer-focus", obs)
    assert r.ok is None and "summoned" in r.observed
    obs.events = [{"t": T0 - 30, "kind": "summon", "id": 7}]    # long before the drawer opened
    assert one("drawer-focus", obs).ok is False


# -- the compositor, the rest of it --

def app(at=(690, 210), size=(540, 660), ws=1, floating=True, hidden=False, klass="bombadil-app-notes", monitor=0):
    return {"class": klass, "at": list(at), "size": list(size), "workspace": {"id": ws, "name": str(ws)},
            "floating": floating, "hidden": hidden, "mapped": True, "monitor": monitor, "title": "Notes"}


def test_apps_stacked_needs_two_floating_apps_on_one_workspace_with_one_spot():
    def stacked(*clients):
        return one("apps-stacked", Observation(clients=list(clients)))
    assert stacked(app(), app(klass="bombadil-app-calc")).ok is False
    assert stacked(app(), app(klass="bombadil-app-calc"), app(klass="bombadil-app-clock")).observed.startswith("3 ")
    assert stacked(app()).ok is True
    assert stacked(app(), app(at=(700, 220))).ok is True                        # offset: one spot each
    assert stacked(app(), app(size=(500, 660))).ok is True
    assert stacked(app(), app(ws=2)).ok is True                                 # another workspace
    assert stacked(app(), app(floating=False)).ok is True                       # tiled windows share nothing
    assert stacked(app(), app(hidden=True)).ok is True
    assert stacked(app(klass="foot"), app(klass="foot")).ok is True             # his windows are his


def monitor(w=1920, h=1080, **kw):
    return {"id": 0, "name": "Virtual-1", "width": w, "height": h, "x": 0, "y": 0, "scale": 1.0, "transform": 0,
            "activeWorkspace": {"id": 1, "name": "1"}, "specialWorkspace": {"id": 0, "name": ""}, **kw}


def test_window_oversize_is_in_logical_pixels_and_about_bombadils_own_windows():
    def fits(client, **mon):
        return one("window-oversize", Observation(clients=[client], monitors=[monitor(**mon)])).ok
    assert fits(app(size=(540, 660))) is True
    assert fits(app(size=(540, 1200))) is False
    assert fits(app(size=(1922, 1080))) is True                                 # two pixels of rounding
    assert fits(app(size=(1930, 1080))) is False
    assert fits(app(size=(1900, 1000)), w=3840, h=2160, scale=2.0) is True      # logical 1920x1080
    assert fits(app(size=(1900, 1000)), w=1920, h=1080, scale=2.0) is False     # logical 960x540
    assert fits(app(size=(1000, 1800)), w=1080, h=1920, transform=1) is False   # turned on its side: 1920x1080
    assert fits(app(size=(1800, 1000)), w=1080, h=1920, transform=1) is True
    assert fits(app(size=(9999, 9999), klass="firefox")) is True                # not ours
    assert fits({**app(size=(9999, 9999), klass="org.gnome.Nautilus"), "workspace": {"id": -97, "name": "special:files"}}) is False
    assert fits(app(size=(9999, 9999), monitor=3)) is True                      # a monitor we cannot find


def bar(rects, alive=T0 - 2, screen="Virtual-1"):
    return {"alive_at": alive, "screens": {screen: {"w": 1920, "h": 1080, "rects": rects}}}


LINE = {"name": "line", "x": 510, "y": 790, "w": 900, "h": 218}


def test_window_under_bar_needs_an_overlap_with_what_the_bar_shows():
    def under(client, rects=(LINE,), **kw):
        return one("window-under-bar", Observation(clients=[client], monitors=[monitor()], bar=bar(list(rects), **kw),
                                                   now=T0))
    assert under(app(at=(690, 210))).ok is False
    assert under(app(at=(690, 96))).ok is True                                  # ends at 756, the line starts at 790
    assert under(app(at=(690, 130))).ok is True                                 # touches: ends at 790
    assert under(app(at=(690, 133))).ok is True                                 # 3 pixels: border, not "under"
    assert under(app(at=(690, 134))).ok is False                                # 4 pixels are
    assert under(app(at=(-100, 210))).ok is True                                # beside it
    assert under(app(ws=2)).ok is True                                          # on a workspace nobody is looking at
    assert under(app(floating=False)).ok is True
    assert under(app(klass="firefox")).ok is True                               # the pill over his windows is by design
    assert under({**app(), "workspace": {"id": -98, "name": "special:details"}}).ok is True
    shown = monitor(specialWorkspace={"id": -98, "name": "special:details"})
    special = {**app(), "workspace": {"id": -98, "name": "special:details"}}
    assert one("window-under-bar", Observation(clients=[special], monitors=[shown], bar=bar([LINE]), now=T0)).ok is False


def test_window_under_bar_reads_the_bars_rectangles_in_each_screens_own_coordinates():
    second = monitor(id=1, name="HDMI-A-1", x=1920)
    window = {**app(at=(1920 + 690, 210)), "monitor": 1}
    obs = Observation(clients=[window], monitors=[monitor(), second], now=T0,
                      bar={"alive_at": T0, "screens": {"HDMI-A-1": {"rects": [LINE]}, "Virtual-1": {"rects": []}}})
    assert one("window-under-bar", obs).ok is False
    obs.bar["screens"]["HDMI-A-1"]["rects"] = [{**LINE, "x": 0, "w": 500}]
    assert one("window-under-bar", obs).ok is True


def test_window_under_bar_is_not_checked_when_the_bar_said_nothing_current():
    assert one("window-under-bar", Observation(clients=[app()], monitors=[monitor()], bar=bar([LINE], alive=T0 - 99),
                                               now=T0)).ok is None
    assert one("window-under-bar", Observation(clients=[app()], monitors=[monitor()], bar=bar([]), now=T0)).ok is None
    assert one("window-under-bar", Observation(clients=[app()], monitors=[monitor()], bar={"screens": []})).ok is None
    assert one("window-under-bar", Observation(clients=[app()], monitors=[monitor()])).ok is None


def test_monitor_narrow_is_about_logical_width():
    def narrow(*monitors):
        return one("monitor-narrow", Observation(monitors=list(monitors)))
    assert narrow(monitor(640, 480)).ok is False
    assert narrow(monitor(1920, 1080)).ok is True
    assert narrow(monitor(1366, 768)).ok is True
    assert narrow(monitor(1024, 768)).ok is True
    assert narrow(monitor(1023, 768)).ok is False
    assert narrow(monitor(1280, 720, scale=1.5)).ok is False                    # 853 logical
    assert narrow(monitor(3840, 2160, scale=2.0)).ok is True
    assert narrow(monitor(1080, 1920, transform=1)).ok is True                  # on its side, 1920 wide
    assert narrow(monitor(640, 480, disabled=True)).ok is True
    r = narrow(monitor(1920, 1080), monitor(640, 480, name="HDMI-A-1", id=1))
    assert r.observed == "monitor HDMI-A-1 is 640 logical pixels wide, under 1024"


def test_hyprland_config_errors_are_read_with_the_paths_taken_out():
    assert one("hypr-config", Observation(configerrors=[])).ok is True
    assert one("hypr-config", Observation(configerrors=[""])).ok is True        # hyprctl's empty answer
    err = "Config error in file /home/user/.config/hypr/hyprland.lua at line 12: no such function hl.nope"
    r = one("hypr-config", Observation(configerrors=[err, "another"]))
    assert r.ok is False and r.observed.startswith("Config error in file <path> at line 12")
    assert "/home" not in r.observed and r.evidence["errors"][1] == "another"


def layers(*namespaces):
    return {"Virtual-1": {"levels": {"0": [], "1": [], "2": [{"namespace": n} for n in namespaces], "3": []}}}


def test_the_bar_layer_has_to_be_somewhere():
    assert one("bar-layer", Observation(layers=layers("bombadil-bar", "mako"))).ok is True
    r = one("bar-layer", Observation(layers=layers("mako", "wallpaper")))
    assert r.ok is False and r.evidence["layers"] == ["mako", "wallpaper"]
    assert one("bar-layer", Observation(layers={})).ok is None                  # no monitors at all
    assert one("bar-layer", Observation(layers={"Virtual-1": {"levels": {}}})).ok is False


# -- the bar, agentd --

def summon(t, sid=1, **kw):
    return {"t": t, "kind": "summon", "id": sid, **kw}


def ack(t, sid=1, ms=100, **kw):
    return {"t": t, "kind": "focus_ack", "id": sid, "ms": ms, **kw}


def test_a_summon_needs_its_ack_within_a_second_and_a_half():
    assert one("summon-focus", events(summon(T0 - 20), ack(T0 - 19.9))).ok is True
    assert one("summon-focus", events(summon(T0 - 20), ack(T0 - 18.7, ms=1300))).ok is True
    r = one("summon-focus", events(summon(T0 - 20), ack(T0 - 17.6, ms=2400, late=True)))
    assert r.ok is False and r.evidence["late"] is True and r.evidence["waited"] == 2.4 and r.at == T0 - 20
    r = one("summon-focus", events(summon(T0 - 20)))
    assert r.ok is False and r.evidence["late"] is False and r.at == T0 - 20


def test_a_summon_is_not_judged_before_agentd_has_had_time_to_write_it_up():
    assert one("summon-focus", events(summon(T0 - 3.0))).ok is True             # 1.5 s grace + a 3 s tick
    assert one("summon-focus", events(summon(T0 - 5.0))).ok is False
    # ... unless agentd already wrote the timeout.
    timed = {"t": T0 - 1, "kind": "focus_timeout", "id": 1, "waited": 1.6, "bar": True}
    assert one("summon-focus", events(summon(T0 - 3.0), timed)).ok is False
    # No clock and no timeout: nothing says it was not answered.
    assert one("summon-focus", Observation(events=[summon(T0 - 100)])).ok is True


def test_a_summon_with_no_bar_listening_says_nothing_about_focus():
    timed = {"t": T0 - 10, "kind": "focus_timeout", "id": 1, "waited": 3.0, "bar": False}
    assert one("summon-focus", events(summon(T0 - 14), timed)).ok is True


def test_summon_ids_restart_with_agentd_and_each_summon_is_judged_on_its_own():
    rows = [summon(T0 - 100, 1), ack(T0 - 99.9, 1),
            summon(T0 - 80, 2),                                                  # agentd restarted before an ack came
            summon(T0 - 60, 1), ack(T0 - 59.9, 1)]                               # the id is used again
    assert [r.at for r in reds("summon-focus", events(*rows))] == [T0 - 80]
    rows = [summon(T0 - 100, 1), summon(T0 - 50, 1), ack(T0 - 49.9, 1)]
    assert [r.at for r in reds("summon-focus", events(*rows))] == [T0 - 100]


def test_agentd_has_to_answer_ping():
    assert one("agentd-ping", Observation(agentd={"connected": True, "ponged": True, "latency": 0.003})).ok is True
    down = one("agentd-ping", Observation(agentd={"connected": False, "error": "[Errno 111] Connection refused"}))
    hung = one("agentd-ping", Observation(agentd={"connected": True, "ponged": False}))
    assert down.ok is False and hung.ok is False and down.observed != hung.observed
    assert down.retry_after == 0.5
    assert one("agentd-ping", Observation()).ok is None


UP = {"connected": True, "ponged": True, "latency": 0.003}


def test_the_bars_alive_has_to_be_fresh_but_not_while_agentd_is_only_just_back():
    def alive(age, **kw):
        return one("bar-alive", Observation(bar={"alive_at": T0 - age}, now=T0, agentd=UP, **kw))
    assert alive(5).ok is True and alive(15).ok is True
    assert alive(15.5).ok is False and alive(15.5).observed == "the bar's last alive is more than 15 s old"
    assert alive(40, agentd_started=T0 - 10).ok is None                         # the bar has not said hello to it yet
    assert alive(40, agentd_started=T0 - 100).ok is False
    assert alive(5, agentd_started=T0 - 10).ok is True                          # it has
    assert one("bar-alive", Observation(bar={"pid": 1}, now=T0)).ok is None
    assert one("bar-alive", Observation(bar={"alive_at": T0 - 99})).ok is None  # needs the time


def test_a_bar_that_restarts_again_and_again_is_not_one_that_restarted():
    def restarts(*offsets):
        return reds("bar-restarts", events(*({"t": T0 - o, "kind": "restart", "pid": 1, "was": 2} for o in offsets)))
    assert restarts() == [] and restarts(100, 50) == []
    assert restarts(1000, 500, 10) == []                                        # three, but over 16 minutes
    bad = restarts(500, 300, 100)
    assert len(bad) == 1 and bad[0].at == T0 - 100 and bad[0].evidence["restarts"] == 3
    assert [r.at for r in restarts(9000, 8900, 8800, 100, 50, 10)] == [T0 - 8800, T0 - 10]    # two separate bursts
    assert one("bar-restarts", Observation(events=[])).ok is True


# -- crashes --

def dump(exe, when=T0 - 3600, sig=11, **kw):
    return {"time": int(when * 1_000_000), "pid": 4242, "uid": 1000, "gid": 1000, "sig": sig, "corefile": "present",
            "exe": exe, "size": 31_457_280, **kw}


def test_a_coredump_of_one_of_bombadils_programs_is_a_crash_on_sight():
    cases = [({"exe": "/usr/bin/quickshell"}, "bar", "quickshell crashed (SIGSEGV)"),
             ({"exe": "/usr/bin/Hyprland", "sig": 6}, "hypr", "Hyprland crashed (SIGABRT)"),
             ({"exe": "/usr/bin/python3.11", "comm": "agentd"}, "agentd", "agentd crashed (SIGSEGV)"),
             ({"exe": "/usr/bin/python3.11", "comm": "bombadil-app"}, "apps", "bombadil-app crashed (SIGSEGV)"),
             ({"exe": "/usr/bin/python3.11", "cmdline": "python3 /usr/bin/bombadil-os-mcp"}, "os-mcp",
              "bombadil-os-mcp crashed (SIGSEGV)"),
             ({"exe": "/usr/lib/chromium/chromium", "cmdline": ["chromium", "--class=bombadil-browser"]}, "browser",
              "the browser panel crashed (SIGSEGV)")]
    for extra, component, observed in cases:
        exe = extra.pop("exe")
        bad = reds("coredump", Observation(coredumps=[dump(exe, **extra)], now=T0))
        assert len(bad) == 1 and bad[0].component == component and bad[0].observed == observed
        assert bad[0].kind == "crash" and bad[0].at == T0 - 3600 and bad[0].retry_after is None


def test_a_coredump_of_something_else_or_long_ago_is_not_news():
    others = [dump("/usr/bin/firefox"), dump("/usr/bin/python3.11"),             # python, and nothing says which
              dump("/usr/lib/chromium/chromium", cmdline="chromium https://example.com"),
              dump("/usr/bin/quickshell", when=T0 - 8 * 86400), {"time": "soon", "exe": None}, "x", 7]
    assert one("coredump", Observation(coredumps=others, now=T0)).ok is True
    assert one("coredump", Observation(coredumps=[])).ok is True
    assert one("coredump", Observation(coredumps="no coredumps")).ok is None


def test_each_dump_is_its_own_sighting_and_the_time_is_read_whatever_its_unit():
    rows = [dump("/usr/bin/quickshell", when=T0 - 100), dump("/usr/bin/quickshell", when=T0 - 50)]
    rows[1]["time"] = int((T0 - 50) * 1000)                                     # milliseconds
    assert [r.at for r in reds("coredump", Observation(coredumps=rows, now=T0))] == [T0 - 100, T0 - 50]


# -- turns --

def turn(tid, t, **kw):
    row = {"t": t, "prompt": "show me how full my disk is", "result": "You have 212 GB free.", "ok": True,
           "snapshot": None, "provider": "claude", "stopped": False, "id": tid, "started": t - 10, "seconds": 10.0,
           "origin": "typed", "tools": {"n": 1, "names": ["Bash"]}, "rate_limit": None, "v": 2}
    return {**row, **kw}


def local(verb, t, **kw):
    return {"t": t, "kind": "local", "prompt": verb, "action": verb, "result": "ok", "ok": True, "v": 2, "verb": verb,
            "via": "typed", "word": None, "of": None, "of_snapshot": None, **kw}


def test_a_failed_turn_is_friction_unless_it_is_not_ours():
    failed = turn("a-1", T0 - 100, ok=False, result="API Error: invalid request body")
    assert one("turn-failed", Observation(ledger=[turn("a-0", T0 - 200), failed])).ok is False
    r = one("turn-failed", Observation(ledger=[failed]))
    assert r.kind == "friction" and r.at == T0 - 100 and r.observed == "a turn ended with an error: API Error: invalid request body"
    # No result at all (the provider died) is a failure too.
    assert one("turn-failed", Observation(ledger=[turn("a-2", T0 - 90, ok=None, result="")])).ok is False


@pytest.mark.parametrize("text", [
    "Not logged in · Please run /login", "Claude AI usage limit reached|1790684400", "Credit balance is too low",
    "429 Too Many Requests", "getaddrinfo ENOTFOUND api.anthropic.com", "Connection error.", "API Error: 529 overloaded",
    "Invalid API key", "You've hit your limit · resets 5pm"])
def test_signed_out_a_limit_or_offline_are_not_bombadils_bugs(text):
    assert probes.not_ours(text) in ("signed out", "limit", "offline")
    assert one("turn-failed", Observation(ledger=[turn("a-1", T0, ok=False, result=text)])).ok is True


def test_the_words_of_a_failure_can_come_from_the_turns_error_events_or_a_limit_notice():
    row = turn("a-1", T0, ok=None, result="")
    errors = [{"turn": "a-1", "t": T0, "text": "Not logged in"}]
    assert one("turn-failed", Observation(ledger=[row], turn_errors=errors)).ok is True
    limited = turn("a-2", T0, ok=False, result="", rate_limit={"status": "rejected", "rateLimitType": "five_hour"})
    assert one("turn-failed", Observation(ledger=[limited])).ok is True


def test_a_stop_a_shell_command_and_old_rows_are_not_failed_turns():
    rows = [turn("a-1", T0, ok=None, result="", stopped=True), turn("a-2", T0, ok=False, provider="shell",
                                                                        prompt="!false", result="(exit 1)"),
            {"t": T0, "prompt": "old row"}, local("open", T0, ok=False)]
    assert one("turn-failed", Observation(ledger=rows)).ok is True


def test_the_ledger_fixture_has_no_turn_that_failed_for_a_reason_of_ours():
    assert one("turn-failed", Observation(ledger=ledger())).ok is True


def test_the_error_line_of_a_failed_turn_never_carries_his_words():
    asked = "transfer the money from savings to checking"
    failed = turn("a-1", T0, ok=False, prompt=asked, result=f"could not parse: {asked} at /home/user/notes.txt:12")
    r = one("turn-failed", Observation(ledger=[failed]))
    assert asked not in r.observed and "/home" not in r.observed and "…" in r.observed


def test_the_os_tools_not_starting_is_found_in_the_row_or_the_error_event_once_per_turn():
    said = "the OS tools did not start (bombadil-os: failed)"
    from_row = turn("a-1", T0 - 5, ok=False, result=said)
    from_event = [{"turn": "a-2", "t": T0 - 50, "text": said}]
    bad = reds("os-tools", Observation(ledger=[from_row, turn("a-2", T0 - 49, ok=None, result="")],
                                       turn_errors=from_event))
    assert sorted(r.at for r in bad) == [T0 - 49, T0 - 5]
    assert bad[0].observed == "the OS tools did not start (failed)" and bad[0].component == "os-mcp"
    both = reds("os-tools", Observation(ledger=[from_row], turn_errors=[{"turn": "a-1", "t": T0 - 6, "text": said}]))
    assert [r.at for r in both] == [T0 - 5]                                      # one turn, one sighting
    assert one("os-tools", Observation(ledger=[turn("a-1", T0)])).ok is True


def tool(name, ok=True, t=T0 - 100, text="", turn_id="a-1"):
    return {"turn": turn_id, "tool": name, "ok": ok, "t": t, "text": text}


def test_an_os_tool_that_fails_often_enough_often_is_friction():
    calls = [tool("mcp__bombadil-os__open_app", ok=i >= 3) for i in range(8)]   # 3 of 8 fail: 37%
    assert one("tool-errors", Observation(tool_results=calls, now=T0)).ok is True
    calls = [tool("mcp__bombadil-os__open_app", ok=i >= 4) for i in range(8)]   # 4 of 8: 50%
    r = one("tool-errors", Observation(tool_results=calls, now=T0))
    assert r.ok is False and r.evidence == {"tool": "open_app", "calls": 8, "failed": 4}
    assert r.kind == "friction" and r.component == "os-mcp"
    assert one("tool-errors", Observation(tool_results=calls[:4], now=T0)).ok is True    # four calls are too few
    old = [tool("show_panel", ok=False, t=T0 - 5 * 86400) for _ in range(9)]
    assert one("tool-errors", Observation(tool_results=old, now=T0)).ok is True
    assert one("tool-errors", Observation(tool_results=[])).ok is True


CHECK = ('create_app: check FAILED. The files are written; a running app keeps its last good version.\n'
         '{"ok": false, "errors": ["main.qml:14:9: Type Card unavailable", "main.qml:3:1: other"]}')


def test_create_app_failing_its_check_again_for_one_piece_is_friction():
    same = [tool("mcp__bombadil-os__create_app", text=CHECK, t=T0 - 90, turn_id="a-1"),
            tool("mcp__bombadil-os__create_app", text=CHECK.replace("14:9", "31:5"), t=T0 - 30, turn_id="a-1")]
    r = one("app-check", Observation(tool_results=same))
    assert r.ok is False and r.at == T0 - 30 and r.evidence == {"times": 2, "turns": 1}
    assert r.observed == "create_app failed its check again for the same piece: Type Card unavailable"
    assert one("app-check", Observation(tool_results=same[:1])).ok is True
    other = CHECK.replace("Type Card unavailable", "Cannot assign to non-existent property")
    assert one("app-check", Observation(tool_results=[same[0], tool("create_app", text=other)])).ok is True
    passed = tool("create_app", text="create_app: check passed.")
    assert one("app-check", Observation(tool_results=[passed, passed])).ok is True
    elsewhere = [tool("show_panel", text=CHECK), tool("show_panel", text=CHECK)]
    assert one("app-check", Observation(tool_results=elsewhere)).ok is True


def test_an_undo_within_a_minute_of_a_turn_ending_is_friction():
    row = turn("a-1", T0 - 100, snapshot=41)
    by_id = local("undo", T0 - 70, of="a-1")
    by_snapshot = local("undo", T0 - 70, of_snapshot=41)
    for undo in (by_id, by_snapshot):
        r = one("undo-soon", Observation(ledger=[row, undo]))
        assert r.ok is False and r.at == T0 - 70 and r.evidence["after"] == 30 and r.kind == "friction"
    assert one("undo-soon", Observation(ledger=[row, local("undo", T0 - 40, of="a-1")])).ok is False    # 60 s
    assert one("undo-soon", Observation(ledger=[row, local("undo", T0 - 39, of="a-1")])).ok is True     # 61 s
    assert one("undo-soon", Observation(ledger=[row, local("undo", T0 - 70, of="other")])).ok is True
    assert one("undo-soon", Observation(ledger=[row, local("undo", T0 - 70, of="a-1", ok=False)])).ok is True
    shell = turn("a-2", T0 - 100, provider="shell", prompt="!rm x", snapshot=42)
    assert one("undo-soon", Observation(ledger=[shell, local("undo", T0 - 70, of="a-2")])).ok is True


def test_the_fixture_ledgers_undo_of_a_shell_turn_is_his_own_command_undone():
    assert one("undo-soon", Observation(ledger=ledger())).ok is True
    assert one("stop-soon", Observation(ledger=ledger())).ok is True


def test_a_stop_within_five_seconds_of_a_turn_starting_is_friction():
    quick = turn("a-1", T0 - 100, seconds=3.2, stopped=True, ok=None)
    r = one("stop-soon", Observation(ledger=[quick, local("stop", T0 - 100, of="a-1")]))
    assert r.ok is False and r.at == T0 - 100 and r.evidence["ran"] == 3.2
    slow = turn("a-1", T0 - 100, seconds=6.1, stopped=True, ok=None)
    assert one("stop-soon", Observation(ledger=[slow, local("stop", T0 - 100, of="a-1")])).ok is True
    assert one("stop-soon", Observation(ledger=[local("stop", T0 - 100)])).ok is True      # nothing was running
    no_seconds = turn("a-1", T0 - 100, seconds=None, started=T0 - 103, stopped=True, ok=None)
    assert one("stop-soon", Observation(ledger=[no_seconds, local("stop", T0 - 100, of="a-1")])).ok is False


def test_asking_again_soon_after_a_turn_failed_was_stopped_or_undone_is_friction():
    a = turn("a-1", T0 - 300, prompt="show me how full my disk is", snapshot=7)
    again = turn("a-2", T0 - 200, prompt="how full is my disk", started=T0 - 210)

    def asked(first, *extra):
        return reds("rephrase", Observation(ledger=[first, *extra, again]))
    assert [r.evidence["how"] for r in asked({**a, "ok": False, "result": "API Error: bad request"})] == ["failed"]
    assert [r.evidence["how"] for r in asked({**a, "ok": None, "stopped": True})] == ["stopped"]
    undone = asked(a, local("undo", T0 - 290, of="a-1"))
    assert [r.evidence["how"] for r in undone] == ["undone"] and undone[0].at == T0 - 200
    assert asked(a) == []                                                        # it worked: a new ask is a new ask
    assert asked({**a, "ok": False, "result": "Not logged in"}) == []            # not Bombadil's failure
    far = {**again, "started": T0 - 100, "t": T0 - 90}
    assert reds("rephrase", Observation(ledger=[{**a, "ok": False, "result": "bad"}, far])) == []   # 200 s later
    other = {**again, "prompt": "what is the weather in Lisbon", "t": T0 - 200}
    assert reds("rephrase", Observation(ledger=[{**a, "ok": False, "result": "bad"}, other])) == []
    app_ask = {**again, "origin": "app"}
    assert reds("rephrase", Observation(ledger=[{**a, "ok": False, "result": "bad"}, app_ask])) == []


def test_a_turn_three_times_slower_than_its_groups_median_is_friction():
    rows = [turn("a-1", T0 - 300, seconds=12.0), turn("a-2", T0 - 200, seconds=30.0), turn("a-3", T0 - 100, seconds=40.0)]
    obs = Observation(ledger=rows, groups={"a-1": "disk", "a-2": "disk", "a-3": "disk"}, medians={"disk": 10.0})
    bad = reds("slow-turn", obs)
    assert [r.at for r in bad] == [T0 - 200, T0 - 100] and bad[1].evidence == {"seconds": 40.0, "median": 10.0, "times": 4.0}
    slower = dataclasses.replace(obs, medians={"disk": 13.0})                   # 39 s is three times as long
    assert [r.at for r in reds("slow-turn", slower)] == [T0 - 100]
    assert one("slow-turn", dataclasses.replace(obs, groups={}, medians={})).ok is True   # no group, no claim
    stopped = Observation(ledger=[turn("a-1", T0, seconds=90.0, stopped=True, ok=None)], groups={"a-1": "g"},
                          medians={"g": 10.0})
    assert one("slow-turn", stopped).ok is True


def test_the_provider_changing_under_bombadil_shows_as_drift():
    r = one("provider-drift", Observation(ledger=ledger()))
    assert r.ok is False and r.kind == "drift" and r.component == "providers"
    assert r.observed == "claude sent message types Bombadil does not know: hologram" and r.at == 1790673841.8
    assert one("provider-drift", Observation(ledger=[turn("a-1", T0)])).ok is True
    many = turn("a-1", T0, drift={"b": 2, "a": 1, "c": 1, "d": 1, "we/ird\n": 1})
    assert "a, b, c" in one("provider-drift", Observation(ledger=[many])).observed


def test_esc_pressed_again_and_again_with_something_up_is_friction():
    row = {"t": T0 - 50, "kind": "friction", "what": "esc", "count": 3, "seconds": 8, "drawer": True}
    r = one("esc-friction", events(row))
    assert r.ok is False and r.kind == "friction" and r.at == T0 - 50
    assert r.observed == "Esc was pressed again and again with the drawer up"
    assert "a card up" in one("esc-friction", events({**row, "drawer": False, "card": True})).observed
    assert one("esc-friction", events({**row, "what": "enter"})).ok is True
    assert one("esc-friction", events({**row, "count": 2})).ok is True
    assert one("esc-friction", events({**row, "seconds": 30})).ok is True
    assert one("esc-friction", events({"t": 1, "kind": "hello"})).ok is True


# -- apps --

def test_an_app_whose_status_is_not_ok_and_whose_process_is_gone_is_a_bug():
    assert one("app-health", Observation(apps=[{"ok": False, "running": False, "log": []}])).ok is False
    assert one("app-health", Observation(apps=[{"ok": False, "running": True, "log": []}])).ok is True    # a red banner
    assert one("app-health", Observation(apps=[{"ok": True, "running": False, "log": []}])).ok is True
    assert one("app-health", Observation(apps=[{"running": False}])).ok is True       # no status yet
    assert one("app-health", Observation(apps=[{"ok": False}])).ok is True            # cannot tell it is gone
    assert one("app-health", Observation(apps=[])).ok is True


def test_a_fatal_line_in_an_apps_log_counts_from_the_apps_latest_start():
    log = ["--- 10:00:01 bombadil-app run notes (pid 4242)", "loaded /home/user/Apps/notes/main.qml",
           "10:02:11 error: FATAL: Segmentation fault in libQt6Quick"]
    r = one("app-health", Observation(apps=[{"ok": True, "running": True, "log": log}]))
    assert r.ok is False
    assert r.observed == "an app's log has a fatal line: error: fatal: segmentation fault in libqtquick"
    assert "/home" not in json.dumps(r.evidence)
    restarted = [*log, "--- 10:05:00 bombadil-app run notes (pid 4300)", "loaded /home/user/Apps/notes/main.qml"]
    assert one("app-health", Observation(apps=[{"ok": True, "running": True, "log": restarted}])).ok is True
    assert one("app-health", Observation(apps=[{"ok": True, "running": True, "log": ["unfatally fine"]}])).ok is True


# -- his words, only beside a probe that fired --

class Shown:
    def __init__(self, first, last, evidence=""):
        self.first, self.last, self.evidence = first, last, evidence


def test_words_of_trouble_join_a_finding_only_when_a_probe_fired_within_five_minutes():
    finding = Shown(T0, T0 + 60)
    prompts = [{"t": T0 - 200, "prompt": "the drawer is still stuck, it won't close"},
               {"t": T0 + 200, "prompt": "nothing happens when I press Super"},
               {"t": T0 + 400, "prompt": "why is this STILL broken"},                   # 340 s after the last sighting
               {"t": T0 - 100, "prompt": "show me my passwords"}]
    assert probes.attach_words(finding, prompts) == ["nothing happens", "still", "stuck", "won't"]
    assert probes.attach_words(Shown(T0 + 5000, T0 + 5000), prompts) == []          # no probe fired then
    assert probes.attach_words(finding, [(T0, "it is still broken")]) == ["broken", "still"]
    assert probes.attach_words(finding, [{"t": T0, "prompt": "why won’t it open"}]) == ["won't"]
    assert probes.attach_words(finding, [{"t": T0, "prompt": "open my notes"}]) == []
    assert probes.attach_words(finding, [{"t": T0}, None, "x", (1,), {"t": "a", "prompt": "still"}]) == []
    assert probes.attach_words(None, prompts, fired=[T0 + 400]) == ["broken", "nothing happens", "still"]


def test_the_words_go_into_the_evidence_and_the_sentence_never_does(tmp_path):
    (tmp_path / "evidence.json").write_text(json.dumps({"probe": "drawer-focus"}))
    finding = Shown(T0, T0, evidence=str(tmp_path))
    probes.attach_words(finding, [{"t": T0 + 10, "prompt": "the drawer is stuck again, what a mess"}])
    bundle = json.loads((tmp_path / "evidence.json").read_text())
    assert bundle == {"probe": "drawer-focus", "words": ["again", "stuck"]}
    # No bundle, or nothing said: nothing is written, nothing fails.
    probes.attach_words(Shown(T0, T0, evidence=str(tmp_path / "missing")), [{"t": T0, "prompt": "still"}])
    assert not (tmp_path / "missing").exists()


# -- robustness: a half-filled Observation, garbage, a probe that raises --

def test_an_observation_with_nothing_in_it_checks_nothing_and_fails_nothing():
    results = run_all(Observation())
    assert len(results) == len(PROBES) and all(r.ok is None for r in results)
    assert all(r.observed.startswith("not checked: no ") for r in results)
    assert one("drawer-focus", Observation()).observed == "not checked: no clients, monitors, activewindow"


def test_half_the_fields_check_half_the_probes():
    obs = Observation(monitors=[monitor(640, 480)], configerrors=[])
    results = {r.id: r.ok for r in run_all(obs)}
    assert results["monitor-narrow"] is False and results["hypr-config"] is True
    assert sum(1 for ok in results.values() if ok is not None) == 2


GARBAGE = ["hyprctl: command not found", 42, 3.5, True, [], {}, [None, 1, "x", [], {}],
           {"a": {"b": [1, 2]}}, [{"at": "x", "size": [1], "class": 5, "workspace": "w", "monitor": "m",
                                    "floating": "yes", "mapped": None, "hidden": [], "t": "now", "id": {}}],
           [{"at": [1, 2], "size": [None, 3], "class": None, "workspace": {"id": "x", "name": 4},
             "t": float("nan"), "ok": "maybe", "prompt": 5, "result": [], "drift": "x", "of": [], "log": "a"}],
           {"screens": {"x": 1, "y": {"rects": "no"}}, "alive_at": "now", "levels": 1}, "", float("inf")]


@pytest.mark.parametrize("junk", GARBAGE, ids=[repr(g)[:24] for g in GARBAGE])
def test_garbage_in_every_field_is_not_checked_or_fine_but_never_a_raise(junk):
    obs = Observation(**{f.name: junk for f in dataclasses.fields(Observation)})
    for p in PROBES.values():
        p.run(obs)      # the probe itself, not the wrapper that would catch it
        for r in run_probe(p, obs):
            assert isinstance(r, Result) and r.rule != "probe-raised", (p.id, r)


KEYS = ["class", "at", "size", "workspace", "id", "name", "monitor", "floating", "mapped", "hidden", "width",
        "height", "scale", "transform", "x", "y", "w", "h", "rects", "screens", "alive_at", "levels", "namespace",
        "specialWorkspace", "activeWorkspace", "t", "kind", "of", "of_snapshot", "ok", "stopped", "provider",
        "prompt", "result", "drift", "seconds", "started", "tool", "turn", "text", "log", "running", "exe", "comm",
        "cmdline", "time", "sig", "count", "what", "drawer", "bar", "ms", "verb", "action", "rate_limit"]


def junk(rng, depth=0):
    kind = rng.randrange(9 if depth < 3 else 6)
    if kind == 0:
        return None
    if kind == 1:
        return rng.choice([0, 1, -1, 5, 1790680000, 10**18, 2.5, float("nan"), float("inf"), True, False])
    if kind == 2:
        return rng.choice(["", "x", "special:details", "bombadil-details", "bombadil-app-x", "undo", "stop",
                           "summon", "focus_ack", "restart", "esc", "create_app check FAILED", "…", "a" * 500])
    if kind == 3:
        return [rng.choice([0, 640, 1080, 1790680000.0]) for _ in range(rng.randrange(4))]
    if kind == 4:
        return rng.choice(KEYS)
    if kind == 5:
        return rng.choice([[], {}, ""])
    if kind == 6:
        return [junk(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {rng.choice(KEYS): junk(rng, depth + 1) for _ in range(rng.randrange(8))}


def test_random_structures_in_every_field_never_make_a_probe_raise():
    import random
    rng = random.Random(20260930)
    fields = [f.name for f in dataclasses.fields(Observation)]
    for _ in range(400):
        obs = Observation(**{f: junk(rng) for f in fields if rng.random() < 0.8})
        for p in PROBES.values():
            for r in run_probe(p, obs):
                assert isinstance(r, Result) and r.rule != "probe-raised", (p.id, r.observed, obs)


def test_garbage_beside_good_data_does_not_hide_what_is_really_there():
    bad = fixture("apps-stacked-bad")
    bad.clients = [None, "x", 5, *bad.clients, {"at": "x"}]
    assert one("apps-stacked", bad).ok is False


def test_loads_reads_hyprctl_or_says_it_could_not():
    assert probes.loads('[{"class": "foot"}]') == [{"class": "foot"}]
    assert probes.loads("Invalid") is None and probes.loads("") is None and probes.loads(None) is None


def test_a_probe_that_raises_becomes_a_result_about_the_probe(monkeypatch):
    def boom(obs):
        raise KeyError("workspace")
    monkeypatch.setitem(PROBES, "boom", probes.Probe("boom", "hypr", "invariant", "Boom.", "raises to see", (), boom))
    [r] = run_probe("boom", Observation())
    assert (r.ok, r.component, r.rule, r.id, r.kind) == (False, "loop", "probe-raised", "boom", "event")
    assert r.observed == "boom raised KeyError: 'workspace'" and r.evidence == {"probe": "boom", "error": "KeyError"}
    assert r.retry_after is None and "own checks" in r.title


def test_run_all_goes_on_past_a_probe_that_raises_and_one_that_returns_nonsense(monkeypatch):
    monkeypatch.setitem(PROBES, "boom", probes.Probe("boom", "hypr", "invariant", "Boom.", "raises to see", (),
                                                      lambda obs: 1 / 0))
    monkeypatch.setitem(PROBES, "nonsense", probes.Probe("nonsense", "hypr", "invariant", "Odd.", "returns a str",
                                                         (), lambda obs: "fine"))
    results = run_all(fixture("drawer-fixed"))
    assert [r.rule for r in results if r.rule == "probe-raised"] == ["probe-raised", "probe-raised"]
    assert {r.id for r in results} >= {"drawer-focus", "monitor-narrow", "bar-layer"}
    assert any(r.id == "drawer-focus" and r.ok is True for r in results)


def test_an_unknown_probe_or_a_non_observation_is_not_checked():
    assert run_probe("no-such-probe", Observation())[0].ok is None
    assert run_probe("drawer-focus", None)[0].ok is None
    assert run_probe("drawer-focus", {"clients": []})[0].ok is None


def test_run_all_can_leave_probes_out_or_pick_them():
    obs = fixture("monitor-640-bad")
    assert {r.id for r in run_all(obs, only=["monitor-narrow", "hypr-config"])} == {"monitor-narrow", "hypr-config"}
    assert "monitor-narrow" not in {r.id for r in run_all(obs, skip={"monitor-narrow"})}
    assert all(r.retry_after is None for r in run_all(obs, retried=True))


def test_the_probes_only_look_at_what_they_are_given(monkeypatch):
    """No file, program or socket is touched: a probe that tried would raise here and show as probe-raised."""
    import builtins
    import socket
    import subprocess

    def no(*args, **kwargs):
        raise AssertionError("a probe reached outside its Observation")
    observations = [fixture(n) for n in ("drawer-bad", "super-tap-bad", "line-over-app-bad", "apps-stacked-bad",
                                         "monitor-640-bad", "monitor-640-fixed")]
    observations.append(Observation(ledger=ledger(), now=T0, groups={}, medians={}, apps=[{"ok": False}],
                                    coredumps=[dump("/usr/bin/quickshell")], agentd={"connected": True}))
    monkeypatch.setattr(builtins, "open", no)
    monkeypatch.setattr(subprocess, "Popen", no)
    monkeypatch.setattr(socket, "socket", no)
    monkeypatch.setattr(Path, "read_text", no)
    monkeypatch.setattr(Path, "write_text", no)
    monkeypatch.setattr(Path, "exists", no)
    for obs in observations:
        assert not [r for r in run_all(obs) if r.rule == "probe-raised"]


def test_what_goes_in_a_fingerprint_loses_paths_numbers_and_ids():
    line = "Error in /home/user/.config/hypr/x.lua:12 pid 4242 at 0x55a1c0 id 3c91a0ff, 12.5 s (9f8e7d6c-1234-4abc-8def-0123456789ab)"
    assert probes.strip_line(line) == "error in pid at id , s ()"
    assert probes.scrub_text("see /home/user/x.lua:3 and ~/notes/a.txt or https://example.com/a/b") == \
        "see <path> and <path> or https://example.com/a/b"
    assert probes.scrub_text("a/b and 1/2 and / alone") == "a/b and 1/2 and / alone"
