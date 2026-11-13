# Contributing to Bombadil

Bombadil is a Linux distribution whose main interface is an AI agent. It has a strong point of view
about how it should feel, and the fastest way to contribute well is to learn that first.

## Start here

1. Read the [README](README.md) for what Bombadil is and how the parts fit.
2. Read the [principles](docs/principles.md). They are short, and they decide most questions before you ask them.
3. Skim the [architecture](docs/ARCHITECTURE.md) and read the page of the piece you want to change in
   [`docs/architecture/`](docs/architecture/). Each has a section on extending it and one on the tests that cover it.
4. Set up and run the tests with [the development guide](docs/contributing/development.md).

[`docs/README.md`](docs/README.md) is the index of everything else: the design briefs, the design system,
the decision log and the roadmap.

## Before you open a pull request

- **Run the tests** for what you touched, and the headless desktop test if you touched the shell
  ([how](docs/contributing/development.md)). A fresh checkout is not clean: `ruff check` already reports
  findings that predate your change, and the Vault tests need the `cryptography` package, which no extra
  installs. The development guide says what to expect.
- **Check the change against the principles.** [The checklist](docs/principles.md#checking-a-change-against-the-principles)
  is twelve questions. A change that asks permission for something undo could reverse, hides what the machine is
  doing, or styles something one-off will be sent back.
- **Document it in the same pull request.** Update the page of the piece you changed, and the decision log or
  the roadmap if you decided or shipped something. [How, and what never to commit](docs/contributing/documenting.md).
- **If the change adds something a person can ask, add its tip too.** The tip row and its scene go in the same
  pull request, gated by a `needs` flag until the code is on `main` ([how](docs/TIPS.md#how-a-tip-is-added)).
- **If the change adds a file to the system** (a unit, a config file, a command), put it where the `bombadil` package expects it. [Packages and updates](docs/architecture/installed-os.md#packages-and-updates) says where, and how a change to a file that another package owns, or one in the person's home, reaches a machine that is already installed.
- **Check the docs:** `python3 docs/tools/check_docs.py --mermaid` (the flag needs Mermaid's CLI; leave it off without it).
- **Describe what a person sees.** Open the pull request description with what it looked like before and
  what it looks like after, then say in a sentence what the change does and how.

## Bigger changes: write the design first

A new surface, a new kind of widget or a change to how something feels starts as a design brief, not as
code. [How to write one](docs/contributing/design-briefs.md). The briefs in
[`docs/design/`](docs/design/) are the examples to follow.

## Licence

The project is under the [MIT licence](LICENSE). By contributing you agree your contribution is under it.
