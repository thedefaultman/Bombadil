# Writing a design brief

Anything the person can see or feel in Bombadil starts as a design brief: a short document that says what
the experience is, which rules it keeps, what to build first and what was decided, before any code. The
briefs in [`docs/design/`](../design/README.md) are the working examples. This page describes their shape
so a new one reads like the others and can be built from without asking its author.

Write a brief when you add a new surface (a widget, a card kind, a screen), change how something feels,
or take a decision that other pieces will depend on. A bug fix or a refactor does not need one.

## The spine

Briefs share a spine. Use the sections that apply and keep their names, so a reader can find the same
thing in the same place in every brief.

| Section | What it holds |
|---|---|
| Opening scene | A short, concrete story of what the person sees and does, in present tense and second person. It is the test: if the design cannot be told as a scene, it is not clear yet. |
| What this changes | One paragraph on how the brief extends the ones before it and which decisions stay in force. |
| The rules | Five to eight numbered rules, each a bold sentence and a reason. These are the part other pieces will link to. They must be checkable: a reader should be able to say whether a change breaks one. |
| Build these first | The pieces in build order. For each: **What you see**, **Why first**, **What changes** (the files and tools it touches), **Effort**. |
| A day with it | The design lived through a realistic day, to find what the pieces miss. |
| Decisions I picked a default for | Each open question as a bold question, the default, the reason, and the options not taken. Mark which the owner confirmed. |
| Everything else, by moment | Smaller ideas grouped by when the person meets them, with enough detail to build. |
| Cut, and why | What was considered and left out, with the reason. This is as valuable as the rules: it stops the next person proposing it again. |
| Gaps worth thinking about | Problems in the code today and in the design, each concrete. |
| What would make it feel like X instead | The anti-goal. The ways this design could slide into a chat window, a dashboard or a mascot, written as a list to check against. |
| Extends or changes the earlier briefs | Every earlier decision this brief amends, named, so the history stays honest. |
| Checked | The facts the design rests on, checked against sources with the date, and the ones still to check before building. |
| Open questions | Only what the owner can answer. |

A brief ends with a line pointing to its page in [`docs/design/pages/`](../design/pages/), and each
page is the brief drawn: the same words with mock-ups and diagrams.

## How decisions are recorded

- A default is a decision with a reason and a way out. Write the default, why, and what you did not choose.
- When the owner answers, mark the decision confirmed and put the date next to it. Do not rewrite the
  decision text to match the answer; add what changed.
- When a later brief changes an earlier decision, name it under "Extends or changes the earlier briefs" in the
  new brief and add a row to [the decision log](../decisions.md). Do not edit the old brief's decision silently.
- Facts about upstream tools (a Claude Code flag, a Hyprland behaviour) are written with the version and the
  date they were checked, because they move. Anything not checked is marked as such.

## Keeping the principles

Before a brief is done, read it against [the principles](../principles.md#checking-a-change-against-the-principles).
The rules section of a brief should either restate a principle for the new surface or add a rule the
principles do not have. If it contradicts a principle, say so in "Extends or changes" and say why; the
principles page is then updated with the brief, not after it.

## What a brief must not contain

The same things nothing else in the repository may contain: personal details, machine names, links to
private conversations and quotes from them. See [Documenting your piece](documenting.md#what-must-never-be-committed).
A design pass is attributed to its role ("the owner confirmed on this date"), not to a person.

## From brief to code

A brief is built piece by piece. When a piece ships, add its page to [`docs/architecture/`](../architecture/),
set the status in [the roadmap](../roadmap.md) and put the status block at the top of the brief, so the next
reader can tell which parts of it are built.
