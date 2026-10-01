# Documenting your piece

Every pull request that changes what Bombadil does also changes the page that describes it, in the
same pull request. This page says where each kind of fact lives, how a page is shaped, how to draw
the diagrams, what must never be committed, and how to check all of it.

The goal of the docs is that someone who forks the repository can understand the system, build on it
and keep its [principles](../principles.md) without asking the people who wrote it.

## Where things go

| Path | What it holds |
|---|---|
| `README.md` | what Bombadil is, the parts in one table, how to try it, and a pointer into `docs/` |
| `docs/README.md` | the index: where to start, by what you want to do |
| `docs/principles.md` | the rules the design keeps, with the checklist to run against a change |
| `docs/ARCHITECTURE.md` | the parts and how they connect, with one diagram |
| `docs/architecture/<piece>.md` | one page per piece: how it works, its interfaces, how to extend it |
| `docs/design/*-brief.md` | the design briefs: the reasoning and the decisions behind a piece, written before it is built |
| `docs/design/pages/*.html` | the same briefs as drawn pages, with mock-ups |
| `docs/design-system/README.md` | the look and the wording of the interface, with token names |
| `docs/decisions.md` | the decision log: what was decided, when, why, and where it is built |
| `docs/roadmap.md` | what is shipped, in progress and designed |
| `docs/ux/README.md` | the interaction model: surfaces, states, keys, timings, flows |
| `docs/contributing/` | how to develop, how to document, how to write a design brief |
| `docs/glossary.md` | the project's own words |
| `docs/known-issues.md` | defects that are still true |
| `docs/history.md` | how the project got here, in order |

## What to update when you change something

| You changed | Update |
|---|---|
| a socket message, an event, a config key or an environment variable | `architecture/agentd.md` |
| an `os-mcp` tool | `architecture/os-mcp.md`, and the skill or system prompt if the agent needs to know |
| a card type or a `system_map` subject | `architecture/cards-and-pictures.md` |
| a kit component or a native binding | `architecture/app-kit.md` and the skill's `references/` |
| a shell component, a pill state or a key | `architecture/shell.md` and `ux/README.md` |
| a desk widget or a job kind | `architecture/desk.md` |
| a colour, size, type or motion value | `design-system/README.md` |
| the ISO, the installer or the boot flow | `architecture/iso-and-install.md` |
| a decision that changes an earlier one | `decisions.md`, and the brief that held the old decision |
| the status of a piece | `roadmap.md` |
| a new piece | a new `architecture/<piece>.md`, a row in `ARCHITECTURE.md`, a line in `docs/README.md` |

If your piece has a design brief, link it from the top of the page and keep the page to what exists.
The brief says what was meant; the page says what is true.

## The shape of a page

A page starts with a status block and one plain paragraph.

```markdown
# Title

> **Status:** Shipped
> **Code:** `src/bombadil/example.py`, `shell/Example.qml`
> **Design:** [Name of brief](../design/name-brief.md)
> **Verified:** 2026-10-01 against `main` at `abc1234`
```

| Status | Means |
|---|---|
| Shipped | on `main`, exercised by tests or a documented check |
| Partly shipped | some of the pieces the design names are on `main`; the page says which |
| In progress | on a branch or an open pull request, not on `main` |
| Designed | a decided design with no code |
| Idea | proposed, not decided |

"Verified" means you read the code behind every concrete claim on the page on that date. Do not write
it on a page you did not check, and update it when you re-check.

A piece page has these parts, in this order, so a reader learns the shape once:

1. What the piece is and why it exists, in two or three sentences.
2. **How it works**, with a diagram.
3. **Interfaces other pieces depend on**: a table of exact names (messages, events, tools, files,
   configuration keys, environment variables, commands).
4. **Where state lives**: files, sockets, databases and units, with paths.
5. **Principles it keeps**: three to six bullets that link a rule in [principles](../principles.md) and
   say what it means for this piece, including the trap a contributor could fall into.
6. **Extending it**: numbered steps for the common additions, taken from how the code registers things today.
7. **Tests**: which tests cover it and how to run them.
8. **Known gaps**: only gaps you verified in code, each with the file that shows it.

## Style

- Plain full sentences and sentence-case headings. No emoji, no exclamation marks, no hype, no "we", no "simply".
- Avoid em dashes; use a full stop, a comma or a colon.
- Name things exactly as the code does, in backticks. Paths are relative to the repository root.
- Prefer a table or a diagram to a paragraph when the content is a catalogue or a flow.
- Do not duplicate a fact; link to the page that owns it.
- Do not write "currently", "now" or "new" without a date. State the fact, or the fact and its date.
- Examples use neutral names such as `alex`, `acme` and `example.com`.

## Diagrams

Draw diagrams in Mermaid, in a fenced block with the language `mermaid`. GitHub renders them, the
source diffs cleanly, and the next contributor can edit them.

- Keep one diagram to about twelve nodes and split bigger ones.
- Use `flowchart LR` or `TB`, `sequenceDiagram` and `stateDiagram-v2`.
- Quote any label that contains punctuation: `A["agentd (daemon)"]`.
- Do not set colours or themes. The viewer's theme decides.
- A diagram earns its place by showing something the prose would make the reader assemble in their head:
  a flow of events, a lifecycle, a set of states.

## What must never be committed

The docs describe the product, not the people who work on it. Keep these out of every text file,
every image and every log in the repository:

- names, e-mail addresses, handles and phone numbers of people, other than the licence holder in `LICENSE`
  and the authors Git records;
- host names, machine names, drive letters and folders from anyone's computer, and which hardware or
  accounts someone tests on;
- links to a conversation, a chat session, a private project or a private document, and quotes from a
  private conversation;
- credentials of any kind: tokens, keys, cookies, account names;
- sample data that is real. Use made-up people and made-up accounts in examples, fixtures and screenshots;
- screenshots that show a username, a host name, a path or a wifi name. Check the title bar, the
  terminal prompt and any list. Strip image metadata.

Keep a private list of terms that must not appear (your own name, your machine names) outside the
repository, and pass it to the checker. A check can then fail the build on the one thing a generic
pattern cannot know.

Removing a line from `main` does not remove it from Git history. If something sensitive was committed, tell
the maintainers: the history may need rewriting, which is their decision.

## Checking your docs

```sh
python3 docs/tools/check_docs.py                       # links, anchors and leak patterns
python3 docs/tools/check_docs.py --mermaid             # also renders every diagram
python3 docs/tools/check_docs.py --private-terms ~/my-terms.txt docs/architecture/os-mcp.md
```

The checker has no dependencies. It verifies that every relative link and anchor in Markdown and HTML
resolves, scans for e-mail addresses, session identifiers, working claude.ai URLs, paths of the shared
project folder, home-directory paths and token shapes, and reports any term from `--private-terms`
(one per line, whole words, case-insensitive). `--soft-terms` takes a second list that is reported for a
human to judge and never fails the run.

`--mermaid` renders each diagram with Mermaid's command line tool. Install it with
`npm install --global @mermaid-js/mermaid-cli`. In a container that runs the browser as root, point
`MMDC_CONFIG` at a JSON file such as `{"args": ["--no-sandbox"]}` and, if the browser is not
downloaded by the tool, add its `executablePath`.

Run it before you push. It is cheap, and a broken link or a leaked name is much cheaper to fix before review.
