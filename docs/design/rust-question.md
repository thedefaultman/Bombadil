# Should Bombadil use Rust?

> **Status:** Partly shipped. The decision holds on `main`, which has no Rust. Of the six problems it says to fix in Python, four are built on `main`; the other two are open in the brain's watcher.  
> Shipped: the decision itself (no Rust of ours on `main` or on any remote branch); problem 1, the agent daemon restarted by a user unit; problem 2, an unreadable CLI line skipped; problem 3, the watcher's lines batched by writer; problem 6, the folder view's index named. The brain is on `main`, and so is its watcher.  
> Designed, no code on `main` or on any branch: problems 4 and 5, the watcher's only loop and its privileges; and a Rust watcher, which the Decision paragraph makes conditional. Idea: a Rust compositor ("later").  
> Built differently from the decision: problem 3 sends the writer's chain once per run of events by one writer, not once per process (section 7).  
> Shipped, and no part of the decision: the coding-session host, the hook signal and the session wrapper that sections 1 and 4 mention. They are on `main` since 2026-11-08 (`src/bombadil/dev.py`, `bin/bombadil-signal`, `share/zellij/config.kdl`), and the ISO lists `zellij`. Nothing in this brief is in progress on a branch.  
> **Code:** nothing to build for the decision itself: `main` has no `.rs` file and no `Cargo.toml`. The fixes are in `packaging/bombadil/root/usr/lib/systemd/user/bombadil-agentd.service` and `share/hypr/bombadil.lua` (problem 1), `src/bombadil/providers.py` and `src/bombadil/agentd.py` (problem 2), and `src/bombadil/brain/watch.py` and `src/bombadil/brain/focus.py` (problems 3 and 6). Problems 4 and 5 sit in `src/bombadil/brain/watch.py` and `packaging/bombadil/root/usr/lib/systemd/system/bombadil-brain-watch.service`.  
> **Design:** [Foundation choices](foundation-choices.md) (section 2, the compositor; section 5, Rust interface toolkits rejected), [Bombadil's Brain](brain-brief.md) (the watcher)  
> **Decided by:** the project owner, 2026-09-30 (option a, in the Decision paragraph below)  
> **Verified:** 2026-11-11 against `main` at `7d17600`: the status above, that no Rust is on `main` or on any remote branch, the state of problems 1 to 6 on `main` and, for the watcher and its unit, on every remote branch that has the brain (the same files on all of them), the lines that section 7 cites for problems 1 and 2, that the coding-session host and its hook signal and session wrapper are on `main` and `zellij` is in `iso/packages.x86_64`, and the 931 lines of `src/bombadil/dev.py` in section 4. The line counts, timings and Rust figures in sections 1 to 4 (the 931 lines excepted), the watcher's libc calls and kernel structs, and the file names and line numbers in section 5 are the survey's of 2026-09-30 and were not measured again.

Answer to the project owner's question of 2026-09-29: should Bombadil use Rust for some of this work, what languages is it written in, and what is Rust. Written 2026-09-30 from a read-only survey of main (d9dde3b) and all 12 remote branches, with measurements. No code was changed.

**Short answer: not yet.** Keep Python and QML. Rust has two good places later: the custom compositor (already planned, Smithay is Rust) and the brain's root file watcher. Every slow or fragile spot found today is a design problem that a rewrite would copy, and those are cheaper to fix in Python.

**Decision (the owner, 2026-09-30 17:46 UTC): option a.** Keep Python and QML, and fix the design problems in section 5 in Python: the watcher's items go to the work on the brain, and the agentd restart and unexpected-line items go to the VM fixes. A Rust watcher comes only if a big write (a system upgrade, a 20,000-file checkout) makes the machine lag after the brain merges, or when the compositor work brings a Rust toolchain into the build. Until then the watcher's edge stays thin (kernel events in, rows out, the logic in the Python service), so a later Rust swap replaces one file.

---

## 1. What Bombadil is written in today

Our own code at the time of the survey (tracked source, docs excluded):

| Language | main | Biggest additions on branches | Used for |
|---|---|---|---|
| Python | 6,917 lines (4,067 product, 2,850 tests) | brain +15,400, app kit +7,900 | agentd, os-mcp, narrator, launcher, Stop, the brain, the app runtime, tests |
| QML (with JavaScript inside) | 824 | app kit +7,900 | the bar, pill, status line, component kit, every generated app |
| Bash | 482 | | installer, rollback, ISO build, VM scripts |
| Lua | 72 | | Hyprland config |
| Small bits | | | SQL (brain's SQLite with FTS5), TOML, JSON, systemd units, KDL (zellij config, unmerged at the survey and on `main` since 2026-11-08), SVG icons, one Windows .cmd |

The brain and the app kit, the two biggest additions in the table's third column, are both on `main` as of 2026-11-08; the counts are the survey's.

**Rust of ours: zero**, on every branch (no .rs file, no Cargo.toml on any of the 12 heads).

Compiled code we stand on, written by others: Hyprland, Qt, Quickshell and Chromium (C++); foot and btrfs-progs (C); and some Rust already: zellij (hosts the coding sessions, which are on `main` since 2026-11-08, and `iso/packages.x86_64` lists it), greetd (login), python-cryptography, and Codex, whose engine was rewritten in Rust ([InfoQ](https://www.infoq.com/news/2025/06/codex-cli-rust-native-rewrite/), [openai/codex codex-rs](https://github.com/openai/codex/tree/main/codex-rs)). Claude Code and Codex are installed with npm (Node).

## 2. Rust in plain words

- **Compiled, like C.** A compiler turns the code into a ready-to-run machine program before it ever runs. That is why it is fast and uses little memory: no interpreter reading the code as it goes, no garbage collector pausing it.
- **Safe, unlike C.** The compiler checks how the program uses memory and refuses to build code that could read freed memory, write past a buffer, or let two threads change the same thing at once. In C those mistakes are most crashes and security holes; in Rust they mostly cannot be written.
- **The price.** A build step before anything runs (seconds, sometimes minutes), and code that is slower to write and change because the compiler is strict.
- **Python is the opposite trade.** No build step: the interpreter reads the code as it runs. Slower and heavier, but the agent can edit a file and rerun it in a second, which is how Bombadil is meant to work.

People say "Rust makes apps faster" because it replaces interpreted or garbage-collected code. That only helps where the program spends its time computing. Bombadil mostly spends its time waiting on the model.

## 3. What was measured

All on a 4-core Linux VM with Python 3.11 (not Arch, not btrfs, no real model turn):

| What | Python | Rust | Note |
|---|---|---|---|
| Parse fanotify events (the watcher's binary kernel messages), 200k events | 2.6 µs/event | 0.08 µs/event | 31x faster, but parsing is only 2.5 of the 30 to 80 µs each event costs today |
| Idle daemon, resident memory | 21 MB (asyncio + stdlib) | 3 MB (tokio + serde) | a floor, not a real port |
| agentd resident memory | 22 MB idle, 24 MB after three turns | | |
| agentd CPU during a long turn | 0.34 s for a 5,800-line replay | | about 1% of a core at real model speed (estimate) |
| Super tap (`bombadil pill`) | 64 ms | 3 ms | lazy imports get Python to about 25 ms |
| Build a small Rust daemon | | 13 s first build, 0.4 s rebuild, 850 KB binary | |
| Brain first index, 96k files | 21 s (15 s with two small patches) | | floor is about 8 s of SQLite plus the file walk; nobody waits on it |
| One open generated app | 106 MB | | mostly Qt, which a Rust host would still load |

## 4. Where Rust fits, piece by piece

| Piece | Verdict | Why |
|---|---|---|
| Custom compositor | **Rust, later** | Smithay is Rust; already the plan ([foundation-choices.md](foundation-choices.md) section 2). No code yet. |
| Brain root file watcher (watch.py, fanotify.py, forks.py, in src/bombadil/brain/, on `main` since 2026-09-30) | **Rust, later** | The only piece that runs as root for the whole uptime and talks straight to the kernel (5 libc calls, 11 hand-copied kernel structs), behind a clean socket. Wait until the brain merges, runs on real btrfs, and its protocol is written down. |
| Tiny one-shot clients (Super tap; hook signal and session wrapper, which are on `main` since 2026-11-08) | Rust only if a compiler enters the build anyway | 64 ms vs 3 ms per tap, 25 ms vs 1.5 ms per hook, nothing a person notices. |
| agentd, narrator, providers, launcher, os-mcp, Stop | Stay Python | Mostly waiting on the model; still edited on six branches; [the self-improvement brief](self-improvement-brief.md) lets the agent mend these, which needs no build step. |
| Brain service (index, search, Focus) | Stay Python | Its slow spots are SQL. |
| App runtime host | Stay Python | Memory and start time are Qt's. Apps can already call any prebuilt program through the Command native, so a fast helper can be added without changing the host. |
| Generated apps, component kit, bar and pill | **Never Rust** | "Agent writes a file, a window appears" is the product. The ISO has no Rust compiler, and [foundation-choices.md](foundation-choices.md) already rejected Rust UIs. |
| Coding session host | Never ours | Already Rust (zellij, about 146k lines); our 931 lines (`src/bombadil/dev.py`, on `main` since 2026-11-08) only orchestrate it. |

## 5. The real problems found (none are about language)

File names and line numbers in this section are those of the survey. `watch.py` and `focus.py` in items 3 to 6 are in `src/bombadil/brain/`, which was not on `main` when the survey was written and is on `main` as of 2026-11-08; section 7 says where each item stands.

A Rust rewrite would copy each of these, so they come first:

1. **Nothing restarts agentd after a crash**, and a restart forgets the conversation. It is started once with `hl.exec_cmd("agentd")` (hyprland.lua:8 on main, same on all 12 branches) and no unit has `Restart=`. Fix: a systemd user unit.
2. **One odd line from the CLI ends the turn and drops its answer.** providers.py calls `.get` on message content without checking its type (`m.get("message", {}).get("content", [])` in the assistant and user branches), and agentd.py:454 calls `source.parse` with no guard. Reproduced: a stub CLI line `{"type":"assistant","message":"..."}` ended the turn with an AttributeError and the real result that followed was never shown.
3. **The watcher's lines are mostly repeated bytes.** Each event repeats the full "who wrote this" chain (997 of a 1,156-byte line), so a 20,000-file write is about 56 MB and overflows the 8 MiB client buffer (brain branch, watch.py:82-83, 429-441). Fix: send the chain once per process.
4. **The watcher blocks its only loop** on `btrfs subvolume find-new` (timeout 120 s, every 60 s, watch.py:838-843) while the kernel queue is unlimited, and runs the whole catch-up before it accepts clients (watch.py:1352, 1361). Fix: run these off the loop.
5. **The watcher runs as unrestricted root** with a socket any user can write to (0666, watch.py:1041) and a unit with no capability limits. Fix: CapabilityBoundingSet, NoNewPrivileges, ProtectSystem.
6. **Brain folder view takes about 450 ms** because SQLite picks the wrong index for the last-writers query (`last_writers`, focus.py:946 on the brain branch); 1.4 ms with the right one, measured on 340k synthetic events.

## 6. Caveats

- Nothing was measured on btrfs, on Arch's kernel or Python, under Hyprland, or during a real model turn.
- The desk and passenger branches were only skimmed (Python and QML glue, nothing Rust-shaped).
- The 3 MB Rust figure is a minimal daemon, not a finished port.

---

## 7. Where this stands

Added 2026-10-01 against `main` at `26843d3`, brought up to date on 2026-11-09 against `main` at `ca7f80a` and checked again on 2026-11-11 against `main` at `7d17600`. Sections 1 to 6 are the survey as written on 2026-09-30.

| Part of the decision | State | Evidence |
|---|---|---|
| Keep Python and QML, write no Rust of ours | Shipped | No `.rs` file and no `Cargo.toml` in the tree (`find . -name '*.rs' -o -name Cargo.toml` finds nothing), and none on any remote branch. `iso/packages.x86_64` lists no Rust toolchain. |
| Problem 1: restart agentd after a crash | Shipped | `iso/airootfs/etc/systemd/user/bombadil-agentd.service` sets `Restart=always`, `RestartSec=1` and `StartLimitIntervalSec=0`, and `hyprland.lua` (line 10) restarts `bombadil-agentd bombadil-shell` through `systemctl --user` in place of `hl.exec_cmd("agentd")`, so the bar comes back too. Until 2026-11-09 the list had `mako` as well; `main` starts no notification daemon, because the bar's own `NotificationServer` (`shell/Notifications.qml`) shows the notifications and restarts with the bar. The conversation also survives a restart: `_load_state` and `_save_state` in `src/bombadil/agentd.py` keep the provider's conversation id and the model in `agentd-state.json` under the runtime directory, though not across a reboot or a later login. Tests: `tests/test_iso_profile.py` and the smoke check `agentd-comes-back` in `iso/airootfs/usr/local/bin/bombadil-smoke`. |
| Problem 2: one unreadable CLI line ends the turn | Shipped | The assistant and user branches of `Claude._events` in `src/bombadil/providers.py` read `message` through `_obj` (lines 351 and 360), and the `pump` function inside `AgentD.turn` in `src/bombadil/agentd.py` (line 2916) passes `source.parse` through `_safely` (defined at line 3272), which logs a line it cannot read and skips it, so `{"type":"assistant","message":"..."}` no longer ends the turn. The rate-limit branch of `Claude._events` (line 340) still calls `.get` on `message` directly; `_safely` catches that. Tests: `tests/test_providers.py` (`test_a_line_of_the_wrong_shape_reads_as_nothing_not_an_error`, `test_the_answer_after_an_odd_line_is_still_read`) and `tests/test_agentd.py`. |
| Problems 3 and 6: the watcher's repeated bytes and the folder view's index | Shipped; problem 3 built differently from the decision | The brain and its watcher are on `main`. `src/bombadil/watch.py` is the `bombadil watch` turn viewer, a different module. `batch_line` and `Hub.deliver_run` in `src/bombadil/brain/watch.py` send the writer's chain once for a run of consecutive events by one writer within one read of the kernel's queue (at most 1,000 events), and the brain unpacks the line with `_unbatch` in `src/bombadil/brain/service.py`; the decision said once per process, and each line stands alone so a spool or a late reader needs nothing before it. The last-writers query in `src/bombadil/brain/focus.py` (line 955) uses `INDEXED BY events_thing_t`. Tests: `tests/test_brain_watch.py` and `tests/test_brain_focus.py` (line 613 checks the query plan). The line format is in [BRAIN.md](../BRAIN.md). |
| Problems 4 and 5: the watcher's only loop and its privileges | Designed, no code on `main` or on any branch (2026-11-11) | In `src/bombadil/brain/watch.py` on `main`, `Btrfs._run` runs `btrfs subvolume find-new` with `subprocess.run` (timeout 120 s) from `read_markers` every 60 s, and `catch_up` runs once before the loop accepts a client, all on the watcher's only loop. The socket is still mode 0666 (`Watcher.listen`), and `bombadil-brain-watch.service` has no `CapabilityBoundingSet`, `NoNewPrivileges` or `ProtectSystem`. The watcher and its unit are the same file on every branch that has the brain. |
| A Rust watcher | Designed, conditional, no code | No `.rs` file exists. The triggers in the Decision paragraph above are not recorded as met: the brain has merged and its protocol is written in [BRAIN.md](../BRAIN.md), but no lag from a big write is recorded, and the watcher's VM check (`tests/vm/samples/brain-watch/`, check 8) measures throughput on 20,000 files, not the machine's lag. |
| A Rust compositor | Idea | No compositor of ours exists, in any language, on `main` or on any branch. It is a direction ("later") in [foundation-choices.md](foundation-choices.md) section 2, not a design. |
| The coding-session host, its hook signal and its session wrapper | Shipped (no part of the decision) | `src/bombadil/dev.py` (931 lines; its `run` is the session wrapper behind `bombadil dev run`), `bin/bombadil-signal` and `share/zellij/config.kdl` are on `main` since 2026-11-08, and `iso/packages.x86_64` lists `zellij`. Tests: `tests/test_dev.py` and the `dev-*` checks of `bombadil-smoke`. [Coding sessions](../architecture/coding-sessions.md) says how far each part of that piece is built. |

[The roadmap](../roadmap.md) tracks the unmerged work; [Bombadil's Brain](brain-brief.md) holds the brain's design.
