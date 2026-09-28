# How the brief's pictures are drawn

Every brief is a page in a series Daniel already reads: How Bombadil Feels, Building on Bombadil,
Bombadil's Brain, The Bombadil Desk, and the briefs after them. Pictures must look like those pages' pictures and like the real kit.
Reference pages: The Bombadil Desk (inline-SVG mockups, https://claude.ai/artifact/97xCqVJ3hAEeLw1mdHnqKV)
and Building on Bombadil (small CSS mocks, https://claude.ai/artifact/5kd2NrfDp8xUDoWqrLPH5K); read them with
the Artifact tool. Real kit renders to match: `bombadil-app check <app> --screenshot out.png`.

## Page tokens (the paper around the pictures; light and dark)
brief.css already has the :root and dark blocks; inline it as it is. Fonts: Hanken Grotesk (page),
IBM Plex Mono (labels, code), Inter (inside OS pictures, because the OS uses Inter).

## OS tokens (inside every picture; the OS is always dark) = share/qml/Bombadil/Theme.qml
bg #101214 (window/screen) · panel #1a1d21 (cards, panels, bar) · raised #22262b (hover, selected rows,
inputs, meter tracks) · overlay/border #2a2f36 · borderStrong #3a414a · sunken #0c0e10 (editors, code)
ink #e6e8eb · muted #8b939c · faint #5c636b
accent #d97757 (hover #e38a6c) · accentSoft = #d97757 at 20% (#2b2320 on bg) · good #5fb36b · warn #e0a93b
(amber text on dark: #e8c38d) · bad #d05555 (the briefs draw red as #e06a6a) · info/sessions blue #5b9bd5
Chart series in order: #3987e5 #d95926 #199e70 #c98500 #d55181 #008300 #9085e9 #e66767; grid #262a30;
axis #383e46.
Shape: radius 12 windows/cards, 8 controls/rows; pad 16; gap 12; controls 36 px tall; rows 44 px.
Type (px at 1:1): body 14, caption 12, heading 17, title 22, display 34; mono 13.
Who-colours (the Brain's, used on every dot and meter): white #e6e8eb you, orange #d97757 the machine,
blue #5b9bd5 coding sessions.

## The shell's real sizes
The pill: 52 px tall, up to 900 px wide, radius 26, fill #1a1d21, stroke #2a2f36 (orange #d97757 1.5 px
while a turn runs), a 10 px status dot at left (green #5fb36b idle, orange pulsing while busy), placeholder
"Ask anything" in muted 16 px, clock at right in muted 13 px. The line above the pill: 58 px card, 15 px
text, an amber 3 px edge + the exact command in amber mono for system steps. Rails 300 px wide; strips
28 px tall; answer cards rise in the column above the pill.
Apps open floating and centred in their own special workspace, sized to the usable area (a 760×560 manager,
up to 1200×760 for a dashboard).

## A kit app window, as the testers' screenshots show it
Window fill #101214, radius 12, 1 px #2a2f36 border. Header row at top-left, 16 px in: a 28 px rounded
(radius 8) tile filled #2b2320 with the app's icon stroked in #d97757; the title 17 px semibold #e6e8eb;
a muted 14 px subtitle after it; actions (SearchField 36 px tall, radius 8, fill #1a1d21, border #2a2f36,
magnifier icon and muted placeholder; IconButtons 28 px) at top-right. Content below in Panels: fill
#1a1d21, radius 12, border #2a2f36, 16 px padding, title 14 px semibold, caption 12 px muted. Stat: label
12 px muted, value 22 px semibold. Rows 44 px with 1 px #2a2f36 dividers, selected row #22262b. The one
primary button: fill #d97757, white 14 px semibold text, radius 8, 36 px tall; other buttons fill #22262b
with #2a2f36 border. Badges: 20 px tall pills, 12 px text, tinted fill (tone at ~18%) with the tone as text.
Toasts: bottom-centre of the window, #22262b pill with a tone dot, text and an optional action in accent.
Icons: Lucide style, 1.75–2 px round strokes on a 24 grid, drawn in #8b939c or the tone colour.

## SVG rules (every picture)
- `<svg class="mock-svg" viewBox="0 0 W H" role="img" aria-label="(what it shows, one sentence)"
  font-family="Inter, 'Hanken Grotesk', system-ui, sans-serif">`, first child a full-size rect rx 12 fill
  #101214. Whole-screen pictures: 1920×1080 or 1280×720 with the Desk's wallpaper defs
  (two radial gradients #1e2a34 top-left and #34241d bottom-right). Component pictures: draw at 1:1 kit
  size inside a viewBox about 640–760 wide, so text is 11–15 px at 1:1 and legible when the figure is
  ~420 px wide on a phone.
- Every shape has an explicit fill (use fill="none" for strokes). Monospace text:
  font-family="'IBM Plex Mono', ui-monospace, monospace".
- Text must fit its box. Estimate Inter width as 0.56 × font-size per character (0.6 for mono); ellipsize
  with "…" before it would cross an edge; right-align numbers with text-anchor="end". Leave 12 px inside
  every card edge. Nothing overlaps unless it is meant to.
- Unique ids inside each SVG (prefix with the figure's name, e.g. id="k3-wp1"), because many SVGs share one
  page.
- Motion only with the existing `.pulse` class (a ring that grows and fades), and only on something live.
- Real content only: Daniel's world (Bombadil, Latchkey, ~/Downloads, AWS, his Music, recipes), plausible
  file names, sizes, times; never lorem ipsum. Mark example data as example where it matters.
- Captions (<figcaption>) start with a bold sentence naming the moment, then one or two plain sentences.

## Small CSS mocks (optional, for one-line moments)
The .mock / .m-pill / .m-line / .m-chip / .m-card classes in brief.css draw a pill with a
line above it in about ten lines of HTML. Use them for moments that are only a line and a pill.

## Words
Write like the earlier briefs: present tense, second person, concrete ("You type <q class="ui">clean up my
downloads</q>. Within a second a window titled Downloads rises..."). Short plain sentences. Quote what the
user types or reads with <q class="ui">…</q>. No em-dash asides, no "not X but Y", no stock phrases.
