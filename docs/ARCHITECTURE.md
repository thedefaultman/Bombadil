# Architecture

## Decisions (2026-09-27)

| Choice | Pick | Why |
|---|---|---|
| Base | Arch + btrfs/snapper | Agents know it; rolling graphics stack; archiso; snapshots are the undo |
| Display | Hyprland now, custom compositor later | Slide-in special workspaces and IPC out of the box; a browser needs a real Wayland server |
| First target | QEMU VM, ISO also boots hardware | Fast loop while the core changes daily |
| Agent runtime | Official Claude Code / Codex CLIs wrapped by `agentd` | Logins and subscriptions just work; vendor tool use for free |
| Generated apps | QML via PySide6, `~/Apps/<name>/` | No build step, hot reload, real windows, no ports |
| Provider switching | One MCP server (`bombadil-os-mcp`) | Same OS abilities whatever the provider |
| Browser | Chromium with remote debugging on | Agent can drive it while you watch |

## One turn

1. The bar (or `bombadil ask`) writes `{"type":"prompt","text":…}` to the socket.
2. `agentd` takes a snapper snapshot named `turn:<n>: <prompt>`.
3. It runs the provider CLI once, in full-access mode, resuming the previous session id,
   with `bombadil-os-mcp` in its MCP config and `providers.SYSTEM_PROMPT` appended.
4. The CLI's JSON stream becomes `text` / `tool` / `result` events, broadcast to all clients.
5. The turn is appended to `~/.local/state/bombadil/turns.jsonl`.

"Undo that" is the agent calling `rollback` (or the user running `bombadil undo`): snapper
rolls the root subvolume back to the last `turn:` snapshot; it applies on reboot. Home is
its own subvolume and is not rolled back.

## Generated apps

`create_app` writes `main.qml`, optional `app.py` (a `Backend(QObject)` exposed as
`backend`), `app.toml` and a `.desktop` entry, checks the app offscreen (errors with
`file:line` plus a screenshot go back to the agent), then starts `bombadil-app run <name>`.
The runtime owns the window and reloads the QML into it on every write, so the agent
iterates by calling `create_app` again and the app keeps its place, size and saved state.
Each app lives in its own Hyprland special workspace and gets a chip in the bar.

`import Bombadil` is the app kit (`share/qml/Bombadil`, native types in
`src/bombadil/appkit/native`), and the `bombadil-apps` skill in `share/skills` tells the
agent how to use it. The skill reaches both CLIs from `/etc/skel` (`~/.claude/skills` and
`~/.agents/skills`) and through the `app_guide` tool.

## Next

- Boot the ISO in QEMU and fix what the real Hyprland session shows (bar layering,
  app window rules, greetd autologin, snapper config on the installed system).
- Browser control: a Playwright/CDP MCP against Chromium's port 9222, so the agent can
  read and act in pages the user is watching.
- Voice input and a screen-aware mode (periodic screenshots into context).
- Custom compositor once the interaction model is settled.
