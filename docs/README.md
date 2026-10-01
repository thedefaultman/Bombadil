# Bombadil docs

The project's documents, next to its code.

- `CHRONICLE.md`: who asked what, what was decided, what was built and merged, and what is open. Start here.
- `ARCHITECTURE.md`: how the pieces of the first milestone fit together, including what the machine does when the AI rests.
- `design/`: the design briefs. Every decision in them was confirmed by the owner.
  - `foundation-choices.md`: base distro, compositor, agent runtime, app toolkit.
  - `ux-brief.md`: how Bombadil feels (the pill, the status line, undo).
  - `dev-brief.md`: building other projects on Bombadil (named coding sessions, dots, fences).
  - `brain-brief.md`: the native index of files, projects and turns, and its Focus, Map and Time views.
  - `widgets-brief.md`: the desk and its widgets.
  - `passenger-brief.md`: "Riding with Bombadil", the user as a passenger who gets things explained.
  - `voice-brief.md`: the one-time setup card, the voices and the welcome lines.
  - `self-improvement-brief.md`: how Bombadil notices repeated requests and tends to itself.
  - `rust-question.md`: whether Bombadil should use Rust, with measurements.
  - `poor-man-switch-brief.md`: what the machine does when Claude or Codex runs out of plan or spending, or
    is paused by hand: the resting state, the AI card with its switch, and the finder that answers a sentence
    from the computer. Piece 1, Resting, is built; piece 2, the finder, comes next; the rest is later.
- `design/pages/`: the published pages of those briefs as standalone HTML, with the drawn mock-ups.
  Open them in a browser. Fonts load from Google Fonts when you are online and fall back to system
  fonts without a connection. They are copies of what the pages showed on 2026-09-30, and 2026-10-01 for
  `the-poor-man-switch.html` (the page of `poor-man-switch-brief.md`, with a switcher for the resting lines).
- `review/`: the first-milestone review findings, hand-off notes, boot-test logs and an unapplied lint patch.
- `screens/`: screenshots from the first boot, the pill walkthrough and the details drawer fix.

The chat transcripts and the project memory are not in this repository. They name the owner's machine and
conversation, so they live in a private bundle outside it.
