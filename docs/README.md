# Bombadil docs

The project's documents, next to its code.

- `CHRONICLE.md`: who asked what, what was decided, what was built and merged, and what is open. Start here.
- `ARCHITECTURE.md`: how the pieces of the first milestone fit together.
- `DESK.md`: the desk's widgets, rails and strips: how it is built, what it says to agentd, how to add a widget.
- `pictures.md`: how the pictures above the pill read the machine, and what a real VM taught us.
- `brand/`: the mark (a stone with a b cut through it): what it is, its colours and faces, how Stone.qml draws it, and what a real machine taught us. Holds the lockups, tile, avatar and social preview.
- `design/`: the design briefs. Every decision in them was confirmed by Daniel.
  - `foundation-choices.md`: base distro, compositor, agent runtime, app toolkit.
  - `ux-brief.md`: how Bombadil feels (the pill, the status line, undo).
  - `dev-brief.md`: building other projects on Bombadil (named coding sessions, dots, fences).
  - `brain-brief.md`: the native index of files, projects and turns, and its Focus, Map and Time views.
  - `widgets-brief.md`: the desk and its widgets.
  - `passenger-brief.md`: "Riding with Bombadil", the user as a passenger who gets things explained.
  - `voice-brief.md`: the one-time setup card, the voices and the welcome lines.
  - `self-improvement-brief.md`: how Bombadil notices repeated requests and tends to itself.
  - `identity-brief.md`: the mark: what was asked, the three options drawn in round two, why the riding b, the orange, where the mark goes, and what was cut. The options and every state are on `pages/bombadil-mark.html`.
  - `design-system.md`: the look written down: colour, type, shape, space, motion, icons and the pill's states.
  - `rust-question.md`: whether Bombadil should use Rust, with measurements.
  - `poor-man-switch-brief.md`: what Bombadil does when the AI runs out of plan or money: a resting state that keeps everything already made working, holds asks until the reset, finds things on the computer, and has a switch. Questions answered 1a 2a 3a.
  - `boot-and-wallpaper.md`: every screen from power-on to the desk in the design system's colours: the quiet console, the wallpaper under the desk, and why the desk used to be black.
- `design/pages/`: the published pages of those briefs as standalone HTML, with the drawn mock-ups.
  Open them in a browser. Fonts load from Google Fonts when you are online and fall back to system
  fonts without a connection. They are copies of what the pages showed on 2026-09-30.
- `review/`: the first-milestone review findings, hand-off notes, boot-test logs and an unapplied lint patch.
- `screens/`: screenshots from the first boot, the pill walkthrough, the details drawer fix and the boot and wallpaper.

The chat transcripts and the project memory are not in this repository. They name Daniel's machine and
his conversation, so they live in a private bundle outside it.
