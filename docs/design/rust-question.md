# Should Bombadil use Rust?

Answer to Daniel's question of 2026-09-29 ("Should we not use Rust for some of this work? What languages are we using? Explain Rust"). Written 2026-09-30 from a read-only survey of main (d9dde3b) and all 12 remote branches, with measurements. No code was changed.

**Short answer: not yet.** Keep Python and QML. Rust has two good places later: the custom compositor (already planned, Smithay is Rust) and the brain's root file watcher. Every slow or fragile spot found today is a design problem that a rewrite would copy, and those are cheaper to fix in Python.

---

## 1. What Bombadil is written in today

Our own code (tracked source, docs excluded):

| Language | main | Biggest additions on branches | Used for |
|---|---|---|---|
| Python | 6,917 lines (4,067 product, 2,850 tests) | brain +15,400, app kit +7,900 | agentd, os-mcp, narrator, launcher, Stop, the brain, the app runtime, tests |
| QML (with JavaScript inside) | 824 | app kit +7,900 | the bar, pill, status line, component kit, every generated app |
| Bash | 482 | | installer, rollback, ISO build, VM scripts |
| Lua | 72 | | Hyprland config |
| Small bits | | | SQL (brain's SQLite with FTS5), TOML, JSON, systemd units, KDL (zellij layout), SVG icons, one Windows .cmd |

**Rust of ours: zero**, on every branch (no .rs file, no Cargo.toml on any of the 12 heads).

Compiled code we stand on, written by others: Hyprland, Qt, Quickshell and Chromium (C++); foot and btrfs-progs (C); and some Rust already: zellij (hosts the coding sessions), greetd (login), python-cryptography, and Codex, whose engine was rewritten in Rust ([InfoQ](https://www.infoq.com/news/2025/06/codex-cli-rust-native-rewrite/), [openai/codex codex-rs](https://github.com/openai/codex/tree/main/codex-rs)). Claude Code and Codex are installed with npm (Node).

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
| Custom compositor | **Rust, later** | Smithay is Rust; already the plan (foundation-choices.md section 2). No code yet. |
| Brain root file watcher (watch.py, fanotify.py, forks.py) | **Rust, later** | The only piece that runs as root for the whole uptime and talks straight to the kernel (5 libc calls, 11 hand-copied kernel structs), behind a clean socket. Wait until the brain merges, runs on real btrfs, and its protocol is written down. |
| Tiny one-shot clients (Super tap, hook signal, session wrapper) | Rust only if a compiler enters the build anyway | 64 ms vs 3 ms per tap, 25 ms vs 1.5 ms per hook, nothing a person notices. |
| agentd, narrator, providers, launcher, os-mcp, Stop | Stay Python | Mostly waiting on the model; still edited on six branches; the self-improvement brief lets the agent mend these, which needs no build step. |
| Brain service (index, search, Focus) | Stay Python | Its slow spots are SQL. |
| App runtime host | Stay Python | Memory and start time are Qt's. Apps can already call any prebuilt program through the Command native, so a fast helper can be added without changing the host. |
| Generated apps, component kit, bar and pill | **Never Rust** | "Agent writes a file, a window appears" is the product. The ISO has no Rust compiler, and foundation-choices.md already rejected Rust UIs. |
| Coding session host | Never ours | Already Rust (zellij, about 146k lines); our 892 lines only orchestrate it. |

## 5. The real problems found (none are about language)

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
