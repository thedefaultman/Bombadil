---
name: bombadil-brief
description: Daniel's format for Bombadil design work. Use whenever he asks for ideas and how they would look ("give me some ideas and how would that look like", "run a user experience session", "how should X work or feel", "options for X", "design X"), for any part of Bombadil. The answer is a published page in the experience-brief series: drawn mockups in the OS's own look, the ideas, what to build first, decisions with defaults, what was cut, and what was checked. Never answer such a request with chat text alone.
---

# Bombadil experience briefs

When Daniel asks for ideas and how they would look, he wants a page he can see, in the same series as
the briefs he has already confirmed. Chat text alone does not answer it.

The series so far. The first four are confirmed by Daniel and stay in force; 4 and 5 wait for his confirmation:

| Brief | Artifact |
|---|---|
| 1 · How Bombadil Feels: the pill, cards, undo, apps (29 decisions) | https://claude.ai/artifact/7am6RMWd6ekqtquenUGt5B |
| 2 · Building on Bombadil: coding sessions (19 decisions) | https://claude.ai/artifact/5kd2NrfDp8xUDoWqrLPH5K |
| 3 · Bombadil's Brain: the index, Map, Focus, Time (17 decisions) | https://claude.ai/artifact/5Ysyv6FdPDpMhX8uCWq57f |
| The Bombadil Desk: widgets (15 decisions) | https://claude.ai/artifact/97xCqVJ3hAEeLw1mdHnqKV |
| 4 · The Bombadil Kit: making apps and graphics (17 decisions, defaults not yet confirmed) | https://claude.ai/artifact/VxLBezm1vDbCyHPzq7VNUM |
| 5 · Bombadil at Work: everyday work for PMs, office workers, students (18 decisions, defaults not yet confirmed) | https://claude.ai/artifact/U3FibR6CqdLKqkDbC4WQWL |

Read the ones your subject touches (Artifact tool, `action: "read"`) before designing. A new brief
never contradicts them silently: it names every decision it extends or changes.

## Standing rules from Daniel
- The flow must be simple but complete. Check every idea against both words.
- He is a passenger: the AI drives, and he gets things explained visually.
- Findings and designs are pictures first. Every idea worth building has a drawing.

## Method (the one the earlier briefs used)
1. **Evidence first.** Where the thing exists, run it: testers who play the agent or the user and build
   or walk through real cases, with numbers (time, steps, failures). Where it does not, persona
   walkthroughs of a full day against the confirmed briefs and the repo. Add researchers for facts
   (versions, package sizes, licences, what works on Arch and Hyprland today).
2. **Designers with angles.** Five designers, one angle each, write ideas as moments: what you type,
   what appears, how fast, what it looks like.
3. **A fact checker per designer.** Checks every claim against the code, the research and the confirmed
   briefs, and corrects or drops what fails.
4. **Three judges.** Daniel as owner, a first-time user, and the engineer who builds it next. They score
   simplicity, capability and feasibility, and pick what to build first.
5. **Write and draw the page**, then look at it rendered once (desktop and phone width) and fix what
   is clipped or overlapping.
When a workflow is allowed (Daniel opted into multi-agent work), run steps 1 to 4 as workflows and
stay in the loop between them.

## The page
Author HTML with `brief.css` (next to this file) inlined in a `<style>` block. It is the series' own
CSS: page tokens for light and dark, and the OS tokens, which are always dark. Follow `drawing.md` for
every picture.

Sections, in this order (leave out one that has nothing true to say):
1. **Hero.** Eyebrow `Bombadil · experience brief N · <subject> · <date>`, the h1 (the page's name, two
   to four words), a lede that walks through one moment in second person and present tense, a large
   drawn screen (inline SVG, 1920×1080), a note on which briefs it sits on, `.hero-meta` counts (ideas,
   to build first, decisions, mock-ups, facts checked), and a `.toc`.
2. **The rules** (`ol.rules`): six to eight rules every idea below follows.
3. **What the evidence showed**: real screenshots or walkthrough findings, numbers, and one chart if the
   numbers are the point.
4. **Build these first** (`.pieces` of `.piece`): a number, a title, an effort chip, a picture beside the
   text, then `p.see` (the moment), and a `dl` with Why first, What changes, and Before building.
5. **A day**: a `.day` timeline that shows how it feels once built.
6. **Decisions** (`details.dec`): each fork with `Default:`, why, and the other options, plus
   Expand/Collapse buttons.
7. **Everything else, by moment** (`.group`); **Cut, and why** (`ul.cut`); **Gaps**; **Pitfalls**
   (`ul.pitfalls`, what would make it feel wrong).
8. **Extends or changes the earlier briefs**: every confirmed decision touched, and exactly how.
9. **Checked today, and to verify** (`.facts`): facts with sources, then what is unverified.
10. **Footer**: the date, the branches and docs it was written against, and how it was made.

Words: write like the earlier briefs. Use short, plain sentences in present tense and second person,
and quote what people type or read as `<q class="ui">…</q>`. Name the real tools and real numbers.
Leave out em-dash asides, "not X but Y", hype and stock phrases.

## Publishing
Publish with the Artifact tool as a private page, with icon `layout`. The description is one sentence
that says which brief it is and what it covers. Give Daniel the link and the decisions that need his
confirmation, and ask him to confirm or change the defaults. Once he confirms, update the page's note and
its decisions heading to say so, as the earlier briefs do.
