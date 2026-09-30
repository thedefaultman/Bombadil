# AI Linux distro: foundation choices

Each section leads with the recommendation, then the alternatives. Answer with one word per choice (the **bold** word).

---

## 1. Base distro → **Arch**

**Why:** the agent works best with a plain, imperative system (`pacman -S`, edit a file, restart a service), which is exactly what Claude and Codex have seen the most of. Rolling release keeps Mesa, Wayland and kernel drivers current, which matters for a graphics-heavy shell. `archiso` makes building our own ISO simple.

The "no guards" goal needs an undo button instead of a permission wall, so pair Arch with **btrfs + snapper**: take a snapshot before every agent turn that touches the system, and boot into any old snapshot from the bootloader.

| Option | Pros | Cons |
|---|---|---|
| **Arch** (recommended) | Agents know it well; fresh packages; AUR covers almost everything; easy custom ISO | Rolling updates can break things (snapshots cover this); you own more of the maintenance |
| **NixOS** | Whole OS is one config file; perfect rollback; reproducible images for free | Agents stumble on Nix errors; "install this and try it" becomes "edit the flake and rebuild", which is slow and breaks the natural feel; generated apps have to be packaged |
| **Debian** | Very stable; huge docs | Old graphics stack and toolkits; slower to get the latest Wayland features |
| **Fedora Atomic / bootc** | Image-based updates with rollback; modern stack | Read-only root fights an agent that should be able to change anything |

---

## 2. Display stack → **Hyprland** now, custom compositor later

**Why:** the browser requirement rules out a raw framebuffer shell, because Chromium and every real app need a Wayland (or X) server. Hyprland already does smooth slide-in animations, "special workspaces" that act like drawers, and has a socket the agent can drive (`hyprctl dispatch ...`) to move, open and animate windows. That gets a working "browser slides in" demo in days. Once the UX is proven, a custom compositor built on **Smithay** (Rust) or **wlroots** gives full control over how panels behave.

The shell itself (agent bar, panels, notifications) would be **Quickshell**, a toolkit for writing Wayland desktop shells in QML, so the shell and generated apps share one UI language (see section 5).

| Option | Pros | Cons |
|---|---|---|
| **Hyprland** (recommended start) | Animations and drawers built in; agent control via IPC; mature | Its window model is tiling-first; some UX ideas will hit its limits |
| **Custom** compositor (Smithay/wlroots) | Total control over every panel and transition; truly "our OS" | Months of work before anything else is usable; you maintain input, multi-monitor, HiDPI, etc. |
| **Niri** / **Sway** | Stable, simple | Fewer animation hooks than Hyprland |
| **Framebuffer** custom shell | Minimal, true bare-metal | No browser, no GPU apps, no third-party apps; dead end for this goal |

---

## 3. First target → **VM**, with an ISO that also boots real hardware

**Why:** a VM (QEMU with virtio-gpu) gives fast rebuild and test loops and cheap snapshots while the core is changing daily. Build it with `archiso` so the same ISO boots on your laptop whenever you want to try it.

| Option | Pros | Cons |
|---|---|---|
| **VM** (recommended) | Fast iteration; nothing to break; easy to share with others | GPU acceleration in VMs is weaker, so animations won't feel as smooth as bare metal |
| **Hardware** | True bare-metal feel; real GPU, Wi-Fi, suspend | Slow loop; driver issues distract from the actual product early on |

If you already have a spare machine, testing on it every week or two catches hardware problems early.

---

## 4. How the agent runs as the OS → **CLIs**: official agent CLIs as the engine, our own daemon around them

**Why:** run each vendor's official agent (Claude Code headless / Agent SDK, and Codex CLI) under a small daemon of ours (`agentd`) that starts at login as the session's core. The daemon owns the conversation, the UI connection and snapshots; the vendor CLI does the thinking and tool use. Your normal Claude or ChatGPT login then works as is, and we get vendor improvements for free.

The agent runs as your user with passwordless sudo and the CLIs in their full-auto mode (Claude Code `bypassPermissions`, Codex full access), matching "no guards". The safety net is the btrfs snapshot per turn, plus a visible activity log.

| Option | Pros | Cons |
|---|---|---|
| **CLIs** wrapped by our daemon (recommended) | Subscriptions and logins just work; best tool use each vendor offers; less code for us | Two different CLIs to adapt; bound to their release cycles |
| **API** direct (own agent loop on raw model APIs) | One loop, full control, easiest to add more providers | We rebuild tool use, context management and file editing that the CLIs already do well; API billing only, no subscription login |
| **Terminal** (just autostart the CLI in a full-screen terminal) | Working in an hour | Feels like a terminal, not an OS; no panels or native UI |

---

## 5. How generated apps are built and shown → **QML** (Qt Quick, via PySide6)

**Why:** QML is declarative and interpreted, so the agent writes a file and the window appears in about a second, with no compile step. It has fluid animations built in, runs as a real native Wayland window (no ports, no browser), and hot reloads while the agent edits it. Python behind it gives the agent easy access to files, sqlite, D-Bus and system APIs. Apps live in `~/Apps/<name>/` and get a launcher entry automatically.

| Option | Pros | Cons |
|---|---|---|
| **QML** + Python (recommended) | Instant, no build; animations; hot reload; same language as the Quickshell shell | Qt adds roughly 200 MB to the image |
| **GTK4** + Python (libadwaita) | Native GNOME look; also no build step | Custom animations and unusual layouts are harder; different language from the shell |
| **Rust** (egui / iced / Slint) | Fast, small binaries | Compiles take 30 s to minutes and fail often, which breaks the "created on the spot" feel |
| **Tauri / webview** | Agents are great at HTML | It is still a web app inside a window, which is what you said you don't want |

---

## 6. Provider switching (Claude, Codex, ...) → **MCP** as the shared tool layer

**Why:** everything the OS lets the agent do (open or slide in a panel, launch or show a generated app, take a screenshot, control the browser, notify, snapshot/rollback) is exposed as one **MCP server** (`os-mcp`). Claude Code and Codex both speak MCP, so switching providers means pointing `agentd` at a different CLI; the OS abilities stay the same. The installer asks which provider, runs its login, and writes one config line.

| Option | Pros | Cons |
|---|---|---|
| **MCP** shared tools (recommended) | Same OS abilities for every provider; new providers are cheap | Each provider's quirks still need a small adapter in `agentd` |
| **Per-provider** integrations | Can use each vendor's unique features deeply | Every OS feature built twice; providers drift apart |

---

## 7. The browser → **Chromium**, driven over CDP

**Why:** Chromium exposes the DevTools Protocol, so the agent can see and act in the page (via a Playwright-style MCP) while you watch it in the slide-in panel. Firefox automation support is weaker.

| Option | Pros | Cons |
|---|---|---|
| **Chromium** (recommended) | Best agent automation; runs natively on Wayland | Google-flavored; heavier |
| **Firefox** | Independent engine, privacy | Automation is less complete for agents |

---

## Suggested first milestone (once you pick)

A VM ISO that boots straight into Hyprland + a Quickshell agent bar, `agentd` running Claude Code with `os-mcp`, and three demos: "open the browser" (slides in), "make me a password manager" (QML app appears in seconds), and "undo that" (snapshot rollback).
