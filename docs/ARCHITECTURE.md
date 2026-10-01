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

## Signing in

agentd never handles a password or a token: it runs the provider CLI's own login
(`claude auth login`, `codex login`) in a terminal it holds, so the CLI stores its
credentials exactly as it would in a terminal of yours (`signin.py`).

1. Setup states, shown in the pill with chips: `choose` (first boot: Claude or Codex),
   `checking`, `signed_out`, `offline`, `signing_in`, `ready`. Prompts wait until `ready`
   and then run; a `!command` runs anyway.
2. The CLI runs with `BROWSER=bombadil-browser`, which hands its page to agentd over the
   socket, tagged with the sign-in's id. The page opens in the browser panel's Chromium
   (own profile under `~/.local/share/bombadil/browser`, no first-run pages, DevTools on
   127.0.0.1:9222) and comes back to the CLI's localhost callback.
3. If `$BROWSER` is not used within 2 s, the printed URL opens instead. A Claude page that
   ends on the code page gets `code#state` typed into the CLI, read from the tab's address.
4. DevTools and Hyprland tell it when the panel slid out or the browser was closed; the
   pill offers the page again. Esc cancels (the pill says "Cancelling" at once, and a page
   still opening in a slow browser is dropped, never slid in afterwards); 10 minutes without
   an end times out (`BOMBADIL_SIGNIN_TIMEOUT`). Success is confirmed with `claude auth
   status` or `codex login status`. A browser that cannot open the page is an error the pill
   shows with "Open it again", not a quiet success.
5. No internet (no TCP connection to the provider's sign-in host): the pill says so, offers
   Wi-Fi, and the sign-in starts once the host answers.
6. A turn whose CLI says the login is gone (`authentication_failed`, a 401) signs in again
   and runs the prompt once more, with what you did meanwhile told to the model. If the
   rerun says it too, the login is not what is wrong (a 403, an API key in the environment):
   the CLI's own words show and there is no second sign-in. Only the provider whose turn
   failed is signed in again, even if you switched AI meanwhile. A `/login` typed in a
   terminal lands in the panel too, and agentd watches for it to finish.
7. "Sign in" typed while signed in starts a new login, except for a provider whose login
   signs the stored one out as it starts (`login_replaces`: Codex, even if the new one is
   then called off): there it answers "already signed in".

`fake_signin.py` plays a provider login on localhost for the tests and the VM smoke test
(`BOMBADIL_PROVIDER=fake BOMBADIL_FAKE_SIGNIN=auto|manual|never|fail`).

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

## Coding sessions

The vendors' own Claude Code and Codex run unchanged in a zellij session (no bars, no keys of its
own) inside a systemd user scope per session; a foot window is only a viewer, so closing it never
costs work and typing the session's name brings it back. Managed hooks report each tool's state
through `bombadil-signal` to agentd, which broadcasts one `dev` message that the bar turns into a
dot per session and the desk into its Needs you card. See [`dev-sessions.md`](dev-sessions.md).

## Next

- Boot the ISO in QEMU and fix what the real Hyprland session shows (bar layering,
  app window rules, greetd autologin, snapper config on the installed system).
- Browser control: a Playwright/CDP MCP against Chromium's port 9222, so the agent can
  read and act in pages the user is watching.
- Voice input and a screen-aware mode (periodic screenshots into context).
- Custom compositor once the interaction model is settled.
