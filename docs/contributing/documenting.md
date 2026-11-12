# Documenting your piece

> **Status:** In progress
> **Code:** `docs/tools/check_docs.py`. This guide and the checker are on the documentation branch and not yet on `main`; the status becomes Shipped when that branch merges
> **Design:** there is no brief for the docs; the pages that follow this guide are the examples, starting with [the architecture overview](../ARCHITECTURE.md)
> **Verified:** 2026-11-12 against `main` at `7d37bc5` and the documentation branch, for what changed since 2026-11-08: the list of contract pages, by searching `src/`, `bin/`, `share/`, `shell/`, `iso/`, `scripts/`, `tests/` and `install/` on `main` for the comments that name each, and by reading the top of each for a status block; the owners named in the table of what to update against the pages in `docs/architecture/` and their Status blocks; the Tips rule against [TIPS.md](../TIPS.md#how-a-tip-is-added) and `CONTRIBUTING.md`; the idle-cost row against [IDLE-COST.md](../IDLE-COST.md#rules-for-adding-to-the-shell); the installed-OS row against the headings and Interfaces table of [the installed OS](../architecture/installed-os.md). Before that, 2026-11-08 against `main` at `9d5c729`, and for what `main` lacks (it has no `docs/tools/` and no `docs/contributing/`) against the documentation branch: the checker section by running `docs/tools/check_docs.py` as it stands on that branch, on sample files for every check, option and exit status it names; both tables against the files in the tree of that branch and the headings of the pages they name; the status words against [the roadmap](../roadmap.md). The rules for style and diagrams are rules, not claims about code, and were not checked against anything.

Every pull request that changes what Bombadil does also changes the page that describes it, in the
same pull request. This page says where each kind of fact lives, how a page is shaped, how to draw
the diagrams, what must never be committed, and how to check all of it.

The goal of the docs is that someone who forks the repository can understand the system, build on it
and keep its [principles](../principles.md) without asking the people who wrote it.

## Where things go

| Path | What it holds |
|---|---|
| `README.md` | what Bombadil is, how the parts fit (a diagram and a table), how to try it, where it stands, and a pointer into `docs/` |
| `CONTRIBUTING.md`, `AGENTS.md` | the short version for a pull request, one for contributors and one for AI coding agents; `AGENTS.md` also has the table of where in the code each kind of change goes |
| `docs/README.md` | the index: where to start, by what you want to do |
| `docs/principles.md` | the rules the design keeps, with the checklist to run against a change |
| `docs/ARCHITECTURE.md` | the parts and how they connect, the path of one turn and where the code lives, with a diagram for the parts and one for the turn |
| `docs/architecture/<piece>.md` | one page per piece: how it works, its interfaces, how to extend it |
| `docs/design/README.md` | the index of the briefs: the reading order, a table with each brief's status, and the notes that are not briefs |
| `docs/design/*-brief.md` | the design briefs: the reasoning and the decisions behind a piece, written before it is built |
| the other notes in `docs/design/` | notes that are not briefs, such as the foundation choices and the record of boot and the wallpaper; each has a status block |
| `docs/design/pages/*.html` | the briefs as drawn pages, with mock-ups |
| `docs/design-system/README.md` | the look and the wording of the interface, with token names |
| `docs/decisions/` | the decision log, one file per area: what was decided, when, why, and where it is built |
| `docs/roadmap.md` | what is shipped, in progress and designed |
| `docs/ux/README.md` | the interaction model: the surfaces, the stone's states, one conversation, and a gallery of screens |
| `docs/ux/flows.md` | the flows from first boot to coding sessions and the Away card, each with where it breaks and what the person sees |
| `docs/ux/keys-and-timings.md` | every key, duration, size, limit and launcher word, with where the code defines it |
| `docs/reference.md` | every command, environment variable, file, socket, unit, configuration key, tool and event, one line each, with the page that owns the detail |
| `docs/security-model.md` | what the agent can do, who can reach what, and what undo covers and does not |
| `docs/forking.md` | making a fork: what carries Bombadil's identity, what to keep, recipes for the common changes |
| `docs/MAIL.md`, `docs/BRAIN.md`, `docs/CONNECT.md`, `docs/TIPS.md`, `docs/TIP-SCENES.md`, `docs/MOTION.md`, `docs/IDLE-COST.md` | the contracts: the wire formats, states and rules of Mail, of the brain, of connections and of Tips, the vocabulary of a tip's scene, the one switch for reduced motion, and what the desktop may cost at rest. Comments in the code, the add-on and the tests point to them by name: to `MAIL.md` in more than twenty files, to `CONNECT.md` in more than twenty, to `TIPS.md` in eleven, to `TIP-SCENES.md` in seven and to `MOTION.md` and `IDLE-COST.md` in three each. For the brain, one comment in `tests/test_brain_watch.py` points to the contract a replacement watcher has to keep, "The watcher's edge" in `BRAIN.md`. All stay as flat files; the piece pages say how each piece works |
| `docs/contributing/` | how to develop, how to test the whole system in a VM, how to document, how to write a design brief |
| `docs/glossary.md` | the project's own words |
| `docs/known-issues.md` | defects that are still true |
| `docs/history.md` | how the project got here, in order |
| `docs/brand/` | the mark and its files, with the rules for using them |
| `docs/screens/`, `docs/mail/screens/` | the screenshots the pages show, and the logs of the runs that took them. The rule on screenshots under "What must never be committed" applies, and the checker does not read images |
| `docs/tools/check_docs.py` | the docs checker, described under "Checking your docs" |

## What to update when you change something

| You changed | Update |
|---|---|
| a command, a socket message, an event, a tool, a file, a unit, a configuration key or an environment variable | the page of the piece that owns it, and its row in `reference.md`. The owners: `architecture/agentd.md` for the agent socket, `architecture/mail.md` and `MAIL.md` for the Mail socket, `architecture/brain.md` and `BRAIN.md` for the brain, `architecture/browser-and-signin.md` for the browser panel and sign-in, `architecture/iso-and-install.md` and `contributing/development.md` for build and VM variables, `architecture/installed-os.md` for the installer's own switches and the files it writes on the disk |
| an `os-mcp` tool | `architecture/os-mcp.md`, and the skill or system prompt if the agent needs to know |
| a card type or a `system_map` subject | `architecture/cards-and-pictures.md` |
| a kit component or a native binding | `architecture/app-kit.md` and the skill's `references/` |
| a shell component or a pill state | `architecture/shell.md`, and `ux/README.md` for a state |
| a key, a timing, a limit or a launcher word | `ux/keys-and-timings.md`, and `architecture/shell.md` for a Hyprland bind or `architecture/agentd.md` for a launcher word |
| a step the person takes or sees: first boot, sign-in, a turn, undo, a failure | `ux/flows.md` |
| a desk widget, a card on the desk, how a card is dragged or folded by hand, or a job kind | `architecture/desk.md`, and `ux/keys-and-timings.md` for the gesture |
| a colour, size, type or motion value | `design-system/README.md` |
| a systemd unit, or what restarts the session | `architecture/session-services.md`, and the page of the piece the unit runs (`architecture/agentd.md`, `architecture/shell.md`, `architecture/mail.md`, `architecture/brain.md`) |
| a restore point, how many are kept, or undo | `architecture/restore-points.md` |
| the browser panel or provider sign-in | `architecture/browser-and-signin.md` |
| Mail or the brain | `architecture/mail.md` with `MAIL.md`, `architecture/brain.md` with `BRAIN.md` |
| the ISO, the stick's installer or the boot flow | `architecture/iso-and-install.md` |
| what an install writes on the disk (the layout, encryption, the carry list, the refresh, the GRUB screen, `bombadil remote`, `bombadil snapshot`) | `architecture/installed-os.md`, and `architecture/iso-and-install.md` for the part that runs on the stick |
| a tip, its scene or the Tips app | `architecture/tips.md` with `TIPS.md` and `TIP-SCENES.md`. A change that adds something a person can ask ships its tip row and scene in the same pull request, behind a `needs` gate until its code is on `main` ([how a tip is added](../TIPS.md#how-a-tip-is-added)) |
| a timer, a poll or an animation in the shell, or the stone's idle cost | `architecture/idle-cost.md` with `IDLE-COST.md`, and a scene in `tests/desktop/idle.py` when the feature has a state that can last ([the rules](../IDLE-COST.md#rules-for-adding-to-the-shell)) |
| a test, or how the tests run | `contributing/development.md`, and the Tests part of the piece's page |
| a smoke check, a mode of `scripts/test-vm.sh` or a VM tool | `contributing/vm-testing.md` and `contributing/development.md`; the check names are catalogued in `architecture/iso-and-install.md#the-smoke-test` |
| anything that widens what the agent can do or reach | `security-model.md`, as well as the page of the piece |
| a name or a word the project uses | `reference.md` and `glossary.md` |
| a defect you found or fixed | `known-issues.md` |
| something that carries the project's identity: its name, mark, colours, paths or the `user` account | `forking.md` |
| a decision, or one that replaces an earlier one | the rows in `decisions/` (the earlier row gets "Superseded by D-nnn" and the later row's Decision starts with "Replaces D-nnn:"), and the brief that held the old decision |
| the status of a piece | the Status line of its page, its row in `roadmap.md`, and the places that repeat it: the Status columns of `ARCHITECTURE.md`, `docs/README.md` and `design/README.md`, the status block of its brief and the Built column of its rows in `decisions/` |
| a piece ships | a dated line in `history.md`, as well as the status |
| a piece you add | an `architecture/<piece>.md`, a row in `ARCHITECTURE.md` (and in its diagram if the piece joins the flow), a row in `docs/README.md` and one in `roadmap.md`, and a line in the table of `AGENTS.md` |

If your piece has a design brief, link it from the top of the page and keep the page to what exists.
The brief says what was meant; the page says what is true.

## The shape of a page

A page about a piece, a guide or a list starts with a status block and one plain paragraph. A line that
does not apply may be left out (`known-issues.md` has no Status line). The entry and index pages have no block: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`,
`docs/README.md`, `docs/design/README.md` and every file in `docs/decisions/`, and so do the contract pages
(`MAIL.md`, `BRAIN.md`, `CONNECT.md`, `TIPS.md`, `TIP-SCENES.md`, `MOTION.md` and `IDLE-COST.md`).
`roadmap.md` and `history.md` have a `Covers` line and a `Verified` line instead.
A design brief adds `Decided by` and sometimes `Pieces`, `Built differently` and a reading note
([how](design-briefs.md#the-top-of-a-brief)).

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
6. **Extending it**: numbered steps for the common additions, taken from how the code registers things
   on the Verified date.
7. **Tests**: which tests cover it and how to run them.
8. **Known gaps**: only gaps you verified in code, each with the file that shows it.

A piece that is not Shipped keeps this order and adds three things. Right after the first paragraph, a
section says which parts are on `main` (`What is built and what is designed` on
`architecture/cards-and-pictures.md`, `What is built` on the design system page, `What it will do` on
`architecture/loop.md` and `architecture/voice.md`). What is only designed, or built on a branch that is
not merged, is written in the future tense and labelled `(designed)` or `(built on an unmerged branch)`,
and it is never mixed into a table that describes `main`: a separate table such as `Interfaces it adds`
lists it with its status. The page ends with what changes when the piece ships: the status, the Verified
line, the roadmap row and the brief's status block (`When it ships` on those two pages).

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
python3 docs/tools/check_docs.py                       # links, anchors and leak patterns in the default scope
python3 docs/tools/check_docs.py --mermaid             # also renders every diagram
python3 docs/tools/check_docs.py --private-terms ~/my-terms.txt docs/architecture/os-mcp.md
python3 docs/tools/check_docs.py --no-links --private-terms ~/my-terms.txt src shell iso scripts tests share bin
```

The checker has no dependencies. Run it from anywhere: every `PATH` is a file or a folder relative to the
repository root (or to `--root DIR`, which points it at a copy), and with no `PATH` it reads `docs/`,
`README.md`, `CONTRIBUTING.md` and `AGENTS.md`. The last command of the example above is the run for
the rest of the repository: `--no-links` skips the link checks and `--no-leaks` skips the leak checks, so that
run looks for leaks only. It reports fixtures too, such as the sample addresses in the Mail tests and the
placeholder home folders, so judge each hit.

**Links.** It verifies that every relative link and anchor in Markdown and HTML resolves: Markdown links,
images and reference definitions (a link's text may wrap over lines), the `href`, `src`, `poster` and
`srcset` of HTML, and the anchor of a `#` heading by GitHub's rule or of an `<a name>` or `<a id>`; a
heading underlined with `===` is not recognised. A link to a file outside the repository is an error too.
Code fences and code spans are not read for links. A link cannot carry a line number: `page.md:12` is read
as a path and fails. A code fence that is never closed is an error, because the rest of the file would be
read as code. A diagram fence may use three backticks or three tildes.

**Leaks.** It scans for e-mail addresses, session, message and account identifiers, working claude.ai
URLs, paths under `/mnt` that belong to the shared project folder, a Windows drive or the WSLg folder,
Windows and macOS user paths and home folders other than `/home/user`, and token shapes. These e-mail
addresses are not reported: addresses at `example.com`, `example.org` and `example.net`, with any
subdomain; at `acme.` followed by one top-level name, so `acme.io` but not `acme.co.uk`; at the reserved
`.test`, `.example` and `.invalid` names; at `users.noreply.github.com` (GitHub's plain
no-reply address at `github.com` is reported); `noreply@anthropic.com`, the address in the Claude commit
trailer; and systemd unit names such as `getty@tty1.service`. The checker also reports any term from `--private-terms`: one per line, whole
words, case-insensitive, and a line that starts with `#` is a comment. `--soft-terms` takes a second list
in the same format; its hits are listed for a human to judge and never fail the run.

**What it reads.** Only text files: `.md`, `.html`, `.htm`, `.txt`, `.json`, `.svg`, `.toml`, `.qml`,
`.py`, `.sh`, `.log`, `.patch` and `LICENSE`. Links and anchors are read in `.md`, `.html` and `.htm` files
only; the other text files are read for leaks and private terms. Screenshots and other bitmap images are
never scanned, so check them by eye as the section above says. Files with any other suffix or none (the
commands in `bin/` and the image's scripts in `iso/airootfs/usr/local/bin/`, `.service` and `.conf` files,
`.js` and `.lua`) are not read either; look at those by hand.

**Diagrams.** `--mermaid` renders each diagram in the Markdown files with Mermaid's command line tool,
`mmdc`. Install it with `npm install --global @mermaid-js/mermaid-cli`. `MMDC` is the path of the binary
(default: `mmdc` on `PATH`). In a container that runs the browser as root, point `MMDC_CONFIG` at a JSON
file such as `{"args": ["--no-sandbox"]}` and, if the browser is not downloaded by the tool, add its
`executablePath`.

**Exit status.** 0 when nothing is wrong, 1 when an error is found (a broken link or anchor, a fence that
is never closed, a diagram that does not render, a leak or a private term), and 2 when the check could not
run (a `PATH` or terms file that does not exist, a `PATH` outside the repository, or no `mmdc` with
`--mermaid`). The last line says how many files were found under the paths (images and other files it does
not read are counted), how many errors were found and how many soft hits are left to judge; the soft hits
and, with `--mermaid`, the count of diagrams that rendered are printed as `NOTE` lines above it.

A clean run means the links, anchors, diagrams and leak patterns pass. It does not check that a path, a name
or a `file:line` reference in backticks exists, whether a status block is true, the Verified date, headings,
wording or style such as em dashes, and it cannot catch a name it was not given.

Run it before you push. It is cheap, and a broken link or a leaked name is much cheaper to fix before review.
