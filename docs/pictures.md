# Pictures of the machine

How the pictures above the pill are made, and what running them on a real machine taught us.
The design is in [`design/passenger-brief.md`](design/passenger-brief.md) (piece 2) and the short
version is in [`ARCHITECTURE.md`](ARCHITECTURE.md#why-lines-and-pictures). This page is for someone
who wants to change a picture, add one, or understand why a reading is the way it is.

## Where a picture comes from

Two sources draw the same kind of card (`cards.py`: a chain, layers, a compare or a timeline, at
most 12 boxes):

- the agent, with `show_card`, for anything it wants to explain;
- the machine itself, with `system_map` (or a plain question the launcher knows, such as
  "how am I connected", with no model call). `src/bombadil/sysmap.py` reads the real tools and
  builds the card from what they print.

| Picture | Read from | Notes |
| --- | --- | --- |
| network | `ip -j route`, `nmcli -t`, a request to the AI's own host | the signal and the latency move by themselves, so they never count as a change |
| boot | `systemd-analyze critical-chain`, `time`, `blame` | slow by nature, so it gets 4 s instead of half a second; a chain under four steps gives way to the slowest units |
| one service | `systemctl show`, `pacman -Qo` | what it requires and wants, each with its state |
| disks | `lsblk -J`, `findmnt -J` | disks under 1 MB (a floppy drive, an empty card slot) are not drawn |
| sound | `pw-dump` | which app plays to which speaker, and how loud |
| screens | `hyprctl monitors -j` | left to right, the focused one lit |

Every tool is run in the C locale with colour off (`_ENV` in `sysmap.py`), so the output has one
shape on every machine and in every language.

## Reading the machine: what a real VM taught us

Each of these passed its tests against output we had written down by hand, and was wrong on a real
system. The fixtures in `tests/test_sysmap.py` now carry the real shape.

- **The boot tree is ASCII in the C locale.** `systemd-analyze critical-chain` draws its tree with
  `` `- ``, `|-` and `| ` there, and with box-drawing characters in a UTF-8 locale. The reader
  (`_CHAIN_RE`) takes both, and a unit whose name starts with a `-` (`-.mount`) keeps it. Reading
  only the Unicode marks left just the first line, so the card showed one row, "10.1 s
  graphical.target", and said nothing took a second. To see what your machine prints:
  `LC_ALL=C systemd-analyze critical-chain --no-pager | cat -A`.
- **A PipeWire level is in two places.** A node has a master `volume` and one level per channel
  (`channelVolumes`). A hardware speaker keeps its level in the channels and leaves `volume` at
  1.0; a software one does the other thing. What plays is the two together, and both are linear:
  the percent on a slider is the cube root. Reading `volume` alone said "100%" at any volume, so
  the agent setting 65% left two identical snapshots and no receipt.
- **Unit names are case-sensitive and people do not type capitals.** `NetworkManager.service` is
  not `networkmanager.service`. `sysmap.find_unit` tries the name as typed, then looks for it among
  the unit files and the loaded units ignoring case, and the picture is drawn under the machine's
  own spelling. The launcher keeps the capitals of what was typed ("What does NetworkManager need")
  so a phrase with a name in it is matched on the text as written, not on the lowercased copy the
  other phrases use. `capture_service` does the same, so an agent that asks for "networkmanager"
  gets the picture too.
- **The lookup has half a second, and `systemctl list-unit-files` takes two.** On a small VM it
  took 1.9 to 2.2 s (3 to 25 s with another VM running), while `systemctl list-units --all` took
  under 0.15 s. The first ask after a reboot waited for the slow one, gave up, and went to the model
  (a restore point and a quota turn for a picture that is free). `find_unit` now asks `systemctl show`
  and `list-units --all` side by side, which finds every unit that is loaded. The names of all the
  unit files come from a list `agentd` reads in the background when it starts and reads again
  (in the background) when it is ten minutes old, so a unit that is only a file on disk is known
  from the first ask on and never waited for.
- **A quiet boot has almost no chain.** With the console quiet, graphical.target is reached through
  three targets that all finish together, and the critical chain is three lines: three targets at
  6.7 s and "nothing holds the boot up", which is true and says nothing. When the chain has fewer
  than four steps and `systemd-analyze blame` knows more units, the card shows the slowest units
  instead (longest first, the duration where the start time was, the longest in amber from a
  second). The three commands run side by side, so it costs no more time.
- **`blame` is mostly waiting.** On the first version of that fallback the twelve rows were all
  `.device` units (`dev-vda2.device` and friends), about 5 s each: a `.device` unit is "waiting for
  the hardware to show up", not work, and the boot says nothing about it. The say line named one of
  them as the thing that takes longest. `sysmap._worth_blaming` leaves out `.device` units and
  `initrd-*` units (the early boot, before the real system starts), so the rows are what the machine
  did: on the test VM `systemd-tmpfiles-setup-dev-early` 2.0 s, `systemd-nsresourced` 1.7 s,
  `NetworkManager` 448 ms. When nothing but those is left the chain is kept as it is.
- **Systemd escapes names.** `/dev/disk/by-uuid/1234` is `dev-disk-by\x2duuid-1234` as a unit name,
  and a boot step for a disk check was labelled `systemd-fsck@dev-disk-by\x2duuid-1234...`. The
  pictures say names the way `systemd-escape -u` reads them: `/dev/disk/by-uuid/1234 device`,
  `systemd-fsck@/dev/disk/by-uuid/1234`. A long instance keeps its end, since the device is what
  tells two of them apart. The box still opens the real unit name.

## Receipts

After a turn that touched a part of the machine, `agentd` compares that part before the turn and
after it (`sysmap.snapshot`, `sysmap.receipt`) and shows what changed, the old value dim and the new
one in orange. Only the parts the turn's steps touched are compared, so a Wi-Fi drop is never
blamed on the agent, and a fact marked `volatile` (a signal, a latency, used space) never counts.

A fact marked `alone` counts as a change only when nothing else changed. A service carries one: the
time it came up. A restart that brings the service back exactly as it was changes nothing else, and
used to leave no receipt at all; now the card shows the start time before and after and says it was
restarted. Next to a real change (stopped, started, failed) the start time is left out, so the
receipt for stopping a service still shows the state and nothing else.

## On the screen

- **Hyprland slides the bar when it is resized, so that animation is off.** The bar is a layer
  surface as tall as what is in it, so it is resized whenever a picture appears, gains a box or
  goes. Hyprland's default `layers` animation moves the surface to its new place each time, and the
  pill dipped (by up to about 55 px) and swung back with an overshoot for about half a second, at every
  box added, at the final layout and when the card went. Measured on a real VM at the pill's bottom
  edge about ten times a second, with the animation off it stays put in 739 of 749 samples (the
  rest are one-frame blips at the resize). `hyprland.lua` in the skeleton has
  `hl.animation({ leaf = "layers", enabled = false })`. **An installed system keeps its own
  copy of `hyprland.lua`** (`~/.config/hypr/hyprland.lua`), which does not change when the skeleton
  does: add that line to it.
  What is left (measured on the real VM with the line in place): a card that appears in one size
  step still moves the pill for a sample or two (3 to 5 samples off rest, the longest run 1 or 2,
  22 to 31 px at worst; with the animation on, 6 samples, a run of 4, 36 px). The cause is not
  known. Making the bar's window a fixed height, so it is never resized, would end it, at the price
  of compositing a large transparent surface all the time, which costs CPU under software
  rendering. It is left as it is until that cost has been measured.
- **The card is placed once.** A card whose height grew over a few frames resized the window every
  frame, one blip each. `shell/CardHost.qml` takes its height at once and eases in by fading and
  rising a few pixels inside that space; it hands the space back after it has faded out. (This did
  not cure the dip above, which was the animation; it only keeps a card to one resize.)
- **A picture you asked for keeps its line, so its × stays where it is.** The card sits above the
  status line in a bottom-anchored column, so whenever the line grew, shrank or went, the card (and
  its ×) moved by the line's height; on the real VM the × was at y 389, then 307, then 354 in three
  tries, and clicks missed. `PillState.pictureStays` is true while a picture that is not a receipt
  and not half-drawn is up, and the line's fade timer (`StatusLine.qml`) waits while it is. The line
  goes with the picture, when it is put away with Esc or the ×. A receipt still fades with its
  closing line, as before (the two are read together), and so does a line with no picture. The
  line's timer sleeps while a picture holds the line (it has no seconds to count and nothing to
  fade), so a picture left up costs no wake-ups. The card's top is still its own height above the
  line, so two different pictures sit at different heights; the same picture sits at the same place
  each time it is drawn.
- **A full-screen window puts the picture away.** In a full-screen window the bar is a dot and a
  clock in a 360 px capsule, and a card hanging over it landed in the middle of whatever the window
  showed (the Brain's list, for one). `CardHost.suppressed` follows `win.capsule` in `shell.qml`: the
  card fades out and gives its space back, and it comes back, not dismissed, when the window goes.
- **A card being drawn keeps the layout it will end with.** The boxes of a streamed chain arrive
  before its links, and the links carry the labels that decide how wide the gaps are, so a finished
  chain used to re-lay itself out from one row into two with a visible jump. `Diagram.qml` now
  leaves room for labels of about 14 characters while a chain has none yet.
- **A before and after sits together.** The two columns are at most 230 px wide with 76 px between
  them for the arrow, centred, instead of one at each edge of a wide card.
- **The agent's own words on the line start at a word.** While the agent works, the line shows the
  newest end of what it is saying. `shell/StatusLine.qml` cuts it at a word with a `…` before it;
  `Text.ElideLeft` alone cut the first word in half.

## Testing

`tests/test_sysmap.py` (readers and receipts, against recorded output), `tests/test_launcher.py`
(the phrases), `tests/test_diagram_qml.py` and `tests/test_pill_qml.py` (the layout and the card
host, in an offscreen window), and the headless desktop test (`tests/desktop/run.sh`) which draws a
streamed card in the real bar. Set `BOMBADIL_SCREENS=<dir>` to save pictures of the QML tests.
`iso/airootfs/usr/local/bin/bombadil-smoke` prints how long each capture takes on a booted machine.
