# Should Bombadil use Rust?

> **Status:** Partly shipped. The decision holds on `main`, which has no Rust. The two fixes it assigns to the agent daemon are built in unmerged work, and the other four belong to the brain (its watcher and its folder view), which is not on `main` either.  
> **Code:** nothing to build for the decision itself: `main` has no `.rs` file and no `Cargo.toml`. The problems it names sit in `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`, `src/bombadil/providers.py` and `src/bombadil/agentd.py`. The watcher is not on `main`.  
> **Design:** [Foundation choices](foundation-choices.md) (section 2, the compositor; section 5, Rust interface toolkits rejected), [Bombadil's Brain](brain-brief.md) (the watcher)  
> **Decided by:** the project owner, 2026-09-30 (option a, in the Decision paragraph below)  
> **Verified:** 2026-10-01 against `main` at `26843d3`: the status above, that no Rust is on `main` or on any remote branch, and the state of problems 1 and 2 on `main` (section 7). The line counts, timings and Rust figures in sections 1 to 4, the file names and line numbers in section 5, and the claims about the brain's watcher and the coding-session host (work that is not on `main`) are the survey's of 2026-09-30 and were not measured again.

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
| Small bits | | | SQL (brain's SQLite with FTS5), TOML, JSON, systemd units, KDL (zellij layout, unmerged), SVG icons, one Windows .cmd |

**Rust of ours: zero**, on every branch (no .rs file, no Cargo.toml on any of the 12 heads).

Compiled code we stand on, written by others: Hyprland, Qt, Quickshell and Chromium (C++); foot and btrfs-progs (C); and some Rust already: zellij (hosts the coding sessions, in unmerged work, not on `main`), greetd (login), python-cryptography, and Codex, whose engine was rewritten in Rust ([InfoQ](https://www.infoq.com/news/2025/06/codex-cli-rust-native-rewrite/), [openai/codex codex-rs](https://github.com/openai/codex/tree/main/codex-rs)). Claude Code and Codex are installed with npm (Node).

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
| Brain root file watcher (watch.py, fanotify.py, forks.py, in src/bombadil/brain/ and not yet on main) | **Rust, later** | The only piece that runs as root for the whole uptime and talks straight to the kernel (5 libc calls, 11 hand-copied kernel structs), behind a clean socket. Wait until the brain merges, runs on real btrfs, and its protocol is written down. |
| Tiny one-shot clients (Super tap; hook signal and session wrapper, which are in unmerged work) | Rust only if a compiler enters the build anyway | 64 ms vs 3 ms per tap, 25 ms vs 1.5 ms per hook, nothing a person notices. |
| agentd, narrator, providers, launcher, os-mcp, Stop | Stay Python | Mostly waiting on the model; still edited on six branches; [the self-improvement brief](self-improvement-brief.md) lets the agent mend these, which needs no build step. |
| Brain service (index, search, Focus) | Stay Python | Its slow spots are SQL. |
| App runtime host | Stay Python | Memory and start time are Qt's. Apps can already call any prebuilt program through the Command native, so a fast helper can be added without changing the host. |
| Generated apps, component kit, bar and pill | **Never Rust** | "Agent writes a file, a window appears" is the product. The ISO has no Rust compiler, and [foundation-choices.md](foundation-choices.md) already rejected Rust UIs. |
| Coding session host | Never ours | Already Rust (zellij, about 146k lines); our 892 lines (in unmerged work, not on `main`) only orchestrate it. |

## 5. The real problems found (none are about language)

File names and line numbers in this section are those of the survey. `watch.py` and `focus.py` in items 3 to 6 are in `src/bombadil/brain/`, which is not on `main`; section 7 says where each item stands.

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

Added 2026-10-01 against `main` at `26843d3`. Sections 1 to 6 are the survey as written on 2026-09-30.

| Part of the decision | State | Evidence |
|---|---|---|
| Keep Python and QML, write no Rust of ours | Shipped | No `.rs` file and no `Cargo.toml` in the tree (`find . -name '*.rs' -o -name Cargo.toml` finds nothing), and none on any remote branch. `iso/packages.x86_64` lists no Rust toolchain. |
| Problem 1: restart agentd after a crash | In progress, not on `main` | `iso/airootfs/etc/skel/.config/hypr/hyprland.lua` line 8 still starts it with `hl.exec_cmd("agentd")`, and no unit in the tree sets `Restart=`. The fix, a systemd user unit, is built in unmerged work. |
| Problem 2: one unreadable CLI line ends the turn | In progress, not on `main` | `Claude._events` in `src/bombadil/providers.py` (lines 287 and 294) still calls `.get` on `message` without checking its type, and the `pump` function inside `AgentD.turn` in `src/bombadil/agentd.py` (line 1335) does not guard `source.parse`. `Claude.parse` raises `AttributeError` on `{"type":"assistant","message":"..."}`. The fix is built in unmerged work. |
| Problems 3 and 6: the watcher's repeated bytes and the folder view's index | In progress, not on `main` | The brain and its watcher (`src/bombadil/brain/watch.py` in unmerged work) are not on `main`. `src/bombadil/watch.py` on `main` is the `bombadil watch` turn viewer, a different module. In the unmerged work the watcher batches events by writer (`batch_line` in `watch.py`) and the last-writers query uses `INDEXED BY events_thing_t` (`focus.py`), which fixes problems 3 and 6. |
| Problems 4 and 5: the watcher's only loop and its privileges | Designed, no code on any branch (2026-10-01) | In the unmerged watcher the catch-up and generation reads still run on its only loop, the socket is still mode 0666, and its unit has no `CapabilityBoundingSet`, `NoNewPrivileges` or `ProtectSystem`. |
| A Rust watcher, a Rust compositor | Not built | No brain watcher and no compositor of ours is on `main`, in any language. The compositor is a direction in [foundation-choices.md](foundation-choices.md) section 2; the Rust watcher is conditional on the triggers in the Decision paragraph above. |

[The roadmap](../roadmap.md) tracks the unmerged work; [Bombadil's Brain](brain-brief.md) holds the brain's design.
