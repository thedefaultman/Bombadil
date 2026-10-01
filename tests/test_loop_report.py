"""The report: built from typed fields, so nothing of his can be in it, and a link that fits.

The privacy tests plant real-looking private things (a home path, a window title, a prompt, a screenshot
path, his name) in every place the evidence could hold them, and assert that none of it can be read
in the report, the preview, the link or the held file.
"""

import http.server
import json
import re
import stat
import sys
import threading
import urllib.error
import urllib.parse
from pathlib import Path
from typing import ClassVar

import pytest

from bombadil.loop import findings, report
from bombadil.loop.findings import Finding, FindingsStore
from bombadil.loop.probes import Observation, run_probe

FIXTURES = Path(__file__).parent / "fixtures" / "loop" / "probes"
VERSIONS = {"build": "d9dde3b", "machine": "laptop VM", "Hyprland": "0.56.2", "Quickshell": "0.3.1",
            "Claude Code": "2.1.283", "codex-cli": "0.157.1", "kit": "unknown"}

SECRETS = {
    "home path": "/home/daniel/Projects/secret-plans/notes.txt",
    "window title": "Quarterly layoffs plan - Google Sheets",
    "prompt": "show me my passwords for the bank",
    "screenshot": "/home/daniel/.local/state/bombadil/loop/findings/x/screenshot-0042.png",
    "app name": "tax-return-2025",
    "his name": "Daniel Hearth",
}


@pytest.fixture(autouse=True)
def his_machine(home, monkeypatch):
    monkeypatch.setenv("HOME", "/home/daniel")
    monkeypatch.setenv("USER", "daniel")
    monkeypatch.setenv("LOGNAME", "daniel")
    monkeypatch.setenv("BOMBADIL_STATE", str(home / "state"))     # where the test's own files go
    monkeypatch.delenv("BOMBADIL_LOOP", raising=False)


def observation(name) -> Observation:
    return Observation(**json.loads((FIXTURES / f"{name}.json").read_text()))


def bundle_for(name, probe, log=(), versions=None) -> tuple[Finding, dict]:
    obs = observation(name)
    r = run_probe(probe, obs)[0]
    fp = findings.fingerprint_of(r)
    bundle = findings.build_bundle(fp, r, obs.now, obs=obs, log=log, versions=VERSIONS if versions is None else versions)
    f = Finding(fp, r.component, r.rule, r.title, r.expected, r.observed, obs.now, obs.now + 60, 3, 2, probe=r.id)
    return f, bundle


def drawer() -> tuple[Finding, dict]:
    return bundle_for("drawer-bad", "drawer-focus")


def texts(rep) -> dict[str, str]:
    """Everything a report can be read as, and the links made from it."""
    link = report.issue_url(rep, copy=None)
    return {"render": rep.render(), "body": rep.body(), "preview": json.dumps(report.preview(rep).to_dict()),
            "to_dict": json.dumps(rep.to_dict()), "url": link.url,
            "url decoded": urllib.parse.unquote(link.url)}


# -- the shape the brief gives --

def test_the_report_reads_like_the_briefs_example():
    f, bundle = drawer()
    text = report.build(f, bundle).render()
    lines = text.splitlines()
    assert lines[0] == f"Title: Details drawer takes no keyboard (Hyprland 0.56.2) [fp {f.fp.split(':')[-1]}]"
    assert lines[1] == "Seen: 3 times on 2 days, build d9dde3b, laptop VM"
    assert lines[2] == "Expected: bombadil-details is the active window within 2 s of Details opening"
    assert lines[3] == "Observed: the drawer is open and the active window is none (probe drawer-focus, 1 retry)"
    assert lines[4] == "Picture: the windows as boxes, drawn from hyprctl rectangles"
    assert lines[5] == "```text" and "```" in lines[6:]
    end = lines.index("```", 6)
    assert lines[end + 1:] == ["Versions: Quickshell 0.3.1, Claude Code 2.1.283, codex-cli 0.157.1",
                               "Tried: nothing has fixed it on this machine yet"]


def test_body_is_the_report_without_its_title_line():
    f, bundle = drawer()
    rep = report.build(f, bundle)
    assert rep.render() == f"Title: {rep.title}\n{rep.body()}\n"
    assert not rep.body().startswith("Title")


def test_seen_counts_in_plain_numbers_and_names_the_machine_only_when_known():
    f, bundle = drawer()
    one = report.build(Finding(**{**f.to_dict(), "n": 1, "days": 1}), bundle)
    assert one.seen == "1 time on 1 day, build d9dde3b, laptop VM"
    bare = report.build(Finding(**{**f.to_dict(), "n": 2, "days": 1}), bundle,
                        versions={"build": "", "machine": ""})
    assert bare.seen == "2 times on 1 day"


def test_the_title_names_the_version_of_what_the_finding_is_about():
    drawer_finding, bundle = drawer()
    assert "(Hyprland 0.56.2)" in report.build(drawer_finding, bundle).title
    bar = Finding("bar:bar-alive:abc123", "bar", "bar-alive", "The bar has stopped answering", "x", "y", 1, 2, 1, 1)
    assert report.build(bar, {}, versions=VERSIONS).title == "Bar has stopped answering (Quickshell 0.3.1) [fp abc123]"
    agentd = Finding("agentd:agentd-ping:0a0a0a", "agentd", "agentd-ping", "Bombadil's agent does not answer", "x", "y",
                     1, 2, 1, 1)
    assert report.build(agentd, {}, versions=VERSIONS).title == "Bombadil's agent does not answer [fp 0a0a0a]"


def test_versions_leave_out_the_one_the_title_names_and_what_is_unknown():
    f, bundle = drawer()
    line = next(x for x in report.build(f, bundle, versions={**VERSIONS, "codex-cli": "unknown"}).render().splitlines()
                if x.startswith("Versions"))
    assert line == "Versions: Quickshell 0.3.1, Claude Code 2.1.283"
    assert "Versions" not in report.build(f, bundle, versions={"build": "abc"}).render()


def test_a_finding_with_no_evidence_still_has_a_report():
    f = Finding("turns:turn-failed:0f0f0f", "turns", "turn-failed", "A turn ended in an error", "a turn ends with a result",
                "a turn ended with an error: no result", 1.0, 2.0, 1, 1, evidence="/nowhere/at/all")
    rep = report.build(f)
    text = rep.render()
    assert "Picture" not in text and "Log" not in text and "Versions" not in text
    assert text.startswith("Title: A turn ended in an error [fp 0f0f0f]\nSeen: 1 time on 1 day\n")


def test_the_evidence_is_read_from_the_findings_folder(home):
    f, bundle = drawer()
    folder = home / "evidence"
    folder.mkdir()
    (folder / "evidence.json").write_text(json.dumps(bundle))
    rep = report.build(Finding(**{**f.to_dict(), "evidence": str(folder)}))
    assert rep.picture and rep.build == "d9dde3b"
    (folder / "evidence.json").write_text("{torn")
    assert report.build(Finding(**{**f.to_dict(), "evidence": str(folder)})).picture == ""


def test_a_finding_may_be_given_as_its_dict():
    f, bundle = drawer()
    assert report.build(f.to_dict(), bundle).render() == report.build(f, bundle).render()


def test_garbage_in_the_evidence_costs_a_part_of_the_report_never_the_report():
    f, _ = drawer()
    junk = {"windows": "no", "monitors": [1, None, {"width": "wide"}], "bar": [], "log": "one line", "versions": 4,
            "activewindow": [], "kind": ["invariant"]}
    text = report.build(f, junk).render()
    assert text.startswith("Title: ") and "Picture" not in text
    for bad in (None, [], "text", 7):
        assert report.build(f, bad).render().startswith("Title: ")
    weird = Finding("x:y:zzzz", "", "", "", "", "", 0, 0, -5, 0)
    assert report.build(weird, {}).title.startswith("Bombadil found a problem")


# -- the picture --

def box(kind, at, size, **more):
    return {"class": kind, "at": list(at), "size": list(size), "floating": True, "mapped": True, "hidden": False,
            "workspace": 1, "monitor": 0, **more}


def screen(w=1920, h=1080, **more):
    return {"name": "Virtual-1", "width": w, "height": h, "x": 0, "y": 0, "scale": 1.0, "transform": 0,
            "disabled": False, "workspace": 1, "special": "", **more}


def test_a_window_inside_the_screen_is_drawn_to_scale():
    picture = report._picture({"monitors": [screen(640, 480)],
                               "windows": [box("bombadil-app", (160, 120), (320, 240))]})
    lines = picture.splitlines()
    assert lines[-1] == "screen 640x480 (dots)"
    grid = lines[:-1]
    assert len(grid) == 24 and all(len(row) <= 64 for row in grid)   # 64 columns; a cell is twice as tall as wide
    assert grid[0] == "." * 64 and grid[-1] == "." * 64              # the screen, dotted
    assert grid[6] == "." + " " * 15 + "+" + "-" * 30 + "+" + " " * 15 + "."     # a quarter in, half as wide
    app_rows = [row for row in grid if "|" in row]
    assert len(app_rows) == 10 and "app" in app_rows[0]
    assert grid[17].index("+") == 16 and grid[17].rindex("+") == 47            # and a quarter down, half as tall


def test_three_apps_at_one_spot_are_one_box_that_says_so():
    win = box("bombadil-app", (100, 100), (400, 300))
    picture = report._picture({"monitors": [screen()], "windows": [win, dict(win), dict(win)]})
    assert picture.count("+") == 4 and "app x3" in picture


def test_only_bombadils_own_windows_are_named_and_the_rest_are_other_windows():
    wins = [box("foot", (0, 0), (900, 500)), box("firefox", (0, 520), (900, 500)),
            box("bombadil-details", (960, 0), (900, 500)), box("bombadil-app", (960, 520), (900, 500)),
            box("bombadil-flag-of-his-choosing", (10, 10), (30, 30))]
    picture = report._picture({"monitors": [screen()], "windows": wins})
    words = set(re.findall(r"[a-z]+", picture.split("screen 1920")[0]))
    assert {"other", "window", "details", "app"} <= words
    assert not words & {"foot", "firefox", "flag", "choosing"}


def test_the_active_window_and_the_bar_are_marked_and_said_in_the_legend():
    bundle = {"monitors": [screen()], "windows": [box("bombadil-details", (100, 100), (800, 500))],
              "activewindow": box("bombadil-details", (100, 100), (800, 500)),
              "bar": {"screens": {"Virtual-1": {"rects": [{"name": "line", "x": 500, "y": 900, "w": 900, "h": 100}]}}}}
    picture = report._picture(bundle)
    assert "#" in picture and ":" in picture.split("\n")[2] + picture
    assert picture.splitlines()[-1] == "screen 1920x1080 (dots); # is the active window; : is the bar"
    assert "line" not in picture          # the bar's parts are not named, only "bar"


def test_a_window_bigger_than_the_screen_sticks_out_of_the_dotted_frame():
    picture = report._picture({"monitors": [screen(640, 480)], "windows": [box("bombadil-app", (50, 20), (540, 660))]})
    rows = picture.splitlines()[:-1]
    frame_bottom = max(i for i, row in enumerate(rows) if row.startswith("....."))
    assert any("|" in row for row in rows[frame_bottom + 1:])         # the app goes on below the screen


def test_windows_on_another_workspace_are_not_on_the_screen():
    wins = [box("bombadil-app", (100, 100), (300, 300)), box("bombadil-app", (600, 100), (300, 300), workspace=5)]
    picture = report._picture({"monitors": [screen()], "windows": wins})
    assert picture.count("+") == 4
    special = box("bombadil-details", (200, 100), (800, 600), workspace="special:details")
    shown = report._picture({"monitors": [screen(special="special:details")], "windows": [special]})
    assert "details" in shown


def test_the_screen_with_the_windows_on_it_is_the_one_drawn():
    left, right = screen(), {**screen(), "name": "HDMI-A-1", "x": 1920, "workspace": 2}
    wins = [box("bombadil-app", (2000, 100), (300, 300), workspace=2, monitor=1)]
    picture = report._picture({"monitors": [left, right], "windows": wins})
    assert "app" in picture and picture.count("+") == 4


def test_no_windows_no_picture_and_no_screen_is_fine():
    assert report._picture({"monitors": [screen()], "windows": []}) == ""
    assert report._picture({}) == ""
    lone = report._picture({"windows": [box("bombadil-app", (100, 100), (300, 200))]})
    assert "app" in lone and "screen 400x300" in lone


def test_a_hidden_or_unmapped_window_is_not_drawn():
    wins = [box("bombadil-app", (0, 0), (300, 300), hidden=True), box("bombadil-app", (400, 0), (300, 300), mapped=False)]
    assert report._picture({"monitors": [screen()], "windows": wins}) == ""


def test_odd_numbers_never_break_the_picture():
    wins = [box("bombadil-app", (float("nan"), 0), (10, 10)), box("bombadil-app", (0, 0), (-5, 10)),
            box("bombadil-app", (0, 0), (1e300, 1e300)), box("bombadil-app", (10**9, 0), (5, 5)),
            box("bombadil-app", (1, 1), (1, 1)), {"class": 5, "at": "x"}, "text", None,
            box("bombadil-app", (-400, -300), (9000, 9000))]
    picture = report._picture({"monitors": [screen(), screen(0, 0), {"width": True}], "windows": wins})
    assert isinstance(picture, str)
    assert all(len(row) <= 64 for row in picture.splitlines()[:-1])


def test_the_picture_is_at_most_64_columns_and_26_rows():
    tall = report._picture({"monitors": [screen(300, 4000)], "windows": [box("bombadil-app", (10, 10), (100, 3000))]})
    rows = tall.splitlines()[:-1]
    assert len(rows) <= 26 and max(len(r) for r in rows) <= 64


def test_the_real_fixtures_draw_what_the_bug_looks_like():
    f, bundle = bundle_for("apps-stacked-bad", "apps-stacked")
    assert "app x2" in report.build(f, bundle).picture
    f, bundle = bundle_for("monitor-640-bad", "window-oversize")
    picture = report.build(f, bundle).picture
    assert picture.splitlines()[-1].startswith("screen 640x480")


# -- what cannot be in it --

def plant(bundle: dict) -> dict:
    """Every place the evidence could hold his words, filled with them. The shapes are the store's; the
    extra keys are what a careless change to the store, or a hand-made bundle, could add."""
    s = SECRETS
    bundle = json.loads(json.dumps(bundle))
    for w in bundle["windows"]:
        w.update(title=s["window title"], initialTitle=s["window title"], address="0x55a1c0", pid=4242,
                 cmdline=f"firefox {s['home path']}")
    bundle["activewindow"] = {**(bundle.get("activewindow") or {}), "title": s["window title"]}
    bundle["monitors"][0]["description"] = s["his name"]
    bundle["evidence"] = {"prompt": s["prompt"], "screenshot": s["screenshot"], "title": s["window title"],
                          "file": s["home path"], "app": s["app name"]}
    bundle["turn"] = {"id": "1-1", "prompt": s["prompt"], "result": s["window title"], "summary": s["his name"],
                      "tools": {"n": 1, "names": [s["app name"]]}}
    bundle["tool_events"] = [{"name": s["app name"], "ok": False, "error": s["prompt"]}]
    bundle["words"] = ["still", s["prompt"]]
    bundle["screenshot"] = s["screenshot"]
    bundle["log"] = [f"QML error: {s['home path']}:12: {s['window title']} is not defined",
                     f"error: user {s['his name']} opened {s['screenshot']}", f"failed: he asked: {s['prompt']}",
                     "FATAL: daniel's session died"]
    return bundle


def planted_report():
    f, bundle = drawer()
    f = Finding(**{**f.to_dict(),
                   "expected": f"{SECRETS['window title']} is active; see {SECRETS['home path']}",
                   "observed": f"the active window was none; /home/daniel/{SECRETS['app name']}/main.qml said no",
                   "title": "The drawer takes no keyboard"})
    return report.build(f, plant(bundle))


@pytest.mark.parametrize("what", sorted(SECRETS))
def test_nothing_of_his_can_be_read_in_any_form_of_the_report(what):
    secret = SECRETS[what]
    rep = planted_report()
    parts = [secret, *(w for w in re.findall(r"[\w.-]{6,}", secret) if w != "bombadil")]   # the repo's own name
    for form, text in texts(rep).items():
        for part in parts:
            assert part.lower() not in text.lower(), f"{what!r} ({part!r}) is in the {form}"
    assert "daniel" not in " ".join(texts(rep).values()).lower()


def test_nothing_of_his_reaches_the_held_file_either():
    rep = planted_report()
    held = report.hold(rep)
    assert held is not None and held.read_text() == rep.render()
    low = held.read_text().lower()
    assert "daniel" not in low and "layoffs" not in low and "passwords" not in low and ".png" not in low


def test_the_log_is_three_lines_and_a_home_path_in_it_is_a_tilde():
    rep = planted_report()
    assert len(rep.log) == 3 and all(len(line) <= 160 for line in rep.log)
    assert "FATAL" in rep.log[-1]
    f, bundle = drawer()
    mine = report.build(f, {**bundle, "log": ["QML error: /home/daniel/notes/app.qml:12: boom"]})
    assert mine.log == ("QML error: ~ boom",)


def test_log_lines_are_the_last_ones_that_say_something_went_wrong_else_the_last_ones():
    f, bundle = drawer()
    calm = [f"line {i} all is well" for i in range(10)]
    assert report.build(f, {**bundle, "log": calm}).log == ("line 7 all is well", "line 8 all is well",
                                                             "line 9 all is well")
    noisy = calm[:3] + ["an error here"] + calm[3:6] + ["failed there", "warning: x"] + calm[6:]
    assert report.build(f, {**bundle, "log": noisy}).log == ("an error here", "failed there", "warning: x")
    long = "x" * 500 + " error"
    assert len(report.build(f, {**bundle, "log": [long]}).log[0]) <= 160


def test_paths_become_home_or_path_and_system_files_stay():
    line = report._line("open '/home/daniel/a/b.txt' then '~/notes/x.md' and '/mnt/data/taxes/2025.pdf' "
                        "but /usr/lib/qt6/plugins/x.so and /etc/hypr/hyprland.lua are ours, also '/home/someone/else'")
    assert "daniel" not in line and "taxes" not in line and "someone" not in line and "notes" not in line
    assert "/usr/lib/qt6/plugins/x.so" in line and "/etc/hypr/hyprland.lua" in line
    assert report._line("a fraction 1/2 and and/or are words") == "a fraction 1/2 and and/or are words"


def test_his_login_name_and_machine_name_are_taken_out_but_generic_names_are_not(monkeypatch):
    monkeypatch.setattr(report.socket, "gethostname", lambda: "daniels-framework")
    assert report._line("daniels-framework lost the user unit, daniel said so") == "… lost the user unit, … said so"
    monkeypatch.setenv("USER", "user")
    monkeypatch.setenv("LOGNAME", "user")
    monkeypatch.setenv("HOME", "/home/user")
    assert "user unit" in report._line("the user unit failed")


def test_no_key_the_report_does_not_read_can_reach_it():
    """Every string in the evidence that is not a field the report reads is a marker; none may appear."""
    f, bundle = drawer()
    marks = json.loads(json.dumps(bundle))

    def mark(v, path="x"):
        if isinstance(v, dict):
            return {k: (x if k in ("class", "at", "size", "x", "y", "w", "h", "width", "height", "scale", "transform",
                                   "disabled", "workspace", "special", "floating", "mapped", "hidden", "monitor",
                                   "kind", "versions", "log", "fullscreen") else mark(x, f"{path}.{k}"))
                    for k, x in v.items()} | {f"extra_{len(path)}": f"MARK-{path}"}
        if isinstance(v, list):
            return [mark(x, f"{path}[]") for x in v]
        return f"MARK-{path}" if isinstance(v, str) else v
    marks = {k: (v if k in ("versions", "log", "kind") else mark(v, k)) for k, v in marks.items()}
    marks["log"] = []
    text = json.dumps(texts(report.build(f, marks)))
    assert "MARK" not in text
    assert "Picture" in report.build(f, marks).render()       # and the picture still drew from the typed fields


# -- what the card shows --

def test_preview_lists_what_goes_and_what_stays():
    f, bundle = drawer()
    rep = report.build(f, {**bundle, "log": ["QML error: /home/daniel/x.qml:1: bad"]})
    pv = report.preview(rep)
    assert pv.text == rep.render()
    goes = "\n".join(pv.goes)
    assert goes.startswith("What happened: Details drawer takes no keyboard, 3 times on 2 days")
    assert "The build: d9dde3b" in goes
    assert "Versions: Quickshell 0.3.1, Claude Code 2.1.283, codex-cli 0.157.1" in goes
    assert "The failing check and its output: drawer-focus: the drawer is open and the active window is none" in goes
    assert "A picture of the windows as boxes, without titles" in goes
    assert "1 line of the log, with your folders removed" in goes
    assert "patch" not in goes
    assert pv.stays == ("What you typed or said", "Your files and their paths", "The titles of your windows and pages",
                        "Screenshots", "Your name")
    assert set(pv.to_dict()) == {"goes", "stays", "text"}


def test_preview_says_no_more_and_no_less_than_the_report_holds():
    f, bundle = drawer()
    rep = report.build(f, {**bundle, "windows": [], "log": []}, versions={"build": ""})
    goes = "\n".join(report.preview(rep).goes)
    assert "picture" not in goes and "log" not in goes and "build" not in goes.lower().replace("the failing", "")
    assert "Versions" not in goes
    with_patch = report.build(f, bundle, patch="--- a/src/bombadil/launcher.py\n+++ b/src/bombadil/launcher.py\n@@\n-x\n+y\n")
    assert "A suggested patch" in report.preview(with_patch).goes


def test_a_suggested_patch_is_attached_in_a_diff_block_and_never_a_big_one(home):
    f, bundle = drawer()
    diff = "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    text = report.build(f, bundle, patch=diff).render()
    assert "Suggested patch: attached here and never applied on his machine\n```diff\n--- a/src/x.py" in text
    assert report.build(f, bundle, patch="x" * (report.MAX_PATCH + 1)).patch == ""
    folder = home / "ev"
    folder.mkdir()
    (folder / "evidence.json").write_text(json.dumps(bundle))
    (folder / "patch.diff").write_text(diff)
    assert report.build(Finding(**{**f.to_dict(), "evidence": str(folder)})).patch == diff.strip("\n")


def test_a_fence_in_a_log_line_cannot_close_the_block_early():
    f, bundle = drawer()
    text = report.build(f, {**bundle, "log": ["error ``` then more"]}).render()
    assert "````text" in text


# -- the held copy --

def test_the_held_report_is_where_clear_found_looks(home):
    store = FindingsStore()
    obs = observation("apps-stacked-bad")
    r = run_probe("apps-stacked", obs)[0]
    again = run_probe("apps-stacked", obs, retried=True)
    found = store.record_retry(r, again, obs.now, obs=obs, versions=VERSIONS)
    assert found is not None
    rep = report.build(found)
    held = report.hold(rep)
    assert held == Path(store.conn.execute("PRAGMA database_list").fetchone()["file"]).parent / "reports" / (
        Path(found.evidence).name + ".md")
    assert held.exists()
    store.mark(found.fp, "reported")
    assert store.clear_found() == 1
    assert not held.exists()
    store.close()


def test_hold_says_none_when_the_disk_says_no(home, monkeypatch):
    f, bundle = drawer()
    monkeypatch.setenv("BOMBADIL_LOOP", str(home / "a-file"))
    (home / "a-file").write_text("not a directory")
    assert report.hold(report.build(f, bundle)) is None


# -- the link --

def parsed(url):
    parts = urllib.parse.urlsplit(url)
    return parts, {k: v[0] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()}


def test_the_link_is_the_projects_new_issue_page_with_everything_quoted():
    f, bundle = drawer()
    rep = report.build(f, {**bundle, "log": ["odd & ends # 100% [x] ünï 🙂 = ?"]})
    link = report.issue_url(rep)
    parts, query = parsed(link.url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "github.com", "/thedefaultman/Bombadil/issues/new")
    assert query["title"] == rep.title
    assert query["body"] == rep.body()
    assert query["labels"] == "found-by-bombadil"
    assert "odd & ends # 100% [x] ünï 🙂 = ?" in query["body"]
    assert "\n" not in link.url and " " not in link.url and link.url.isascii()
    assert (link.paste, link.copied, link.note) == (False, False, "")


def test_the_link_can_name_another_repository():
    f, bundle = drawer()
    assert report.issue_url(report.build(f, bundle), repo="someone/fork").url.startswith(
        "https://github.com/someone/fork/issues/new?")


def test_a_report_that_fits_is_never_copied():
    f, bundle = drawer()
    copied = []
    link = report.issue_url(report.build(f, bundle), copy=copied.append)
    assert len(link.url) <= report.MAX_URL and copied == [] and not link.paste


def test_a_body_too_long_for_a_link_opens_an_empty_page_and_goes_to_the_clipboard():
    f, bundle = drawer()
    rep = report.build(f, bundle, patch="+" + "a-long-line-of-patch " * 180)
    assert len(urllib.parse.quote(rep.body(), safe="")) > 3000
    copied = []
    big = report.build(f, bundle, patch=("+" + "x" * 70 + "\n") * 55)        # about 4000 characters, 8000 quoted
    link = report.issue_url(big, copy=lambda t: copied.append(t) or True)
    assert link.url == "https://github.com/thedefaultman/Bombadil/issues/new"
    assert (link.paste, link.copied, link.note) == (True, True, "Paste it here")
    assert copied == [big.render()]             # the title comes along: the page is empty


def test_the_limit_is_on_the_quoted_link_and_one_character_either_side_of_it(monkeypatch):
    f, bundle = drawer()
    rep = report.build(f, bundle)
    exact = len(report.issue_url(rep, copy=None).url)
    monkeypatch.setattr(report, "MAX_URL", exact)
    assert not report.issue_url(rep, copy=None).paste
    monkeypatch.setattr(report, "MAX_URL", exact - 1)
    assert report.issue_url(rep, copy=None).paste


def test_no_clipboard_says_so_and_the_card_does_not_promise_a_paste(monkeypatch):
    f, bundle = drawer()
    rep = report.build(f, bundle)
    monkeypatch.setattr(report, "MAX_URL", 100)
    for copy in (lambda t: False, None, lambda t: 1 / 0):
        link = report.issue_url(rep, copy=copy)
        assert link.paste and not link.copied and link.note == ""


def fake_program(tmp_path, monkeypatch, name, body):
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    path = d / name
    path.write_text(f"#!{sys.executable}\nimport sys, time\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(d))
    return path


def test_wl_copy_gets_the_text_on_stdin(tmp_path, monkeypatch):
    out = tmp_path / "clip"
    fake_program(tmp_path, monkeypatch, "wl-copy", f"open({str(out)!r}, 'w').write(sys.stdin.read())")
    assert report.copy_text("Title: x\nSeen: y ünï") is True
    assert out.read_text() == "Title: x\nSeen: y ünï"


def test_no_wl_copy_or_a_broken_one_is_false(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert report.copy_text("x") is False
    fake_program(tmp_path, monkeypatch, "wl-copy", "sys.exit(1)")
    assert report.copy_text("x") is False
    fake_program(tmp_path, monkeypatch, "wl-copy", "time.sleep(30)")
    assert report.copy_text("x", timeout=0.3) is False


# -- has it been reported --

def test_the_fingerprint_is_searched_for_in_the_projects_issues():
    asked = []

    def fetch(url, timeout):
        asked.append((url, timeout))
        return {"items": [{"number": 12, "title": "Something else [fp 111111]", "state": "open"},
                          {"number": 97, "title": "Details drawer takes no keyboard [fp 3c91a0]", "state": "closed"},
                          {"number": 98, "title": "Details drawer takes no keyboard [fp 3c91a0]", "state": "open"}]}

    assert report.already_reported("hypr:drawer-focus:3c91a0", fetch=fetch, timeout=2.5) == 98     # an open one first
    url, timeout = asked[0]
    parts, query = parsed(url)
    assert (parts.netloc, parts.path, timeout) == ("api.github.com", "/search/issues", 2.5)
    assert query["q"] == 'repo:thedefaultman/Bombadil is:issue "fp 3c91a0" in:title'


def test_the_issue_is_the_one_with_that_exact_tag():
    def hit(items):
        return lambda url, timeout: {"items": items}

    assert report.already_reported("a:b:3c91a0", fetch=hit([{"number": 1, "title": "x [fp 3c91a00]"}])) is None
    assert report.already_reported("a:b:3c91a0", fetch=hit([{"number": 1, "title": "no tag here"}])) is None
    assert report.already_reported("a:b:3c91a0", fetch=hit([{"number": "7", "title": "[fp 3c91a0]"}])) is None
    assert report.already_reported("a:b:3c91a0", fetch=hit([{"number": 7, "title": "[fp 3c91a0]"}])) == 7
    assert report.already_reported("a:b:3c91a0", fetch=hit([])) is None
    assert report.already_reported("3c91a0", fetch=hit([{"number": 7, "title": "[fp 3c91a0]"}])) == 7


@pytest.mark.parametrize("answer", [OSError("no route"), urllib.error.URLError("offline"), TimeoutError(), ValueError("x"),
                                    RuntimeError("proxy said 403"), KeyError("items")])
def test_offline_or_anything_else_is_not_reported_and_never_raises(answer):
    def fetch(url, timeout):
        raise answer
    assert report.already_reported("hypr:drawer-focus:3c91a0", fetch=fetch) is None


@pytest.mark.parametrize("answer", [None, [], "html", {"items": "x"}, {"items": [None, 3, "x"]}, {"message": "rate limited"}])
def test_an_answer_of_the_wrong_shape_is_not_reported(answer):
    assert report.already_reported("hypr:drawer-focus:3c91a0", fetch=lambda url, timeout: answer) is None


def test_a_bad_fingerprint_or_repository_asks_nothing():
    def fetch(url, timeout):
        raise AssertionError("asked")
    assert report.already_reported("hypr:drawer-focus:not hex", fetch=fetch) is None
    assert report.already_reported("hypr:drawer-focus:3c91a0", repo="not a repo", fetch=fetch) is None


def test_the_default_fetch_is_bounded_and_offline_is_none(monkeypatch):
    seen = []

    def urlopen(req, timeout):
        seen.append((req.full_url, timeout, dict(req.header_items())))
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(report.urllib.request, "urlopen", urlopen)
    assert report.already_reported("hypr:drawer-focus:3c91a0", timeout=1.5) is None
    url, timeout, headers = seen[0]
    assert url.startswith("https://api.github.com/search/issues?q=") and timeout == 1.5
    assert "Authorization" not in headers      # no token, ever


# -- the browser panel --

class Panel:
    def __init__(self, fail=False, available=True):
        self.calls, self.fail, self.available = [], fail, available

    def panel(self, name, show=True):
        self.calls.append(name)
        if self.fail:
            raise RuntimeError("chromium is not installed")


URL = "https://github.com/thedefaultman/Bombadil/issues/new?title=x"


def test_the_page_opens_in_the_browser_panel_through_its_debugging_port():
    panel, put = Panel(), []
    assert report.open_issue_page(URL, panel, put=lambda u: put.append(u) or True, xdg=lambda u: 1 / 0) == "panel"
    assert panel.calls == ["browser"] and put == [URL]


def test_chromium_that_is_still_starting_is_tried_again_then_xdg_open_is_the_fallback():
    waited, tries = [], []
    answers = iter([False, False, True])
    assert report.open_issue_page(URL, Panel(), put=lambda u: tries.append(u) or next(answers), xdg=lambda u: 1 / 0,
                                  sleep=waited.append) == "panel"
    assert len(tries) == 3 and waited == [0.5, 0.5]
    tries.clear()
    opened = []
    assert report.open_issue_page(URL, Panel(), put=lambda u: tries.append(u) and False, xdg=lambda u: opened.append(u) or True,
                                  sleep=lambda s: None) == "xdg-open"
    assert len(tries) == 5 and opened == [URL]


def test_without_hyprland_or_a_panel_the_page_still_opens():
    tries, opened = [], []
    for panel in (Panel(fail=True), Panel(available=False)):
        assert report.open_issue_page(URL, panel, put=lambda u: tries.append(u) or False,
                                      xdg=lambda u: opened.append(u) or True, sleep=lambda s: None) == "xdg-open"
    assert len(tries) == 2 and opened == [URL, URL]       # one try at the port each: no waiting for a browser nobody started
    assert report.open_issue_page(URL, Panel(available=False), put=lambda u: False, xdg=lambda u: False) == ""


def test_only_the_projects_host_is_opened():
    def never(u):
        raise AssertionError("opened")
    for url in ("file:///etc/passwd", "http://github.com/x", "https://evil.example/https://github.com/", "javascript:1", ""):
        assert report.open_issue_page(url, Panel(), put=never, xdg=never) == ""


class Chromium(http.server.BaseHTTPRequestHandler):
    """Chromium's /json/new as its source has it (devtools_http_handler.cc): PUT only, the query cut at
    its first "&", then unescaped. What it opens is what it keeps."""

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


def test_the_put_is_what_chromiums_debugging_port_expects(monkeypatch):
    Chromium.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), Chromium)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(report, "BROWSER_DEBUG_PORT", server.server_address[1])
    try:
        f, bundle = drawer()
        url = report.issue_url(report.build(f, bundle), copy=None).url
        assert report.open_issue_page(url, Panel(available=False), xdg=lambda u: 1 / 0) == "panel"
    finally:
        server.shutdown()
    assert Chromium.seen == [("PUT", "/json/new", url)]       # the whole link: the body and the label too
    assert "&body=" in url and "&labels=" in url
