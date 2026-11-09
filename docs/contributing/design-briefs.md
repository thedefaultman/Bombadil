# Writing a design brief

> **Status:** In progress
> **Code:** none; the briefs in [`docs/design/`](../design/README.md) are the examples. This guide is on the documentation branch and not yet on `main`; the status becomes Shipped when that branch merges
> **Design:** there is no brief for the briefs; [the design index](../design/README.md) lists the twelve that exist
> **Verified:** 2026-11-08 against `main` at `9d5c729`, and for what `main` lacks against the documentation branch: the spine, the headings in use and the status block against the twelve `*-brief.md` files in `docs/design/` as they stand on that branch (`main` has ten of them: `everyday-work-brief.md` and `installed-os-brief.md` are on the branch only); what a brief adds and how a replaced decision is marked against `docs/design/README.md` and `docs/decisions/README.md`, which are on the branch only too.

Anything the person can see or feel in Bombadil starts as a design brief: a document that says what
the experience is, which rules it keeps, what to build first and what was decided, before any code. The
briefs in [`docs/design/`](../design/README.md) are the working examples. This page describes their shape
so a brief you add reads like the others and can be built from without asking its author.

Write a brief when you add a surface (a widget, a card kind, a screen), change how something feels,
or take a decision that other pieces will depend on. A bug fix or a refactor does not need one.

## The top of a brief

A brief gets a status block at the top when it is added, so a reader can tell which parts are built.

```markdown
> **Status:** Designed
> **Code:** none of it is on `main`; it would change `shell/Example.qml`
> **Design:** this brief and its page, [Name of brief](pages/name-of-brief.html)
> **Decided by:** nothing yet
> **Verified:** 2026-10-01 against `main` at `abc1234`: what the status says is built, and the files it names
```

The lines are the ones in [Documenting your piece](documenting.md#the-shape-of-a-page), with these
differences:

- **Design** links the brief's drawn page in `docs/design/pages/`. Each page draws the brief with
  mock-ups and diagrams, as a single `.html` file that opens in a browser.
- **Decided by** names the role and the date ("the owner, 2026-09-27") and what the answer covered, or says
  nothing yet. Most briefs have it.
- **Pieces** is added when a brief has several numbered pieces and they stand differently (some Shipped,
  some Designed): a short note per piece.
- **Built differently** is added when the code departs from what the brief designed, and says where
  (`identity-brief.md` has one).

When sentences in a brief say what the code does "today", a blockquote that starts **Reading note.**, after
the opening scene, gives the day they describe, as in `ux-brief.md`, `brain-brief.md` and
`passenger-brief.md`. The code moves, and the note tells the reader which sentences are older than the
status. `widgets-brief.md` has one under its `Checked today, and to verify before building` heading, and
[the design index](../design/README.md) mentions them.

Below the block come the opening scene and the rest of the spine.

## The spine

Briefs share a spine. Use the parts that apply. The first two are unheaded paragraphs after the status
block; the others have a heading, and the headings vary a little between briefs. The second column gives
the usual one and the variants in use, so a reader who looks for a part by what it does will find it. It
is not a list of every heading: each brief also has sections about its own subject, such as `The stone`,
`The switch` and `Coverage`.

| Part | Heading in the briefs | What it holds |
|---|---|---|
| Opening scene | none: the first paragraphs after the status block; `What it looks like` in `self-improvement-brief.md` | A short, concrete story of what the person sees and does, in present tense and second person. It is the test: if the design cannot be told as a scene, it is not clear yet. |
| What this changes | none: a paragraph after the scene | One paragraph on how the brief extends the ones before it and which decisions stay in force. |
| The rules | `The rules` (`Tips' own rules` in the tips brief) | Five to eight numbered rules, each a bold sentence and a reason. These are the part other pieces will link to. They must be checkable: a reader should be able to say whether a change breaks one. |
| Build these first | `Build these first` (`Build first` in the tips brief) | The pieces in build order. For each, in most briefs: **What you see**, **Why first**, **What changes** (the files and tools it touches), **Effort**. Add **Before building** for what to check first. `tips-and-desk-defaults-brief.md` gives numbered steps with an effort in brackets instead. |
| Where the pieces stand | `Where the pieces stand (<date>)` in `installed-os-brief.md`, `widgets-brief.md` and `self-improvement-brief.md`; `Where the rest stands` in `ux-brief.md`; a paragraph that starts `Where each piece stands, checked against ...` under `Build these first` in `ux-brief.md`, `brain-brief.md` and `passenger-brief.md`; **Where it stands** under each piece in `everyday-work-brief.md` | The status of each numbered piece on a stated day, read against `main`, in the words of the status block. |
| A day with it | `A day with Bombadil`, `A day with the brain`, `A day with the voice`, `A working day`, `A day at the desk`, `A day riding along`, `A day with it`, `Three days`, `A life with an installed Bombadil`, `A week, counted`; `The first boot, minute by minute` in `tips-and-desk-defaults-brief.md` | The design lived through a realistic day, or week or first boot, to find what the pieces miss. |
| Decisions | `Decisions I picked a default for`; `Defaults I picked` in `everyday-work-brief.md`; `Defaults picked` in `identity-brief.md`; `Decisions` in `tips-and-desk-defaults-brief.md`; `Decisions, confirmed by the owner on <date>` where the owner had answered; `Choices the owner made` in `everyday-work-brief.md`, with the owner's answer marked | Each open question as a bold question, the default, the reason, and the options not taken. Mark which the owner confirmed. |
| Everything else, by moment | `Everything else, by moment` (four briefs) | Smaller ideas grouped by when the person meets them, with enough detail to build. |
| Owned by other pieces of work | `Owned by other pieces of work` in `everyday-work-brief.md`, `installed-os-brief.md`, `passenger-brief.md` and `poor-man-switch-brief.md`; `Who owns what` in the tips brief | What another piece of work owns that this brief touches, so this brief does not decide it: one line per piece, saying what it owns and what this brief adds to it or asks of it. |
| Cut, and why | `Cut, and why` | What was considered and left out, with the reason. This is as valuable as the rules: it stops the next person proposing it again. |
| Gaps worth thinking about | `Gaps worth thinking about`, with `In the code today` and `Not designed yet` under it in `ux-brief.md`, `brain-brief.md`, `dev-brief.md` and `passenger-brief.md` | Problems in the code on the day the brief is written, and in the design, each concrete. Date them: the code moves. |
| What would make it feel like X instead | `What would make it feel like a chat window instead`, `... a mascot instead` and the like (five briefs, some without "feel like") | The anti-goal. The ways this design could slide into a chat window, a dashboard or a mascot, written as a list to check against. |
| Extends or changes the earlier briefs | `Extends or changes the earlier briefs` and its variants; `Changes to confirmed decisions` in the tips brief | Every earlier decision this brief amends, named, so the history stays honest. |
| Checked | `Checked today`; `Checked` in `everyday-work-brief.md` and `identity-brief.md`; `Checked today, and to verify before building` in `widgets-brief.md`; `Checked, and what still needs checking`; `To check before building` | The facts the design rests on, checked against sources with the date, and the ones still to check before building. `everyday-work-brief.md` and the tips brief also have a section of risks to check before building. |
| Open questions | `Open questions`, or `Questions, and the answers` once the owner has replied; `Four questions` in the tips brief; `Questions only you can answer` in `installed-os-brief.md`; `Risks and open questions` in `self-improvement-brief.md` | Only what the owner can answer. |

A brief links its drawn page from the `Design:` line of its status block, and two briefs also end with a
section named `The page`.

## How decisions are recorded

- A default is a decision with a reason and a way out. Write the default, why, and what you did not choose.
- When the owner answers, mark the decision confirmed and put the date next to it. Do not rewrite the
  decision text to match the answer; add what changed.
- In [the decision log](../decisions/README.md) each decision has one Status: Confirmed, Default, Open, Cut
  or Superseded by D-nnn.
- When a later brief changes an earlier decision, name it under "Extends or changes the earlier briefs" in
  the later brief and add a row to the decision log whose Decision starts with "Replaces D-nnn:". The
  earlier row's Status becomes "Superseded by D-nnn", and the row stays. A decision that only adds to,
  narrows or changes part of an earlier one is noted in the earlier row's Why. Do not edit the old brief's
  decision silently.
- Facts about upstream tools (a Claude Code flag, a Hyprland behaviour) are written with the version and the
  date they were checked, because they move. Anything not checked is marked as such.

## Keeping the principles

Before a brief is done, read it against [the principles](../principles.md#checking-a-change-against-the-principles).
The rules section of a brief should either restate a principle for the surface it adds or add a rule the
principles do not have. If it contradicts a principle, say so in "Extends or changes" and say why; the
principles page is then updated with the brief, not after it, and a rule it adds gets a line under
"[Where each rule was decided](../principles.md#where-each-rule-was-decided)".

## What a brief must not contain

The same things nothing else in the repository may contain: personal details, machine names, links to
private conversations and quotes from them. See [Documenting your piece](documenting.md#what-must-never-be-committed).
A design pass is attributed to its role ("the owner confirmed on this date"), not to a person.

## From brief to code

A brief you add comes with these, in the same pull request:

- its status block, with the status Designed until something is built;
- its drawn page in [`docs/design/pages/`](../design/pages/), linked from the `Design:` line;
- its place in the reading order and its row in the table of [the design index](../design/README.md);
- a status for each piece in [the roadmap](../roadmap.md);
- rows in [the decision log](../decisions/README.md) for what it decides, in the file of the area that owns
  each decision.

A brief is built piece by piece. When a piece ships, add its page to [`docs/architecture/`](../architecture/),
set the status in the roadmap and in the design index, update the brief's status block (Status, Pieces,
Code and Verified) so the next reader can tell which parts of it are built, and set the Built column of the
decisions it holds.
