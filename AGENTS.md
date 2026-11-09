# Working in this repository

This file is for AI coding agents, and for people who want the short version. Bombadil is a Linux distribution whose main interface is an AI agent. It has a strong point of view about how it should feel, and a change that ignores it is sent back even if it works.

## Read first

1. [`README.md`](README.md): what Bombadil is and the parts in one table.
2. [`docs/principles.md`](docs/principles.md): the rules the design keeps, with a twelve-question checklist to run against every change.
3. [`docs/architecture/`](docs/architecture/): one page per piece (`agentd`, `os-mcp`, `shell`, `desk`, `app-kit`, ...). Read the page of the piece you touch. Each has a section on extending it and one on the tests that cover it.
4. [`docs/README.md`](docs/README.md): the index of everything else (design briefs, decisions, roadmap, glossary, security model, reference).

## Run it and test it

```sh
python3 -m pip install -e '.[dev,apps]'
python3 -m pip install cryptography        # the Vault tests; no extra installs it
pytest                                     # unit and QML tests; QML tests skip without PySide6
ruff check src tests bin scripts share iso
python3 docs/tools/check_docs.py --mermaid # links, anchors, diagrams, leaked personal details
```

A fresh checkout is not clean, so compare with the baseline for the files you touched: `ruff check` already reports findings that predate your change (63 with the rules of ruff 0.15.20, 524 with those of 0.16.9), and `--mermaid` needs Mermaid's CLI (`mmdc`; leave the flag off without it). The headless desktop test, the ISO build and the VM smoke need Docker or QEMU. [`docs/contributing/development.md`](docs/contributing/development.md) says what fails and why, what each layer proves and which one covers your change.

## Rules

- **Document the change in the same pull request.** Update the page of the piece you changed, and [the decision log](docs/decisions/README.md) or [the roadmap](docs/roadmap.md) if you decided or shipped something. [How, and what never to commit](docs/contributing/documenting.md).
- **A new surface or a change in how something feels starts as a design brief**, not as code: [how to write one](docs/contributing/design-briefs.md).
- **Nothing personal or sensitive in the repository.** No names, e-mail addresses, machine or folder names, credentials, session or chat links, or real accounts in code, tests, fixtures, screenshots or docs. Use made-up people (`alex`) and made-up accounts (`acme`, `example.com`). `docs/tools/check_docs.py` catches the common shapes; it cannot catch a name it does not know.
- **Keep the principles.** The ones that break the product soonest when ignored: the person is a passenger and sees what the machine does; full access is made safe by undo, not by permission prompts; something true is on screen within 200 ms; nothing the person cannot see and stop runs on its own; one design language, taken from `share/qml/Bombadil/Theme.qml` (a hex or named colour, or a `Text` without `font.family`, in `shell/` fails `tests/test_theme.py`; sizes are kept by convention only).
- **Do not weaken a test to get a green run**, and do not skip one. A test that samples an animation in real time can fail on a loaded machine; say so and rerun it alone.
- **Describe what a person sees** at the top of a pull request: what it looked like before, what it looks like after, then in a sentence what the change does and how.

## Where things are

| You want to change | Look in |
|---|---|
| What the agent can do on the machine | `src/bombadil/mcp_server.py`, which also registers the tools of `cardtools.py`, `mail/tools.py` and `appkit/tools.py`, and [os-mcp](docs/architecture/os-mcp.md) |
| How a turn runs, the queue, Stop, providers | `src/bombadil/agentd.py`, `providers.py` and [agentd](docs/architecture/agentd.md) |
| The pill, the line, the stone, chips | `shell/` and [shell](docs/architecture/shell.md) |
| The desk: cards in rails, strips, dragging a card, jobs | `shell/Desk*`, `src/bombadil/desk.py`, `jobs.py` and [desk](docs/architecture/desk.md) |
| Cards and pictures above the pill | `src/bombadil/cards.py`, `cardtools.py`, `sysmap.py` and [cards and pictures](docs/architecture/cards-and-pictures.md) |
| Apps the agent writes | `share/qml/Bombadil/`, `src/bombadil/appkit/` and [app kit](docs/architecture/app-kit.md) |
| Signing in and the browser panel | `src/bombadil/signin.py`, `browser.py` and [browser and sign-in](docs/architecture/browser-and-signin.md) |
| Restore points and undo | `src/bombadil/snapshots.py` and [restore points](docs/architecture/restore-points.md) |
| Mail | `src/bombadil/mail/`, `share/apps/mail/` and [mail](docs/architecture/mail.md) |
| The brain and the Brain window | `src/bombadil/brain/`, `share/apps/brain/` and [brain](docs/architecture/brain.md) |
| The look (colours, type, motion) | `share/qml/Bombadil/Theme.qml` and [design system](docs/design-system/README.md) |
| The image, the installer, the boot | `iso/`, `scripts/` and [ISO and install](docs/architecture/iso-and-install.md) |
| What restarts the daemon, the bar and notifications | `iso/airootfs/etc/systemd/user/`, `iso/airootfs/etc/skel/.config/hypr/hyprland.lua` and [session services](docs/architecture/session-services.md) |

[`docs/forking.md`](docs/forking.md) covers making a fork of your own and what to keep when you do.
