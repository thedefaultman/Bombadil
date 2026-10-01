# Making it yours

> **Status:** Shipped
> **Code:** `share/qml/Bombadil/Theme.qml`, `share/qml/Bombadil/qmldir`, `docs/brand/`, `src/bombadil/providers.py`, `src/bombadil/config.py`, `src/bombadil/mcp_server.py`, `src/bombadil/cardtools.py`, `src/bombadil/cards.py`, `src/bombadil/desk.py`, `src/bombadil/apps.py`, `shell/DeskState.qml`, `shell/DeskRail.qml`, `shell/PillState.qml`, `shell/CardHost.qml`, `iso/profiledef.sh`, `iso/packages.x86_64`, `scripts/build-iso.sh`, `iso/airootfs/usr/local/bin/bombadil-install`
> **Design:** [principles](principles.md)
> **Verified:** 2026-10-01 against `main` at `a30ebc8`

You can fork Bombadil and change almost everything about how it looks and what it does: the mark, the colours, the wording, the wallpaper, the name, the default AI, the tools the agent has, the pictures and widgets the shell draws, the apps that ship and the packages on the image. What makes it Bombadil is a short list of rules ([principles](principles.md)) and the few files that enforce them. This page says which is which. The first table lists where the identity lives and what to keep in each place, the second section lists the rules a fork inherits, and the recipes say, for eleven common changes, which files to edit and which tests to run.

## Get it running

Both paths start from a clone of your fork. The Python parts need Python 3.11 or newer and no runtime packages.

| You want | Commands | Needs |
|---|---|---|
| The test suite | `python3 -m pip install -e '.[dev,apps]'`, then `python3 -m pip install cryptography pillow numpy cairosvg`, then `pytest` | `dev` brings `pytest`, `pytest-asyncio` and `ruff`; `apps` brings `PySide6`. `cryptography` and the picture packages are not declared in `pyproject.toml`. |
| A session with the echo provider, no desktop | `BOMBADIL_PROVIDER=fake bin/agentd &`, then `bin/bombadil ask "hello"` | Nothing else. The reply is `echo: hello`. |
| The bar on a desktop you already have | `scripts/dev-session.sh` (the echo provider), or `BOMBADIL_PROVIDER=claude scripts/dev-session.sh` | Hyprland and `quickshell`, plus `pyside6` to create apps. |
| The image | `scripts/build-iso.sh`, or `scripts/build-in-container.sh` with Docker or Podman, then `scripts/run-vm.sh` | An Arch host with `archiso`, or a container; QEMU and OVMF to boot it. |

On 2026-10-01, `pytest` at `a30ebc8` ended with `1946 passed, 2 skipped` in about eight minutes in an environment with `pytest-asyncio`, `PySide6` and `cryptography` installed and without `pillow`, `numpy` and `cairosvg` (the two skips are the wallpaper tests that draw the picture again). Without `PySide6` the QML tests skip. Without `pytest-asyncio` the async tests fail with "async def functions are not natively supported". The image build and the VM smoke were not run for this page.

The session above uses your real state, config and socket folders. [Developing and testing Bombadil](contributing/development.md#run-a-dev-session-against-a-checkout) shows how to point them at a scratch folder, and covers the other test layers, the `BOMBADIL_*` variables and the VM tools.

## What carries the identity

Paths are from the repository root. After a first full path, the tables and recipes shorten some names: `providers.py`, `agentd.py`, `launcher.py`, `sysmap.py`, `hypr.py`, `mcp_server.py`, `cardtools.py`, `cards.py`, `desk.py`, `apps.py` and `narrate.py` are in `src/bombadil/`; `bombadil-install`, `bombadil-setup` and `bombadil-smoke` are in `iso/airootfs/usr/local/bin/`; `hyprland.lua` is `iso/airootfs/etc/skel/.config/hypr/hyprland.lua`; `bombadil-live.service` is in `iso/airootfs/etc/systemd/system/`; the `.qml` files are in `shell/`, except `Theme.qml` and the kit components in `share/qml/Bombadil/`.

| What | Where it lives | How to change it | What to keep |
|---|---|---|---|
| The mark and brand files | `docs/brand/` (`bombadil-mark.svg`, `bombadil-tile.svg`, `bombadil-lockup.svg`, `bombadil-lockup-light.svg`, `bombadil-avatar.png`, `bombadil-social-preview.png`), the installed icons `iso/airootfs/usr/share/icons/hicolor/scalable/apps/bombadil.svg` and `bombadil-symbolic.svg`, `iso/airootfs/usr/share/pixmaps/bombadil.svg`, the pill's stone `shell/Stone.qml`, the banner at the top of `README.md` | Redraw the SVGs and `shell/Stone.qml` together: the geometry in the stone is the drawing in the files ([brand](brand/README.md)). Keep the two PNG sizes, 1280 by 640 and 500 by 500. Test: `pytest tests/test_brand.py tests/test_stone_qml.py`. | A mark that is the machine's state: the stone in the pill rolls, leans, knocks, hops or stops, and every state reads without colour. No face, no mascot, nothing from the text or art the name echoes ([Original](principles.md#original)). No SVG `<mask>` in the installed icons, which Qt ignores. The README opens with the lockup in a dark and a light copy. |
| The colour and type tokens | `share/qml/Bombadil/Theme.qml` is the source. Hand copies: `shell/DeskTheme.js`, `iso/airootfs/etc/skel/.config/hypr/hyprland.lua` (lines 16 to 35), `share/grub/bombadil/theme.txt`, `docs/brand/*.svg`, the installed icons, `src/bombadil/appkit/native/highlighter.py` (`PALETTE`), the console palette from `scripts/console-palette.py`, the wallpaper picture | Edit `Theme.qml`, then follow [Changing a token](design-system/README.md#changing-a-token) and recipe 8. | Tokens, not literals: the shell reads `Kit.Theme` and `tests/test_theme.py` fails a hex colour in `shell/*.qml`. White is the person, orange is the machine acting, blue is a coding session ([Colour says who](principles.md#colour-says-who)). `pillHeight` is twice `radiusPill`. `tests/test_theme.py:72` pins `accent` at `#d97757`: edit that line on purpose if you change the orange. A `fontFamily` other than `Inter` needs its package in `iso/packages.x86_64`. |
| The voice lines and persona files | None on `main`. `share/voice/` does not exist and no `persona.py` or `greet.py` is in `src/bombadil/`; that work is described in [voice](architecture/voice.md). What speaks: the system prompt (`src/bombadil/providers.py:33-55`), the narrated steps (`src/bombadil/narrate.py`), the launcher's answers (`src/bombadil/launcher.py`), the desk's sentences (`src/bombadil/desk.py`), the setup lines (`src/bombadil/agentd.py:910,971,1041`) and the pill's placeholder (`shell/shell.qml:433`) | Edit the strings and the tests that quote them (recipe 9). | Full sentences in sentence case that say what changed and how to take it back. No emoji, no exclamation marks, no "Oops". Words may change; a fact, a count or an error may not ([One design language](principles.md#one-design-language)). |
| The wallpaper | `share/wallpaper/bombadil.png`, drawn by `scripts/make-wallpaper.py` from the tokens and shown by `shell/Wallpaper.qml`; Hyprland's own ground, `background_color` at `iso/airootfs/etc/skel/.config/hypr/hyprland.lua:35`; the person's own picture is a path in `~/.config/bombadil/wallpaper`, seeded by `iso/airootfs/etc/skel/.config/bombadil/wallpaper` | Change the tokens and run `scripts/make-wallpaper.py` (needs `pillow`, `numpy` and `cairosvg`; options `--no-stone`, `--out`, `--width`, `--height`), or replace the PNG. Do not retouch the PNG by hand: `tests/test_wallpaper.py` reads it pixel by pixel against the tokens. | A ground made of the design's own surfaces, 16:9 and at least 2560 pixels wide, 8-bit RGB with no alpha, under 6 MB (`tests/test_wallpaper.py`). Nothing lighter than the cards on it, so the rest screen stays wallpaper and one pill ([Quiet at rest](principles.md#quiet-at-rest)). |
| The GRUB theme | `share/grub/bombadil/` (`theme.txt`, `background.png`, `README.md`) | Replace `background.png` (1920 by 1080) and set `desktop-color` to the `bg` token. Nothing installs the theme: `iso/airootfs/usr/local/bin/bombadil-install` never mentions it, so wiring it in is your work. The folder's README has the commands and [reference](reference.md) says the same. | `desktop-color` equal to `bg` and the font `Inter Regular 16` (checked by `tests/test_brand.py`). The installer hides the menu unless Esc is held (`bombadil-install:63-67`). |
| The name "Bombadil" where code hard-codes it | The search below finds 1407 lines in 243 files at `a30ebc8`. The places, by kind, are in [Where the name is hard-coded](#where-the-name-is-hard-coded). | Text a person reads can change freely. An identifier changes only everywhere it appears: the table under the search names the files that go together. | The shell's QML shows the name nowhere: it appears there only in comments, `import Bombadil`, command, window and layer names, and paths. Keep it that way, so a change of visible name needs no edit in the shell. |
| The system prompt the agent gets | `src/bombadil/providers.py:33-55` (`system_prompt()`). Claude gets it as `--append-system-prompt` (`:232`) and Codex as `developer_instructions` (`:426`), on every turn, resumed ones included. | Edit the text. `tests/test_providers.py` and `tests/test_iso_profile.py` quote parts of it. | Where the kit lives and `import Bombadil`, so the agent never searches the disk for it. The sentences that ask for reasons, plans and pictures. The full-access sentence must stay true to the flags at `providers.py:227` and `:435-437`: if you narrow the flags, change the sentence ([security model](security-model.md#if-you-fork-it)). |
| The skills | `share/skills/bombadil-apps/` (`SKILL.md`, `references/components.md`, `references/native.md`, `references/runtime.md`, `examples/memory`, `examples/password-manager`). The agent reaches it through `~/.claude/skills/bombadil-apps` and `~/.agents/skills/bombadil-apps`, links in `iso/airootfs/etc/skel` to `/usr/share/bombadil/share/skills/bombadil-apps`, and through the `app_guide` tool (`src/bombadil/appkit/tools.py:31`). | Edit the files or add an example folder; the smoke test copies the examples (`bombadil-smoke:98,109`). To rename the skill, change `providers.kit_paths()` (`:24-30`), `src/bombadil/appkit/tools.py:31`, the two skel links and `bombadil-smoke:101`. | A skill that lists every kit component and binding the agent can use. When the kit changes, `references/components.md` changes with it. |
| The default provider and model | `iso/airootfs/etc/bombadil/config.toml` (`provider = "claude"`, `snapshots = true`, no `model`); the defaults in `src/bombadil/config.py:14-19`; the list of allowed providers, `config.PROVIDERS` (`:10`); the one model name fixed in code, `"haiku"`, at `providers.py:326` (the Brain's one-line descriptions) | Recipes 1 and 2. First boot asks "Which AI should run this computer?" until `~/.config/bombadil/config.toml` exists or `BOMBADIL_PROVIDER` is set (`src/bombadil/agentd.py:1773`), so the `provider` in `/etc/bombadil/config.toml` is only the fallback before the person chooses. | Both providers run in their full-access mode with the same `bombadil-os` server, so switching one for the other changes no power ([Full access with undo](principles.md#full-access-with-undo)). |

### Where the name is hard-coded

```sh
grep -rIi bombadil src bin shell share iso scripts tests pyproject.toml \
  --exclude-dir=__pycache__ --exclude-dir=bombadil.egg-info
```

The count includes comments and docstrings. Each kind below is one thing to change together.

| Kind | Where | Change together with |
|---|---|---|
| Text a person or the agent reads | `src/bombadil/providers.py:37-41` (system prompt), `src/bombadil/launcher.py:494` (the undo line on a live system), `src/bombadil/mcp_server.py:71` and `src/bombadil/appkit/tools.py:160,168` (tool descriptions), `src/bombadil/appkit/engine.py:48` (`setOrganizationName`), `iso/airootfs/usr/share/applications/bombadil-browser.desktop:4`, the `<title>` of the three icon SVGs, `share/skills/bombadil-apps/`, `README.md` | Nothing. Change the sentence and the tests that quote it. |
| The image | `iso/profiledef.sh:3-6`, `iso/efiboot/loader/entries/01-bombadil.conf:1` (and `02`, `03`), `iso/efiboot/loader/loader.conf:2`, `iso/airootfs/etc/hostname`, `bombadil-install:16,52,68,87`, `scripts/run-vm.sh:22` | Recipe 10. |
| Commands | `bin/bombadil`, `bin/bombadil-app`, `bin/bombadil-browser`, `bin/bombadil-brain`, `bin/bombadil-brain-watch`, `bin/bombadil-os-mcp`, `bin/bombadil-shell`; `iso/airootfs/usr/local/bin/bombadil-install`, `bombadil-rollback`, `bombadil-setup`, `bombadil-smoke` | Every caller: the links at `scripts/build-iso.sh:16`, the modes at `iso/profiledef.sh:18-23`, `hyprland.lua:9,57-65`, `src/bombadil/launcher.py:749-751`, `src/bombadil/agentd.py:1636-1638`, `src/bombadil/snapshots.py:63`, `bombadil-smoke`. |
| The Python package | `src/bombadil/`, `pyproject.toml:2`, the imports in `bin/*`, and 65 of the 73 files under `tests/` | All of them. |
| The MCP server name `bombadil-os` | `src/bombadil/mcp_server.py:185`, `src/bombadil/providers.py:70,264-265,424-425`, `src/bombadil/narrate.py:947`. The CLIs call its tools `mcp__bombadil-os__<tool>`. | The provider code and `narrate.py`; a CLI's saved sessions that name the old tools. |
| The QML module `Bombadil` and the style `Bombadil.Style` | `share/qml/Bombadil/qmldir:1`, `share/qml/Bombadil/Style/qmldir:1`, `src/bombadil/appkit/engine.py:15`, `src/bombadil/appkit/native/__init__.py:10`, every `import Bombadil` in `shell/*.qml`, in `share/apps` and in the apps under `~/Apps`, the system prompt and the skill | Every app the agent already wrote imports it, so a rename breaks them. |
| Window classes and layer names | `bombadil-app-<name>`, `bombadil-browser`, `bombadil-terminal`, `bombadil-details` (`hyprland.lua:68-81`, `src/bombadil/hypr.py`, `src/bombadil/browser.py:27`, `src/bombadil/appkit/placement.py`, `src/bombadil/brain/this.py:33-35`, `bombadil-smoke`); `bombadil-bar`, `bombadil-desk-*`, `bombadil-wallpaper` (`shell/shell.qml:191`, `shell/DeskRails.qml:29`, `shell/Wallpaper.qml:80`) | The Hyprland rules and the smoke test. |
| Unit names | `bombadil-turn-<pid>-<turn>-<time>.scope` (`src/bombadil/agentd.py:1322`), `bombadil-timer-*` and `bombadil-job-*` (`src/bombadil/jobs.py:162`), `bombadil-dev-<project>.slice`; the services `bombadil-brain`, `bombadil-brain-watch`, `bombadil-live` and `bombadil-smoke` | `src/bombadil/brain/actors.py:21-23` and `src/bombadil/procs.py` read these names by pattern. |
| Folders, sockets and attributes | `/etc/bombadil/config.toml` (`src/bombadil/config.py:9`), `~/.config/bombadil`, `~/.local/state/bombadil`, `$XDG_RUNTIME_DIR/bombadil/agentd.sock` (`src/bombadil/paths.py`), `/usr/share/bombadil` (`paths.py:45`, `scripts/build-iso.sh:12`, `iso/profiledef.sh:23`, `providers.py:29`, `src/bombadil/cards.py:38`, the skel skill links, the `Documentation=` lines of the brain units), `/run/bombadil-brain` and `/var/lib/bombadil-brain` (`src/bombadil/brain/watch.py:71-73`), `~/.bombadil/memory.md` (`src/bombadil/brain/witnesses.py:745`), `user.bombadil.made_by` (`src/bombadil/brain/ingest.py:41`) | Everything that reads them, and the person's existing state: a renamed folder starts empty. |
| Environment variables and the kernel argument | 33 `BOMBADIL_*` names ([reference](reference.md)), and `bombadil.smoke` (`bombadil-smoke:14`, boot entries 02 and 03) | The scripts and tests that set them. |
| The account name | `user`, which is not the product name but is fixed: `iso/airootfs/etc/systemd/system/bombadil-live.service:7`, `iso/airootfs/etc/sudoers.d/bombadil:2`, `iso/airootfs/etc/greetd/config.toml`, `bombadil-install:53-54,73-76`, `bombadil-smoke` | All of them. |

The safe fork keeps every identifier and changes the text: recipe 10 lists the visible strings of the image.

## What to keep (the rules a fork inherits)

Each rule is one heading in [principles](principles.md), with the brief that decided it. A change that breaks one turns Bombadil into a chatbot with a desktop around it, or a desktop with a chat sidebar.

| Rule | In one line |
|---|---|
| [The person is a passenger](principles.md#the-person-is-a-passenger) | The AI is the interface, and the person can see the road. |
| [Full access with undo](principles.md#full-access-with-undo) | Full access, with a restore point before every turn instead of a permission prompt. |
| [Real native apps](principles.md#real-native-apps) | What the agent makes is a native app, never a web page on a port. |
| [One machine one conversation](principles.md#one-machine-one-conversation) | One conversation and one memory the person can read, not a list of chats. |
| [Recovery without the broken part](principles.md#recovery-without-the-broken-part) | Stop, undo and rescue never go through the part that broke. |
| [Records before models](principles.md#records-before-models) | Records answer before models do, and the machine explains itself from what it kept. |
| [The person's press reaches people](principles.md#the-persons-press-reaches-people) | Only the person's own press reaches other people or leaves the machine. |
| [Nothing runs unseen](principles.md#nothing-runs-unseen) | Nothing runs on its own that the person cannot see and stop. |
| [Plain files stay the truth](principles.md#plain-files-stay-the-truth) | Folders are still folders, and the person's home is never rewritten. |
| [Stupidly simple](principles.md#stupidly-simple) | One pill and three things to learn. |
| [Something true in 200 ms](principles.md#something-true-in-200-ms) | Something true is on screen within 200 ms of Enter, and open, stop and undo never wait for the model. |
| [The answer is the thing](principles.md#the-answer-is-the-thing) | A window, a card or a panel appears; written replies stay short. |
| ["This" means what you see](principles.md#this-means-what-you-see) | "This" means what is in front of the person. |
| [Quiet at rest](principles.md#quiet-at-rest) | At rest the screen is wallpaper and one pill, and every movement means one thing. |
| [Colour says who](principles.md#colour-says-who) | White is the person, orange is the machine acting, blue is a coding session. |
| [One design language](principles.md#one-design-language) | The shell and every app read the same tokens, kit and plain voice. |
| [Original](principles.md#original) | Nothing borrowed, no face and no mascot. |
| [Degrade and recover](principles.md#degrade-and-recover) | Every piece degrades, and nothing waits on a model it does not need. |

The page also has a [twelve-question checklist](principles.md#checking-a-change-against-the-principles) to run before a change.

### The six that break the product soonest

1. **[Full access with undo](principles.md#full-access-with-undo).** The agent runs with the CLIs' no-prompt flags (`src/bombadil/providers.py:227`, `:435-437`) and its only safety is the restore point taken before each turn, which exists only where `snapper` has a `root` configuration (`src/bombadil/snapshots.py:32-34`), so a fork that leaves btrfs or turns snapshots off keeps the power and loses the way back.
2. **[Recovery without the broken part](principles.md#recovery-without-the-broken-part).** `iso/airootfs/etc/skel/.config/hypr/hyprland.lua:63` binds `bombadil stop` and `bin/bombadil:243` runs `undo`, neither through the shell or a model, so a fork that moves them into the pill leaves the person stuck on the day the agent breaks the shell.
3. **[The person's press reaches people](principles.md#the-persons-press-reaches-people).** None of the 21 tools of `bombadil-os-mcp` sends, posts, pays or pushes (`src/bombadil/mcp_server.py:56-175`), because the agent reads other people's words and undo cannot take back a message, so a fork that adds such a tool moves the press from the person to a model.
4. **[Nothing runs unseen](principles.md#nothing-runs-unseen).** A background job is a transient systemd user unit made by the `job` tool, which the desk's Watching card lists and the person can stop (`src/bombadil/mcp_server.py:150-169`, `src/bombadil/jobs.py:162`), so a tool that starts a daemon of its own makes work nobody can see or stop.
5. **[Something true in 200 ms](principles.md#something-true-in-200-ms).** `src/bombadil/launcher.py` answers open, close, stop, undo, the desk and the picture words inside `agentd` with no model (`launcher.py:1-8`), so a fork that sends them to the provider makes every first answer as slow as a model.
6. **[One design language](principles.md#one-design-language).** The shell reads `Theme.qml`, and `tests/test_theme.py` fails a hex colour or a `Text` with no font family in `shell/*.qml`, so a fork that styles one widget by hand fails the suite and stops matching the apps the agent writes.

## Common changes

Each recipe names the file where the thing is registered, says so when there is no registration, and ends with the test. Line numbers are at `a30ebc8`. The piece pages under `architecture/` hold the detail behind each registration point.

### 1. Add a provider

A provider is one CLI that the daemon starts for each turn. Details: [agentd](architecture/agentd.md#add-a-provider).

1. In `src/bombadil/providers.py`, subclass `Provider` (`:74`). Set `name`, `title`, `binary` and `signin_host`, and write `command(turn, workdir)` and `parse(line)`. `Claude` (`:217`) and `Codex` (`:409`) are the two worked examples.

   ```python
   class Acme(Provider):
       name = "acme"
       title = "Acme"
       binary = "acme"
       signin_host = ("auth.example.com", 443)

       def command(self, turn: Turn, workdir: Path) -> list[str]: ...
       def parse(self, line): ...
   ```

2. `command` must give the CLI the system prompt (`system_prompt()`, as `:232` and `:426` do), the `bombadil-os` MCP server (`mcp_config()` at `:68` writes Claude's file; Codex takes `-c mcp_servers.bombadil-os...` overrides at `:424-425`) and its own full-access flag (`:227`, `:435-437`), so every provider has the same powers ([security model](security-model.md#if-you-fork-it)).
3. `parse` yields the shared events: `session`, `text`, `thinking`, `tool`, `tool_result`, `result`, `error`, `signed_out`. Give tools Claude's names (`Bash`, `TodoWrite`, `WebSearch`, `mcp__<server>__<tool>`), as `Codex.parse` does (`:440`), because `narrate.tool_step` reads those names to write the line above the pill.
4. Write `describe_command` (`:127` raises `NotImplementedError` in the base class and `src/bombadil/brain/describe.py:407` calls it). The sign-in hooks (`login_command`, `signin_command`, `signin_url_kind`, `code_from_url`, `signed_in`, `credentials`, `SIGNED_OUT`, `ends_when_signed_out`, `login_replaces`, `:124-164`) have defaults that do nothing special; override the ones your CLI's login needs.
5. Register it twice. Add it to `PROVIDERS` (`providers.py:581`) and to `config.PROVIDERS` (`src/bombadil/config.py:10`). `config.load()` raises `ValueError` for a name not in `config.PROVIDERS` (`config.py:43-44`); a name in `config.PROVIDERS` and not in `providers.PROVIDERS` raises `KeyError` when the pill offers the choice (`src/bombadil/agentd.py:905-912`); a name only in `providers.PROVIDERS` is never offered, which is how the `fake` provider stays out of the person's list.
6. Add the words people say to `PROVIDER_WORDS` (`src/bombadil/launcher.py:61`), so "use acme" switches to it and typing its name answers the first-boot question (`agentd.py:306-309`). `bombadil provider` and `bombadil signin` read `config.PROVIDERS` (`bin/bombadil:250,257`).
7. Add a row to `PROVIDER_HOSTS` (`src/bombadil/sysmap.py:168`). Without one, the network picture falls back to Claude's host (`sysmap.py:255`): it draws the wrong provider and raises no error.
8. Put the CLI on the image. `iso/airootfs/usr/local/bin/bombadil-setup` names the two CLIs (`:10`, `:12-13`, `:27-28`), `scripts/build-iso.sh:19-23` installs them with `npm`, `iso/profiledef.sh:24-25` sets the modes of their folders, `bombadil-install:73` copies the live user's login folders (`.claude`, `.claude.json`, `.codex`, `.config/bombadil`) to the installed disk, and `bombadil-smoke:83` checks that `codex --version` runs.
9. Test: `pytest tests/test_providers.py tests/test_config.py tests/test_launcher.py tests/test_sysmap.py tests/test_agentd_signin.py`. `tests/test_providers.py` is the pattern: the command and the events from recorded lines in `tests/fixtures/`, and `test_registry` (`:70`). `tests/test_config.py:17-21` uses `gemini` as the name `config.load()` must refuse, so change that example if you add that name. For a whole session, run `BOMBADIL_PROVIDER=acme scripts/dev-session.sh`.

### 2. Change the default model

No model is set anywhere on `main`: each CLI picks its own. `Claude.command` adds `--model` only when one is configured (`providers.py:233-234`) and so does `Codex.command` (`:430-431`).

1. Add `model = "<name>"` to `iso/airootfs/etc/bombadil/config.toml` for every person, or to `~/.config/bombadil/config.toml` for one. `config.load()` reads the system file and then lets the user file win (`config.py:30-45`), and `agentd.main` passes the result to the provider (`agentd.py:1766-1767`).
2. A model name belongs to one CLI, and the file has no per-provider key. `config.save_user` writes the provider and only a model it is given, so a person who picks the other provider in the pill still has the system `model`: on 2026-10-01, `config.load()` after `save_user("codex")` returned the system `model`, and `Codex.command` carried it as `--model`. In the running daemon `choose` drops the model when the provider changes (`agentd.py:1097-1100`), so the two disagree until the daemon restarts. Set `model` in the system file only if every provider you ship accepts that name.
3. The Brain's descriptions use `"haiku"` whatever `model` says (`providers.py:326`); `src/bombadil/brain/describe.py:407` passes no model. Change the literal, or have the caller pass `model=`.
4. Test: `pytest tests/test_config.py tests/test_providers.py`. `tests/test_config.py:12-14` shows a model saved and read back, and `tests/test_providers.py:9-12` shows `--model` in the command line.

### 3. Add an os-mcp tool

`bombadil-os-mcp` is a hand-written JSON-RPC server over stdio with 21 tools at `a30ebc8`. Details: [os-mcp](architecture/os-mcp.md#add-a-tool-that-acts-inside-the-server).

1. In `src/bombadil/mcp_server.py`, inside `OsTools._register` (`:56-175`), add a function under `@t(name, description, properties, required)`, where `t` is `OsTools._tool` (`:46-54`). Put it before `from . import cardtools` (`:171`): the picture tools and then the app tools register after it, and the app tools replace `create_app`, `open_app`, `list_apps` and `app_template` (`src/bombadil/appkit/tools.py:147-150`).

   ```python
   @t("hello", "Say hello to a name.", {"name": {"type": "string"}}, ["name"])
   def hello(a):
       return f"hello {a['name']}"
   ```

   The function gets the arguments as a dict. It returns a string, a dict or list (sent as JSON text) or `{"image", "mimeType"}`, and raises `ToolError` (`:34`) with a plain sentence for a refusal: `handle` turns it into `isError` (`:192-205`). A tool in another module follows `cardtools.register(tools)` (`src/bombadil/cardtools.py:109`).
2. Nothing else exposes it. `tools/list` is built from `self.tools` (`:190-191`) and neither provider is given an allow-list of tool names. On 2026-10-01, a copy of the tree with the function above listed 22 tools and answered the call.
3. If the tool needs `agentd`, send one line over its socket with `_ask_agentd` (`:297`), as `_desk` (`:263`) and `_job` (`:279`) do, and add the message type to `AgentD.handle` (`agentd.py:298`). The turn number reaches the server in `BOMBADIL_TURN`. Codex starts MCP servers with a short environment, so a variable your tool reads must be added to `MCP_ENV` (`providers.py:385-388`).
4. Add a branch for the narrated line to `narrate._os_tool` (`src/bombadil/narrate.py:867`). Without one the line above the pill says "Working on it" (`narrate.py:948`), which is what the copy above showed.
5. Write the description for the model, which reads it. If the agent needs more, extend the skill or the system prompt. Record the tool in [os-mcp](architecture/os-mcp.md) and [reference](reference.md).
6. Judge it against [The person's press reaches people](principles.md#the-persons-press-reaches-people): the tool may prepare something, never release it.
7. Test: `pytest tests/test_mcp_server.py tests/test_narrate.py`. `tests/test_mcp_server.py` builds `OsTools` with a fake Hyprland and fake snapshots (`make()`) and calls tools through `rpc`; `tests/test_narrate.py:71-72` is a table of tool calls and their lines. The boot smoke checks that `show_panel` and `system_map` are listed (`bombadil-smoke:84,132`).

### 4. Add a card kind

One kind exists, `diagram`, and there is no registry of kinds: `diagram` is assumed at each point below. Details: [cards and pictures](architecture/cards-and-pictures.md).

1. `src/bombadil/cardtools.py`: the `kind` enum is `["diagram"]` (`:113`) and `show_card` raises for any other kind (`:123-125`). Add your kind to the enum and route it in `show_card` (`:122`).
2. `src/bombadil/cards.py`: write a validator beside `validate_diagram` (`:74`) and branch to it in `accept` (`:221-238`). Today `accept` ignores `kind`, runs `validate_diagram` on any object and stamps `"type": "diagram"` on the result (`cards.py:213`): on 2026-10-01 `cards.accept` with `kind: "list"` returned a diagram. Give your kind a text twin like `text_of` (`:301`).
3. `src/bombadil/agentd.py`: `_card_message` (`:716`) calls `cards.accept` and sends the card. Streaming a card as the agent writes it (`CardStream`, `cards.py:406`; `agentd.py:770-781`) follows `show_card` calls and builds diagrams only, so another kind is drawn when it is whole.
4. `shell/PillState.qml:102-112`: `_takeCard` drops every card whose `type` is not `"diagram"` (`:108`).
5. `shell/CardHost.qml`: it draws a `Kit.Diagram` for every card (`:116`). Give your kind a face beside it, as the comment at the top of the file expects, and build the face as a kit component (recipe 6) so apps can draw it too.
6. Test: `pytest tests/test_cards.py tests/test_cardtools.py tests/test_pill_qml.py tests/test_diagram_qml.py`. `tests/test_cardtools.py:83` asserts that `kind: "list"` is refused, so that test changes with step 1. The QML tests need `PySide6`.

### 5. Add a desk widget

A widget is a card in one of the two rails beside the pill. Details: [desk](architecture/desk.md).

1. `src/bombadil/desk.py`: add `Widget(id, title, words, rail)` to `WIDGETS` (`:41-48`). The order of the entries is each rail's default order, nearest the pill first. `ALWAYS` (`:50`) lists widgets that cannot be put away and `OPT_IN` (`:52`) the ones that start hidden. The launcher builds its words from `WIDGETS` (`launcher.py:37-38`) and the `desk` tool's `widget` enum is `list(WIDGETS)` (`mcp_server.py:143`), so "show acme" and the tool follow without an edit.
2. Two hand-written lists do not follow. Add the widget's name to `_DESK_ASK` (`desk.py:60-65`), which decides whether the person's own words asked for the desk, and to the sentence in the `desk` tool's description (`mcp_server.py:135-141`).
3. The shell has no list to import. `shell/DeskState.qml` holds the ids as a literal (`:28`), the default rails and order (`:46-47`), a model for each widget, a `...Present` flag and the `present` map (`:116-127`), `_model(id)` (`:163-166`), the height of the full face in `heightOf` (`:169-180`) and the strip text in `_stripOf` (`:345-361`). `shell/DeskRail.qml:84-85` picks the face by id: `now` gets `NowCard`, `watching` and `needs` get `RowsCard`, and every other id gets none, so `machine`, `away` and `alive` have no full face on `main`. Build your face on `shell/DeskCard.qml` and add it there.
4. The data: `DeskState.handle` (`:392-396`) takes the `desk`, `jobs` and `dev` messages from `agentd` and the turn events. There is no registration for a feed; pick the message that carries your data, or add a message type in `agentd.py` and a case in `handle`.
5. Colours come from `shell/DeskTheme.js`, never literals.
6. Test: `pytest tests/test_desk.py tests/test_desk_qml.py tests/test_desk_cards_qml.py tests/test_mcp_server.py tests/test_launcher.py`. `tests/test_desk.py:258` checks the rails against `desk.WIDGETS` and `tests/test_mcp_server.py:173` pins the enum. No test compares `DeskState.widgetIds` with `desk.WIDGETS` (a search of `tests/` finds none), so compare the two by hand.

### 6. Add a kit component

The kit is the QML module `Bombadil`: what the agent builds apps from. Details: [app kit](architecture/app-kit.md).

1. Write `share/qml/Bombadil/Tag.qml`. Use `Theme`, `Fmt` and sibling components by name and no colour literals.

   ```qml
   import QtQuick

   Rectangle {
       property string text
       implicitWidth: label.implicitWidth + 16
       implicitHeight: 24
       radius: height / 2
       color: Theme.raised
       Text { id: label; anchors.centerIn: parent; text: parent.text; color: Theme.fg; font.family: Theme.fontFamily }
   }
   ```

2. Register it: add `Tag 1.0 Tag.qml` to `share/qml/Bombadil/qmldir` (35 entries at `a30ebc8`, lines 5 to 39). A singleton starts with `pragma Singleton` and takes `singleton <Name> 1.0 <Name>.qml`. On 2026-10-01, `bombadil-app check` on an app that used `Tag` reported `Tag is not a type` without the line and `"ok": true` with it.
3. An icon goes in `share/qml/Bombadil/icons/<name>.svg`.
4. Tell the agent: `share/skills/bombadil-apps/references/components.md` and `SKILL.md`, and the table in [app kit](architecture/app-kit.md). No test compares `qmldir` with those, so nothing fails if you skip this.
5. Show it in a gallery, `tests/qml/components_gallery.qml` (`tests/qml/charts_gallery.qml` for a chart), in each state it has.
6. Test: `bin/bombadil-app check tests/qml/components_gallery.qml --screenshot out.png` must print `"ok": true` with no errors or warnings (it did on the unchanged gallery on 2026-10-01). Logic such as selection or keys goes in `tests/test_appkit_kit.py`. Both need `PySide6`.

### 7. Add a built-in app

A built-in app ships with the image and runs like one the agent wrote. Details: [app kit](architecture/app-kit.md).

1. Create `share/apps/<name>/` with `main.qml` (root `AppWindow`), `app.toml` (`title`, `description` and `icon`, an icon name from `share/qml/Bombadil/icons/`) and, if needed, `app.py` with a `Backend` class. The folder name must match `^[a-z0-9][a-z0-9-]{0,40}$` (`src/bombadil/apps.py:25`). `share/apps/brain/` is the one example.
2. There is no registration. `apps.builtin_dir()` is `share/apps` (`apps.py:46-49`), `apps.app_dir` falls back to it when `~/Apps/<name>/main.qml` does not exist (`:59-65`), and `scripts/build-iso.sh:14` copies all of `share/` to `/usr/share/bombadil`. So `bombadil-app run <name>` and the tools that call `apps.load` (`open_app`, `show_app`) find it.
3. What it does not get: `apps.list_apps()` lists `~/Apps` only (`apps.py:156-160`, `tests/test_apps.py:79`), so the pill's app words and Tab completion (`launcher.known_apps`, `launcher.py:158`) do not know it, and no launcher entry is written (that is in `apps.create`, `:123-136`). The Brain opens by name only because `launcher.py` has words for it (`:54`) and `BRAIN_APP` (`:103`); a built-in app of yours needs the same by hand. Its saved state is in `~/.local/state/bombadil/apps/<name>/data/`, because its folder is read-only (`src/bombadil/appkit/context.py:18-22`).
4. Test: `bin/bombadil-app check share/apps/<name> --screenshot out.png`; copy the pattern of `tests/test_apps.py:71`; give a backend its own test, as `tests/test_brain_app.py` does for the Brain. `tests/test_apps.py` runs without `PySide6`; the check does not.

### 8. Change the colour

1. Edit `share/qml/Bombadil/Theme.qml`. It is the source of every colour.
2. Copy by hand what [Changing a token](design-system/README.md#changing-a-token) lists: `shell/DeskTheme.js`, `hyprland.lua` (`:20-21`, `:35`), `share/grub/bombadil/theme.txt`, `docs/brand/*.svg`, the installed icons, `src/bombadil/appkit/native/highlighter.py`. To find what still holds the old value, search for it:

   ```sh
   grep -rIil -e 101214 -e d97757 src shell share iso scripts docs/brand tests
   ```

3. Draw again what is made from the tokens. `scripts/make-wallpaper.py` rewrites `share/wallpaper/bombadil.png`. `scripts/console-palette.py` prints the kernel parameters for the boot console: paste its line into `iso/efiboot/loader/entries/01-bombadil.conf` and `iso/airootfs/etc/default/grub.d/zz-bombadil-console.cfg`.
4. Test: `pytest tests/test_theme.py tests/test_brand.py tests/test_boot_console.py tests/test_wallpaper.py tests/test_stone_qml.py tests/test_diagram_qml.py tests/test_pill_qml.py tests/test_desk_qml.py tests/test_desk_cards_qml.py`. On a copy of the tree on 2026-10-01 these nine files gave 321 passed and 2 skipped. With only `bg` changed to `#0f1720`, 7 failed: the GRUB `desktop-color` (`test_brand.py`), the console palette, the live boot entry, the GRUB drop-in and Hyprland's ground (four in `test_boot_console.py`), and two pixel tests in `test_wallpaper.py`. With only `accent` changed to `#3b82f6`, 5 failed: the pin and `DeskTheme.machine` (`test_theme.py`), one in `test_diagram_qml.py` and two in `test_desk_qml.py`. The pill's glass colours, `hyprland.lua` and the SVGs under `docs/brand/` are checked by nothing, so the search above is the check for them.

### 9. Change the voice

On `main` there is no voice file, persona or greeting to edit: the design is in [voice](architecture/voice.md) and none of it is merged. The voice is the strings.

1. Edit what speaks, from the row in the identity table: `src/bombadil/providers.py:33-55` for the agent's own words, `src/bombadil/narrate.py` for the lines above the pill, `src/bombadil/launcher.py` and `src/bombadil/desk.py` for the answers to typed words, `src/bombadil/agentd.py` for the setup lines, `shell/shell.qml:433` for the placeholder.
2. The one tone setting is `explain` in `config.toml`: `brief`, `normal` or `teach` (`src/bombadil/config.py:11`). It sets how much the machine shows without being asked; at `brief` there are no receipts (`agentd.py:177`).
3. Keep the rule: a voice changes words, never a fact, a count or an error ([One design language](principles.md#one-design-language), [voice brief](design/voice-brief.md#the-rules)).
4. Test: many tests quote exact sentences, so a reworded line fails its test until the expectation changes with it. `pytest tests/test_narrate.py tests/test_launcher.py tests/test_providers.py tests/test_agentd_signin.py tests/test_desk.py` (`tests/test_agentd_signin.py:68,87` quote the setup lines, `tests/test_narrate.py:71-72` the narrated ones).

### 10. Build the ISO with your own name

Change the visible strings and leave the identifiers (see [Where the name is hard-coded](#where-the-name-is-hard-coded)). Details: [ISO and install](architecture/iso-and-install.md).

1. `iso/profiledef.sh:3-6`: `iso_name`, `iso_label`, `iso_publisher` and `iso_application`. The scripts take the newest `out/*.iso` (`scripts/run-vm.sh`, `scripts/test-vm.sh:10`), so the file name does not matter to them.
2. The boot menu: `title` in `iso/efiboot/loader/entries/01-bombadil.conf`, `02-bombadil-serial.conf` and `03-bombadil-install-test.conf`. `iso/efiboot/loader/loader.conf:2` names `01-bombadil.conf` as the default, and `tests/test_boot_console.py` opens the entries by file name (`:64`, `:72`), so rename the files only together with both.
3. The installed system: `iso/airootfs/etc/hostname`, and in `bombadil-install` the btrfs label (`:16`), the hostname written into the target (`:52`), the boot loader id (`:68`) and the closing line (`:87`). `scripts/run-vm.sh:22` names the QEMU window.
4. Build and test. `pytest tests/test_iso_profile.py tests/test_boot_console.py tests/test_brand.py` checks the profile without building. Then `scripts/build-iso.sh` on an Arch host with `archiso` (or `scripts/build-in-container.sh`; `BOMBADIL_NO_CLIS=1` skips baking the CLIs in), `scripts/test-vm.sh` for the live checks and `MODE=install scripts/test-vm.sh` for install, boot and undo over a reboot. The build and the smoke were not run for this page.

### 11. Change the package list

1. Edit `iso/packages.x86_64`: one package a line, with `#` lines naming the groups. `scripts/build-iso.sh:14` also copies the file to `/usr/share/bombadil/`, and nothing reads it at run time (a search of the tree finds only that copy and `tests/test_iso_profile.py`). It sets what ships, not what the person can get: the system prompt tells the agent to install software with `pacman`.
2. Some code names packages. `foot` and `nautilus` are the terminal and files panels (`src/bombadil/hypr.py:21-25`, and the `org.gnome.Nautilus` rule at `hyprland.lua:79`), `grim` takes the screenshots (`hypr.py:221-223`), `libnotify` gives `notify-send` to the `notify` tool (`mcp_server.py:131`), `wireplumber` gives `wpctl` to the volume words (`launcher.py:662`), and `hyprlock` is not in the list, so "lock" answers that nothing can lock the screen (`launcher.py:675-679`). The installer calls `sgdisk`, `mkfs.fat`, `mkfs.btrfs`, `btrfs`, `genfstab`, `arch-chroot`, `snapper`, `mkinitcpio` and `grub-install`. `pyside6` runs the apps, `python-cryptography` the `Vault` and `inter-font` the `Inter` in `Theme.qml`. `nodejs` and `npm` let `bombadil-setup` install a CLI that is missing (`bombadil-setup:12-13`); the build host needs them too, to bake the CLIs in (`scripts/build-iso.sh:19-23`).
3. `tests/test_iso_profile.py` pins the sound packages (`test_there_is_sound`), and `bombadil-smoke:18` checks that `Hyprland quickshell python3 chromium agentd bombadil bombadil-app bombadil-os-mcp` are on the path. Change those checks with the list.
4. Test: `pytest tests/test_iso_profile.py`, then build and run `scripts/test-vm.sh` as in recipe 10.

## Before you publish a fork

| Check | Why | Where |
|---|---|---|
| No personal data in the tree or the history | A fork copies the history. | Run `python3 docs/tools/check_docs.py --private-terms my-terms.txt` for the docs and `python3 docs/tools/check_docs.py --no-links src shell iso scripts tests share bin` for the code. The checker flags e-mail addresses outside example domains, session ids, `claude.ai` working URLs, `/mnt/...` shared-folder paths, Windows user paths, home folders other than `/home/user`, and token shapes; names of people and machines only through `--private-terms`, one per line. At `a30ebc8` the code run lists fixtures (a URL with a password in it, systemd instance unit names, a placeholder home folder in the tests, and the WSLg folder in `scripts/wsl-vm.sh:124` and `scripts/vm-tools/README.md:54`): judge each. By hand: the copyright line in `LICENSE`, the author name and e-mail on every commit (`git log --format='%an %ae'`), the notes under `docs/review/` and `docs/history.md`, the screenshots in `docs/screens/`, and `tests/qml/style_gallery.qml:64`, which shows an address on a domain the checker does not accept as an example (use `example.com`). |
| The security model | The agent has the person's rights and root. | [If you fork it](security-model.md#if-you-fork-it) has the table to work through before an image goes to anyone: a password, a lock screen, the socket's mode, boot entries 02 and 03, pinned CLI versions, the home folder's mode. Add nothing to it here; read it there. |
| What the installer erases | The prompt names a disk, and one boot entry does not ask. | `bombadil-install` runs `sgdisk -Z` on the disk you give it (`:11`), makes a 1 GiB EFI partition and one btrfs partition over the rest (`:12-16`), and asks for the disk name unless `--yes` is passed (`:7-10`). Boot entry 03 (`iso/efiboot/loader/entries/03-bombadil-install-test.conf`) runs the install to `/dev/vda` with `--yes` (`bombadil-smoke:304-305`), so it asks nothing. For that reason `scripts/run-vm.sh` refuses to attach a disk that already holds a system (`:31-36`). Remove entries 02 and 03 from an image you publish. The installer also copies the live session's sign-in (`.claude`, `.claude.json`, `.codex`, `.config/bombadil`) to the disk (`:71-77`): install for someone else only from a session that is not signed in as you. |
| Passwordless sudo | The account `user` has no password and can run anything as root. | `iso/airootfs/etc/sudoers.d/bombadil:2` (`user ALL=(ALL) NOPASSWD: ALL`), `passwd -d user` in `bombadil-live.service:7` and `bombadil-install:55`, and `iso/airootfs/etc/greetd/config.toml`, which logs `user` in without asking. Say so on the page that offers the download, or change it as the security model's table says. |
| The license holder | `LICENSE` is MIT with one copyright line. | The licence text requires that notice in all copies and substantial portions. Keep it, add a line for your own work, and name yourself in `pyproject.toml` if you want an author there: it has no `authors` key. |
| Marks that are not yours | The orange `accent` is `#d97757`, which [the identity brief](design/identity-brief.md#the-rules) calls the exact colour of Claude's logo, and the pill names Claude and Codex. | Decide whether your fork keeps them: the colour is pinned at `tests/test_theme.py:72`, and the names are in `PROVIDER_WORDS` (`launcher.py:61`) and `PROVIDER_HOSTS` (`sysmap.py:168`). |
| The whole suite, then the image | A static check passes on an image that does not boot. | `pytest`, then `scripts/test-vm.sh` and `MODE=install scripts/test-vm.sh` ([development](contributing/development.md)). |

## Keeping your docs honest

The docs of a fork go stale the way its code does. [Documenting your piece](contributing/documenting.md) says where each kind of change is written down, what shape a page has, what the status words mean (`Shipped`, `Partly shipped`, `In progress`, `Designed`, `Idea`) and how `docs/tools/check_docs.py` checks links, anchors, diagrams and leaks. Two habits keep a fork's pages true. Change the page in the same commit as the code. Write a `Verified` line only after reading the code that backs the page. When you rename the product in code, rename it in the prose too: the checker finds a broken link and a leaked address, not an old name.
