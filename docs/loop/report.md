# Report, versions and runner

Three modules in `src/bombadil/loop/`. The runner looks at the machine and keeps what the probes find; `versions` says what it runs on; the report is what a finding becomes when he decides to send it. Nothing here is on a turn's path, and nothing raises: a broken input costs one line on stderr.

## What they do

- **`runner.py`** collects what the probes look at (hyprctl, coredumps, agentd, the bar's files, the turn ledger and logs, the apps' status files, failed units), runs the probes, looks again 500 ms later at every invariant that is red, and hands the result to the findings store. Hyprland's events decide when to look; while he is away it looks once a minute, and once a day it runs the doctor.
- **`versions.py`** asks for the build, Hyprland, Quickshell, Claude Code, Codex and the kit, once each per process, with a 4 s timeout. Anything missing is `"unknown"`.
- **`report.py`** builds the report from typed fields, shows what goes and what stays, and makes the new-issue link. He presses Submit himself: no token is stored and `gh` is not used.

## `runner`

```
Runner(collectors=None, store=None, versions=None, *, clock=time.time, sleep=time.sleep, presence=None)
    collectors   a Collectors, or a dict {field: callable}; a field left out is "not checked"
    store        a FindingsStore (opened on first use, closed by close(), when none is given)
    versions     a dict, or a callable giving one (versions.collect when none)
  .run_once(ids=None, now=None, *, fresh=None) -> [Finding]   look, look again, record; never raises
  .check(ids=None, now=None) -> [Result]                      what `bombadil probe` prints; records nothing
  .run_event(name, data="", now=None) -> [Finding]            openwindow / closewindow / activespecial
  .tick(now=None) -> [Finding]                                what is due; call about once a second
  .next_due() -> float | None                                 when tick next has a check to run
  .doctor(now=None) -> [Finding]                              doctor_live(), doctor.json, findings
  .serve(stop=None, stream=None)                              the prober's loop, until `stop` is set
  .observe(fields=None, now=None) -> Observation              one bounded look
  .close()
  .details_at                                                 when the drawer last opened (set by events)
Collectors(hyprland=None, hypr_timeout=3.0, clock=time.time, agentd_timeout=2.0)
  .clients() .monitors() .layers() .activewindow() .configerrors()   hyprctl -j, bounded
  .bar() .events() .agentd_started()        bar.json, signals.jsonl (3 days), agentd.json
  .agentd() -> {connected, ponged, latency, error}   a real connection and a ping answered within 2 s
  .coredumps() .failed_units()              coredumpctl (kept 5 min), systemctl --user --failed
  .ledger() .tool_results() .turn_errors()  turns.jsonl tail and the per-turn logs it names
  .groups() .medians()                      read-only from loop.db
  .apps()                                   status files and log tails of apps seen in 3 days
  .begin() .refresh()                       a new look starts / forget what is kept between looks
Presence(idle=600, locked=None).away(now) -> bool    no prompt for 10 minutes, or hyprlock running
EventStream(path=None, backoff=(0.5, 1, 2, 5, 10, 30)).poll(timeout) -> [(name, data)]   .close()
doctor_live(collectors=None, *, os_mcp=None, canary=None) -> [{"name", "ok", "detail"[, "skipped"]}]
bounded(fn, timeout) -> (done, value)       find_instance() adopt_instance() socket2_path()
read_events() read_bar() read_agentd() tail_lines() agentd_liveness()      main()
```

The Observation fields are collected only for the probes that are asked for (`PROBES[id].needs`), plus the two small files (`bar`, `events`). `run_once` with `ids` forgets what the collectors keep between looks; the periodic run does not.

Doctor checks: `agentd-ping`, `bar-layer`, `hypr-config`, `units`, `os-mcp` (starts os-mcp and lists its tools within 20 s), `canary-apps` (`bombadil-app check` on the app template and `share/canary/*`). A check that does not apply is `ok` with `skipped: true`. The first three have probes of their own; a failing `os-mcp` or `canary-apps` is kept as a finding with probe id `doctor`.

## `versions`

```
build() hyprland() quickshell() claude_code() codex() kit() machine() -> str
collect(refresh=False) -> {"build", "machine", "Hyprland", "Quickshell", "Claude Code", "codex-cli", "kit"}
capture(cmd, timeout=None, input=None) -> Captured(out, err, code) | None     reset()
```

`build()` is `signals.build_id()` when that imports, else a `VERSION` file beside `paths.share_dir()`, else the git short hash, else `""`. `collect()` asks the programs side by side, so it takes one timeout at worst. A program that was absent is asked again after five minutes. `machine()` is `BOMBADIL_MACHINE` when set, else `VM` or `container` from `systemd-detect-virt`, else `laptop` or `desktop` from the chassis, else `""`: the report says the machine only when it is known.

## `report`

```
build(finding, bundle=None, *, versions=None, machine=None, patch=None, tried=None) -> Report
Report.render() -> str         "Title: ...\nSeen: ...\nExpected: ...\nObserved: ...\nPicture: ...\n..."
Report.body()   -> str         render() without the Title line (what the issue's body holds)
Report.title .seen .picture .log .versions .tried .patch .to_dict()
preview(report) -> Preview(goes, stays, text)        the two lists of the Send card, and the exact text
hold(report) -> Path | None                          reports/<fp>.md, where `clear_found` looks
issue_url(report, repo="thedefaultman/Bombadil", copy=copy_text) -> Link(url, paste, copied, note)
already_reported(fp, repo=..., fetch=None, timeout=4.0) -> int | None     an issue number, or None
open_issue_page(url, hyprland=None) -> "panel" | "xdg-open" | ""
copy_text(text) -> bool                              wl-copy, when there is one
```

The report reads only typed fields: the finding's counts and sentences, the build and versions, window rectangles and class kinds, and at most three log lines. The picture is 64 columns of scaled boxes with no titles; Bombadil's own window classes are named and everything else is "other window". The few free-text fields are cut to one line, then every path becomes `~` or `<path>` (system paths stay), any title or prompt the evidence holds is taken out, and his login and machine names are removed. That second lock is a belt: the tests plant his words in every key the evidence could hold and assert that none of it reaches `render()`, `preview()`, `to_dict()`, the held file or the link.

A link over about 7000 characters after quoting opens the empty new-issue page (`paste=True`) and the whole report (title included) goes to the clipboard; the card says "Paste it here" when it got there. `already_reported` searches the project's issues for `[fp <hash>]` and is for after he pressed Send. `open_issue_page` slides the browser panel in, PUTs `/json/new?<url>` to Chromium's port 9222 (retrying while Chromium starts), and falls back to `xdg-open`. Only `https://github.com/` links are opened.

## Traps

- **One thread owns the store.** `FindingsStore` is an SQLite connection. The runner uses it from the thread that calls `run_once`, `tick` and `run_event`, and schedules delayed checks in `tick` rather than with timers. The 500 ms retry sleeps in that thread.
- **`hyprctl` has no timeout of its own.** Every call goes through `bounded()`. A call that hangs costs one timeout per look (the rest of that look is skipped) and its thread is left to finish; four stuck threads stop new calls until one comes back.
- **Window checks wait a second.** A window event schedules the layout probes at +1 s (`SETTLE`), because a window that is still being placed looks stacked or oversize and would flip red-green, which quarantines a good probe. The drawer is judged at +2.1 s, after the probe's own grace.
- **Tool errors are os-mcp's only.** `tool_results` keeps `mcp__bombadil-os__*` results; a failing Bash command in a turn is his shell's, not Bombadil's.
- **App names are his words.** The runner reads them for its own use (it must find the status file) but they reach no finding: the apps probes leave them out, and the report cannot read them.
- **Coredumps of Python programs** show as `python3`. The runner asks `coredumpctl info` (for the ten newest recent ones, as `coredumpctl list` is oldest first) for the command line, so agentd and `bombadil-app` are told apart from other scripts, however many dumps of other Python programs came before.
- **Presence is asked rarely.** Away is one pgrep and a few file reads, so it is asked at most every 15 s and only when a run or the doctor could be due. With nothing to go on (a fresh install) he is present.
- **A kernel detail.** Right after a process is started, `/proc/<pid>/cmdline` can show a part of the command line for a moment; tests that start a stand-in app wait for it.
- **`bombadil-app check`** may not exist yet in an older kit: the canary check is then skipped, not failed.
- **A user unit does not always have `HYPRLAND_INSTANCE_SIGNATURE`.** `adopt_instance()` (called by `main()`, and at the start of every look by `Collectors.begin()`, since the prober outlives the compositor) finds the newest live instance. An instance is live while its socket is there and the process in its `hyprland.lock` exists.
- **Evidence logs.** A finding about Hyprland carries the tail of `hyprland.log`; one about an app, that app's log. agentd and the bar start from `hyprland.lua` with the session's own output and have no unit, so a finding about them carries no log and the report has no Log section.

## What needs a real machine

- That the shapes in the fixtures are what a real `hyprctl -j clients|monitors|layers|activewindow` prints (already listed in `probes.md`), and that `Hyprland().request("j/...")` answers in under 3 s under load.
- The event names and data shapes from `.socket2.sock` (`openwindow>>ADDR,WS,CLASS,TITLE`, `activespecial>>special:details,MON`): built from the documented format, never seen live.
- That the browser panel takes the PUT on port 9222 and opens the new-issue page in it. The test talks to a local HTTP server that accepts a PUT; Chromium itself was not run.
- The `found-by-bombadil` label: GitHub ignores a label that does not exist on the repository, and the issue opens without it.
- `coredumpctl info` line names (`PID: 1234 (python3)`, `Command Line: ...`) and `systemctl --user --failed --plain` output, written from memory of systemd.
- That a real agentd answers the ping within 2 s under load, and `bombadil-app check <dir> --wait 800` prints `{ok, errors}` as the kit's status does.
- The machine kind: `systemd-detect-virt` and the DMI chassis type on the laptop.

## Known gaps

- agentd and the bar have no log file of their own, so findings about them carry no log lines; a file under the state directory that `hyprland.lua` redirects each one to would give them one, but it needs a size limit decided first.
