# The command line, the prober's unit and the os-mcp tool

What the user, the agent and systemd touch the loop with. Nothing here is on a turn's path, and nothing
calls a model. Contract: `docs/LOOP.md`. Files: `bin/bombadil` (the loop, probe and doctor commands),
`bin/bombadil-probe`, `iso/airootfs/etc/systemd/user/bombadil-probe.service`,
`start_prober` in `src/bombadil/loop/service.py`, `src/bombadil/mcp_server.py` (the `asks` tool), the four loop
checks at the end of the loop block of `bombadil-smoke`. Tests: `tests/test_loop_cli.py`,
`tests/test_mcp_asks.py`, the unit and smoke tests in `tests/test_iso_profile.py`.

## Commands

| Command | What it does | Exit |
|---|---|---|
| `bombadil loop asks [-n N] [--json]` | `asks_text` (or `asks_report` as JSON): what he asks most, 20 by default | 0 |
| `bombadil loop status` | counted, offers waiting or resting, what was found, the prober, the idle doctor, agentd (a real ping), the bar, build and versions | 0 |
| `bombadil loop replay [FILE] [--json] [--logs DIR] [--app NAME]... [--pairs N]` | `LoopStore.replay` in memory: what counted and why not, the groups, what would have been offered, and the pair sheet for hand labelling (100 pairs) | 0; 1 for a FILE that is not there |
| `bombadil loop report [FP]` | the held report for a finding, else the report built from its evidence; nothing is held, marked or sent | 0; 1 no such finding; 2 which one? |
| `bombadil loop forget [--yes]` | without `--yes` says what it would forget; with it, forgets | 0; 1 if it could not |
| `bombadil loop probe [ID...]`, `bombadil probe [ID...]` | `Runner().check`: one line per check; a red one adds `expected:` and `observed:`; "not checked" is neither red nor fine | 1 if any is red; 2 unknown ID |
| `bombadil doctor` | what the idle doctor last wrote (`doctor.json`) | 1 if a check failed |
| `bombadil doctor --live` | `runner.doctor_live()`: looks now, up to a minute; `ok`, `failed` or `skipped` and the detail | 1 if a check failed |

`FP` is the fingerprint (`hypr:apps-stacked:3c91a0`), its hash (the `[fp 3c91a0]` in a report's title) or the
start of it. A bare `bombadil loop report` names the one finding that waits, or lists them.

Plain words, never a traceback: a broken file costs one line on stderr and the command carries on as if
there were nothing in it.

`bombadil ask` is not a loop command, but the loop depends on it: it marks its prompt `"origin":"cli"`, so
what a script, a cron job or another tool asks through it is never counted as something he types. (The pill
sends no origin, which agentd reads as typed; agentd does not guess one for a message that has none.)

## Looking never writes

The read commands open `loop.db` with `mode=ro` (a `LoopStore` whose `_open` is the read-only connection,
defined in `bin/bombadil` and again in `mcp_server.py`), never create it, and treat an absent file, an
empty one or one that is not a database as "nothing counted yet" (exit 0; the last of those says so on
stderr). `FindingsStore` on the same connection works only when its tables are complete, which they are
once the prober has run; otherwise it is "nothing found". A read-only open of a WAL database may leave the
empty `loop.db-wal` and `loop.db-shm` beside it; the file itself is never touched.

`forget --yes` is the one command that writes. agentd's loop service owns the write connection, so when
agentd answers on its socket the command sends `{"type":"noticed_do","op":"forget_asks"}` and prints the
`noticed_result` text (10 s to answer; no answer is an error, and never a fall back to opening the file
behind agentd's back). When nothing listens it opens the store itself.

`bombadil doctor` without `--live` reads what the prober's once-a-day doctor wrote rather than running
`Runner.doctor()`, which records findings: a command that only reads must not.

`bombadil loop status` has no "last look" of the prober to read (the runner writes no heartbeat), so it
says whether `bombadil-probe.service` is active and when the idle doctor last looked.

## The prober's unit

`bombadil-probe.service` runs `/usr/local/bin/bombadil-probe` (linked by `build-iso.sh` like the other
programs) at `Nice=15`, idle CPU and idle I/O, `Restart=always`, `RestartSec=5`, and default `KillMode` so it
stops with its process group. It is not `PartOf`, `BindsTo` or ordered against agentd: it has to outlive an
agentd crash to see it.

agentd's loop service starts it: when agentd comes up, `start_prober` runs `systemctl --user start
--no-block bombadil-probe.service` off the event loop (a no-op when it already runs). That leaves `hyprland.lua`
and any agentd or bar unit alone, whoever owns them, and works the same however agentd was launched. A machine
with no user systemd (a dev session, the headless test) has no prober; one line on stderr says so and
`bombadil loop status` reports the prober as not running.

`StartLimitIntervalSec=0` is under `[Unit]`: systemd ignores it under `[Service]` (`systemd-analyze verify`
says "Unknown key name"), which is where the agentd and bar units have it today.

The prober finds Hyprland itself (`runner.adopt_instance()`): a user unit does not get
`HYPRLAND_INSTANCE_SIGNATURE`. It does so again at the start of every look (`Collectors.begin()`), because
the unit outlives the compositor: a Hyprland that restarted under a new signature is followed, not left
as stale `hyprctl` answers that make every compositor check "not checked". An instance counts as running
only while the process its `hyprland.lock` names exists, so a crashed Hyprland that left its socket behind
is not mistaken for the live one (with no lock to read, the socket alone decides). With no Hyprland it
runs and idles, so the unit is fine on a machine without a session yet.

## The `asks` tool (os-mcp)

Read-only, one optional argument `limit` (1 to 50, 10 when absent or odd). Returns `asks_text`, or
`Nothing counted yet.` when `loop.db` is absent or unreadable. It never creates the loop directory. The
model sees the person's own sentences, as it does in every turn.

## Smoke checks

In the loop block of `bombadil-smoke`: `loop-probe-unit-active` (the unit is active), `loop-status-answers`
(`bombadil loop status` exits 0), `loop-doctor-live` (`bombadil doctor --live` exits 0, up to 3 minutes
allowed) and `loop-asks-tool` (`mcp_call asks '{}'`). They run as the user like their neighbours.

## Traps

- `bombadil loop status` takes up to about 4 s (versions) and 2 s (the ping): it is for a person, not a key bind.
- `doctor --live` runs `bombadil-os-mcp` and `bombadil-app check` on the canary apps; it needs Hyprland's
  socket for two of its checks and fails them without it.
- `bombadil probe` is `Runner.check`: it records nothing, so it never quarantines or counts a finding, and it
  does not skip a quarantined probe.
- The `Server` and `Hyprland` stand-ins in `tests/test_loop_cli.py` speak agentd's and Hyprland's sockets; the
  commands run in a PATH of three stand-in programs (`python3`, `systemctl`, `bombadil-app`).

## What needs a real machine

- `bombadil-probe.service` starting when agentd comes up, and `systemctl --user is-active` saying so
  (only `systemd-analyze verify` on the unit file was run, which found no fault in it).
- `loop status` and `doctor --live` against a real agentd, bar and Hyprland; `doctor --live` inside the VM smoke.
- `Nice`, `CPUSchedulingPolicy=idle` and `IOSchedulingClass=idle` being accepted in a user manager.
- `bombadil loop replay` on his own `turns.jsonl` (the precision check in `counting.md`).
