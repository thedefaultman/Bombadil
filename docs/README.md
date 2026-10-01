# Bombadil docs

Everything about how Bombadil is designed, how it is built and how to build on it, next to its code. The documents describe the product, not the people who work on it. If you have just cloned the repository, read the first section in order and then jump to what you want to do.

## Start here

1. [The README](../README.md): what Bombadil is, the parts in one table, how to try it.
2. [The principles](principles.md): the rules the design keeps, with a checklist for any change. They decide most questions before you ask them.
3. [The architecture](ARCHITECTURE.md): the parts, the path of one turn, where the code lives.
4. [The development guide](contributing/development.md): run it from a checkout, run the tests, and what each layer of testing proves.
5. [Making it yours](forking.md): what carries Bombadil's identity, what to keep in a fork, and recipes for the common changes.

[CONTRIBUTING.md](../CONTRIBUTING.md) is the short version for a pull request, and [AGENTS.md](../AGENTS.md) is the same for an AI coding agent.

## I want to understand a piece

| Piece | Status | Page |
|---|---|---|
| The session daemon, the queue, providers | Shipped | [agentd](architecture/agentd.md) |
| The OS as tools for the agent | Shipped | [os-mcp](architecture/os-mcp.md) |
| The pill, the line, the stone, chips, wallpaper | Partly shipped | [shell](architecture/shell.md) |
| Cards in rails and strips, jobs | Partly shipped | [desk](architecture/desk.md) |
| Apps the agent writes, the kit and its skill | Partly shipped | [app kit](architecture/app-kit.md) |
| The browser panel and signing in | Partly shipped | [browser and sign-in](architecture/browser-and-signin.md) |
| Diagrams and reasons above the pill | Partly shipped | [cards and pictures](architecture/cards-and-pictures.md) |
| Undo | Partly shipped | [restore points](architecture/restore-points.md) |
| The image, the installer, boot | Shipped | [ISO and install](architecture/iso-and-install.md) |
| The index that writes itself | Partly shipped | [brain](architecture/brain.md) |
| Named coding sessions | In progress | [coding sessions](architecture/coding-sessions.md) |
| A name, voices and welcome lines | In progress | [voice](architecture/voice.md) |
| Noticing repeated asks, self-checks | In progress | [loop](architecture/loop.md) |
| Mail and everyday work | In progress | [mail](architecture/mail.md) |
| When the AI's plan runs out | In progress | [poor man switch](architecture/poor-man-switch.md) |
| An encrypted installed system | Designed | [installed OS](architecture/installed-os.md) |

## I want to know how it should look and feel

- [The interaction model](ux/README.md): the surfaces, the stone's states, one conversation, and a gallery of screens.
- [Flows](ux/flows.md): first boot, sign-in, a turn, undo, building an app, and what the person sees when something fails.
- [Keys, timings and limits](ux/keys-and-timings.md): every key, duration and limit, with where it is defined.
- [The design system](design-system/README.md): colour, type, shape, motion, icons and the tokens that carry them.
- [Brand assets](brand/README.md): the mark and its files.
- [Design briefs](design/README.md): the reasoning behind each piece, written before it was built, each with a drawn page of mock-ups.

## I want to know what was decided and what is next

- [The decision log](decisions.md): every decision the briefs record, with its reason, its status and where it is built.
- [The roadmap](roadmap.md): what is shipped, in progress and designed.
- [Known issues](known-issues.md): defects that are still true.
- [History](history.md): how the project got here, in order.
- [The glossary](glossary.md): the project's own words.

## I want to look something up or check my work

- [Reference](reference.md): every command, environment variable, file, socket, unit and configuration key, with the page that owns it.
- [The security model](security-model.md): what the agent can do, who can reach what, and what undo covers and does not.
- [Documenting your piece](contributing/documenting.md): where things go, the shape of a page, diagrams, what must never be committed, and the checker.
- [Writing a design brief](contributing/design-briefs.md).
- [Measuring the desk](contributing/measuring-the-desk.md): measuring the stone and the pill from outside a VM.
- `docs/screens/`: the screenshots the pages show. `docs/tools/check_docs.py`: the docs checker (`python3 docs/tools/check_docs.py --mermaid`).
