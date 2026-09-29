# Bombadil

A Linux distro whose main interface is an AI agent. You boot it, pick Claude or Codex,
and from then on you talk to the machine: the browser slides in when you ask for it,
apps you describe appear as native windows seconds later, and "undo that" rolls the
system back. The agent runs as you, with full access; the undo is a snapshot, not a
permission prompt.

## How it fits together

```
 Hyprland (Wayland)                 ← the screen: panels slide in as special workspaces
 ├─ quickshell  shell/shell.qml     ← the bar: your prompt at the bottom, the reply above it
 ├─ agentd      src/bombadil/agentd ← the session: one Unix socket, one turn at a time
 │    └─ claude -p … | codex exec … ← the provider CLI in full-access mode (providers.py)
 │         └─ bombadil-os-mcp       ← the OS as tools: panels, apps, screenshots, undo
 └─ ~/Apps/<name>/main.qml          ← generated apps, run natively by bombadil-app
 snapper on btrfs                   ← a snapshot before every turn (snapshots.py)
```

| Piece | What it does |
|---|---|
| `bin/agentd` | Starts at login. Takes prompts on `$XDG_RUNTIME_DIR/bombadil/agentd.sock`, snapshots, runs one provider turn, streams events to every connected client, logs the turn. |
| `bin/bombadil-os-mcp` | MCP server both CLIs load. Tools: `show_panel`, `hide_panel`, the app tools (`app_guide`, `create_app`, `check_app`, `open_app`, `show_app`, `hide_app`, `close_app`, `app_status`, `list_apps`, `app_template`), `screenshot`, `snapshot`, `list_snapshots`, `rollback`, `notify`. |
| `bin/bombadil-app` | Runs a generated app (`~/Apps/<name>/main.qml` + optional `app.py`) in its own slide-in drawer with hot reload; `bombadil-app check` loads one offscreen and returns errors and a screenshot. |
| `bin/bombadil` | Terminal client: `bombadil ask "…"`, `status`, `undo`, `provider claude\|codex`. |
| `shell/shell.qml` | The Quickshell bar. |
| `share/qml/Bombadil` | The app kit (`import Bombadil`): the OS look for every Qt Quick control, components (AppWindow, Panel, lists, tables, forms, editor, charts) and native bindings (System, Processes, Store, Vault, Command, ...). |
| `share/skills/bombadil-apps` | The skill both CLIs load to build apps with the kit in one shot, with two example apps. |
| `iso/` | archiso profile: Arch, Hyprland, greetd autologin, passwordless sudo, first-run setup, `bombadil-install` to a btrfs disk with snapper. |

Switching provider changes one line in `~/.config/bombadil/config.toml`; the OS tools
are the same MCP server either way.

## Try it

Run the Python parts anywhere:

```sh
python3 -m pip install -e '.[dev]'
pytest
BOMBADIL_PROVIDER=fake bin/agentd &        # echo provider, no snapshots needed
bin/bombadil ask "hello"
```

On an existing Hyprland desktop with `quickshell` and `pyside6` installed:

```sh
scripts/dev-session.sh                     # bar + agentd against this checkout
BOMBADIL_PROVIDER=claude scripts/dev-session.sh
```

Build and boot the ISO (needs an Arch host or container with `archiso`, and `qemu`):

```sh
scripts/build-iso.sh
scripts/run-vm.sh            # live
scripts/run-vm.sh --disk     # then `sudo bombadil-install /dev/vda` inside
```

On first boot a setup window asks which provider to use and runs its login.

## Status

First milestone: the pieces above, tested where they can be without a display.
Not yet exercised on real Hyprland: the bar, the QML runtime and the ISO build. See
`docs/ARCHITECTURE.md` for the decisions and what comes next.
