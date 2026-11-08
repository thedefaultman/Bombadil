# Decision log

This page lists the decisions in Bombadil's design briefs, one row each, grouped into thirteen areas, one file each, and numbered from D-001. Each row gives what was decided, the reason the brief gives, whether the person confirmed it, the section of the brief that holds the full text and how far it is built. A decision that a later brief replaced stays in the log, marked as superseded and pointing to the row that replaced it, so the history can be followed. The briefs in [`design/`](../design/README.md) hold the reasoning in full, the [roadmap](../roadmap.md) says where each piece stands today, and the [glossary](../glossary.md) defines the terms used here.

## The areas

| Area | Decisions | IDs |
| --- | --- | --- |
| [Foundation and stack](foundation-and-stack.md) | 90 | D-001 to D-090 |
| [Experience and interaction](experience-and-interaction.md) | 185 | D-091 to D-275 |
| [Agent, providers and sessions](agent-providers-and-sessions.md) | 226 | D-276 to D-501 |
| [Shell, desk and widgets](shell-desk-and-widgets.md) | 190 | D-502 to D-691 |
| [Apps and the kit](apps-and-the-kit.md) | 69 | D-692 to D-760 |
| [Pictures, cards and explanations](pictures-cards-and-explanations.md) | 54 | D-761 to D-814 |
| [Memory and the brain](memory-and-the-brain.md) | 94 | D-815 to D-907, D-1761 |
| [Voice and words](voice-and-words.md) | 125 | D-908 to D-1032 |
| [Identity and design system](identity-and-design-system.md) | 86 | D-1033 to D-1111, D-1762 to D-1768 |
| [Installed OS and updates](installed-os-and-updates.md) | 173 | D-1112 to D-1283, D-1769 |
| [Everyday work](everyday-work.md) | 135 | D-1284 to D-1418 |
| [Limits and recovery](limits-and-recovery.md) | 178 | D-1419 to D-1596 |
| [Self-improvement](self-improvement.md) | 164 | D-1597 to D-1760 |

A decision belongs to the area of the brief that owns it. IDs run across the whole log, so an area that gained rows later lists more than one range. The file for an ID is the one in this table, or the one the link from the row that mentions it points to.

## How to read it

Every table has the same seven columns.

| Column | Holds |
| --- | --- |
| ID | `D-001` and up. An ID never changes once committed, and it is never reused. |
| Date | The day the decision was made in its brief or, for a confirmed decision, the day the person confirmed it; "undated" where the brief gives no date. Rows run by date within an area, undated rows last. |
| Decision | The decision in the brief's own words. A row that replaced an earlier one starts with "Replaces D-nnn:". |
| Why | The reason the brief gives. A note that reads "Extended by", "Narrowed by" or "Changed in part by" names a later row that adds to, narrows or changes part of this one without replacing it. |
| Status | One of the five below. |
| Source | The section of the brief that holds the decision. Where several briefs state the same decision it has one row that lists every section. |
| Built | How far the decision is built, with a link to the architecture page or file that holds it. |

| Status | Means |
| --- | --- |
| Confirmed | The person answered the question and said yes. |
| Default | The brief picked it with a reason and a way out; the person has not ruled on it. |
| Open | The brief leaves the question for the person to answer. |
| Cut | Considered and left out; the Why says why. |
| Superseded by D-nnn | A later decision replaced this one. The row stays. |

The words in the Built column are the roadmap's, and each links to it.

| Built | Means |
| --- | --- |
| [Shipped](../roadmap.md) | On `main`, with a test or a documented check. |
| [Partly shipped](../roadmap.md) | Some of what the decision names is on `main`. |
| [In progress](../roadmap.md) | On a branch or in an open pull request, not on `main`. |
| [Designed](../roadmap.md) | Decided, with no code. |
| n/a | Nothing to build on its own: a cut idea, an open question or a rule that applies to everything. |

Built is the state the brief reported for that decision, read against the `main` this documentation was verified at and the unmerged branches on the remote on 2026-10-01. A brief can lag the code, so work that was on a branch when a brief was written may be on `main` now: the first two pieces of the poor man switch were merged into `main` on 2026-10-01, after that check, so their rows read In progress or Designed until the roadmap and the architecture page are verified against the merge. The [roadmap](../roadmap.md) is the live record, and where the two differ it is the one to believe.

## How to add a decision

1. Write the decision in the brief that owns it, under a heading that will not change, with its reason and what was not chosen. When it changes an earlier decision, say so under "Extends or changes the earlier briefs" in the new brief.
2. Add a row at the foot of the table for its area, with the next free ID: the highest number in this file plus one.
3. Fill in the date, the decision in the brief's words, the reason, the status and a link to the brief's section.
4. If it replaces an earlier decision, change that row's Status to "Superseded by D-nnn" and begin the new Decision with "Replaces D-nnn:". If it only adds to, narrows or changes part of an earlier one, add that to the earlier row's Why.
5. Set Built from the [roadmap](../roadmap.md), link the page or file that holds it, and run `python3 docs/tools/check_docs.py docs/decisions/<area>.md`.

An ID never changes once it is committed. A row is not deleted or renumbered when its decision is replaced; its Status changes and the row stays.
