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
| boot | `systemd-analyze critical-chain`, `systemd-analyze time` | slow by nature, so it gets 4 s instead of half a second |
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

- **The card is placed once.** The bar's window is as tall as what is in it, so a card whose height
  grew over a few frames resized the window every frame, and on the compositor the pill jumped
  while it caught up. `shell/CardHost.qml` takes its height at once and eases in by fading and
  rising a few pixels inside that space; it hands the space back after it has faded out.
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
