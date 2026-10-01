# Working in this repository

This file is for AI coding agents, and for people who want the short version. Bombadil is a Linux distribution whose main interface is an AI agent. It has a strong point of view about how it should feel, and a change that ignores it is sent back even if it works.

## Read first

1. [`README.md`](README.md): what Bombadil is and the parts in one table.
2. [`docs/principles.md`](docs/principles.md): the rules the design keeps, with a twelve-question checklist to run against every change.
3. [`docs/architecture/`](docs/architecture/): one page per piece (`agentd`, `os-mcp`, `shell`, `desk`, `app-kit`, ...). Read the page of the piece you touch. Each ends with how to extend it and which tests cover it.
4. [`docs/README.md`](docs/README.md): the index of everything else (design briefs, decisions, roadmap, glossary, security model, reference).

## Run it and test it

```sh
python3 -m pip install -e '.[dev,apps]'
pytest                                     # unit and QML tests; QML tests skip without PySide6
ruff check src tests bin scripts share iso
python3 docs/tools/check_docs.py --mermaid # links, anchors, diagrams, leaked personal details
```

The headless desktop test, the ISO build and the VM smoke need Docker or QEMU: see [`docs/contributing/development.md`](docs/contributing/development.md) for what each layer proves and which one covers your change.

## Rules

- **Document the change in the same pull request.** Update the page of the piece you changed, and [the decision log](docs/decisions.md) or [the roadmap](docs/roadmap.md) if you decided or shipped something. [How, and what never to commit](docs/contributing/documenting.md).
- **A new surface or a change in how something feels starts as a design brief**, not as code: [how to write one](docs/contributing/design-briefs.md).
- **Nothing personal or sensitive in the repository.** No names, e-mail addresses, machine or folder names, credentials, session or chat links, or real accounts in code, tests, fixtures, screenshots or docs. Use made-up people (`alex`) and made-up accounts (`acme`, `example.com`). `docs/tools/check_docs.py` catches the common shapes; it cannot catch a name it does not know.
- **Keep the principles.** The ones that break the product soonest when ignored: the person is a passenger and sees what the machine does; full access is made safe by undo, not by permission prompts; something true is on screen within 200 ms; nothing the person cannot see and stop runs on its own; one design language, taken from `share/qml/Bombadil/Theme.qml` (a new colour or size literal in `shell/` fails `tests/test_theme.py`).
- **Do not weaken a test to get a green run**, and do not skip one. A test that samples an animation in real time can fail on a loaded machine; say so and rerun it alone.
- **Describe what a person sees** at the top of a pull request: what it looked like before, what it looks like after, then in a sentence what the change does and how.

## Where things are

| You want to change | Look in |
|---|---|
| What the agent can do on the machine | `src/bombadil/mcp_server.py` and [os-mcp](docs/architecture/os-mcp.md) |
| How a turn runs, the queue, Stop, providers | `src/bombadil/agentd.py`, `providers.py` and [agentd](docs/architecture/agentd.md) |
| The pill, the line, the stone, chips | `shell/` and [shell](docs/architecture/shell.md) |
| Apps the agent writes | `share/qml/Bombadil/`, `src/bombadil/appkit/` and [app kit](docs/architecture/app-kit.md) |
| The look (colours, type, motion) | `share/qml/Bombadil/Theme.qml` and [design system](docs/design-system/README.md) |
| The image, the installer, the boot | `iso/`, `scripts/` and [ISO and install](docs/architecture/iso-and-install.md) |

[`docs/forking.md`](docs/forking.md) covers making a fork of your own and what to keep when you do.
