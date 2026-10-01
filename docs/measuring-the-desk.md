# Measuring the pill, the stone and the cards from outside the VM

Some faults only exist on the real screen. The bar's own state can be right while the picture is
wrong, and a test that reads QML cannot see what the compositor does with a window. Two of those
were found this way:

- The stone stayed orange at rest after the bar started. A log of its face and fill said `rest` and
  green, because that is what the QML computed; the first frame had been painted before the colour
  changed and the change never reached the screen.
- The pill jumped by up to 50 px every time a card changed size. Hyprland animates a layer surface
  when it resizes, and nothing in the bar's code does.

`scripts/vm-tools/` has two small tools for this and two scenarios for the scripted API. They read
the VM's screen through QEMU, so they need nothing inside the guest and nothing from the host
except python3 (no imaging library). The rest of that folder is described in
[`scripts/vm-tools/README.md`](../scripts/vm-tools/README.md).

## What you need

- A VM running with a QMP socket. `scripts/run-vm.sh --installed` makes one at `out/vm/qmp.sock`;
  `QMP=/path/to/other.sock` points a tool at another VM.
- A 1600x900 screen (the default). The coordinates below are for it: the stone is at about
  x 368 to 384 and, at rest, y 853 to 870.
- For the scenarios, a guest whose agentd talks to the scripted API (below).

## `stonecrop`: the stone at real size, as frames

```sh
scripts/vm-tools/stonecrop out.png 8 0.4          # 8 frames, 0.4 s apart, the stone at 8x
scripts/vm-tools/stonecrop out.png 12 0.2 358 846 32 32 6   # a wider area, to see the glow
```

It takes N screendumps INTERVAL seconds apart, crops each to `X Y W H` (default: the 24 px stone
in the pill), scales it up without smoothing and writes one PNG with the frames in a row. The
faces have fixed colours: rest green (95,179,107), working orange (217,119,87), needs you amber
(224,169,59), offline red. A frame that is the wrong colour for the state the pill shows is the
fault; a stone that never changes between frames while the line above says it works is another.

To check the stone after the bar starts: restart the bar (`pkill -x quickshell`, then start it
as the session does), wait twenty seconds, and run it. It should be green and still.

## `lifttrace`: does the pill move when a card changes size?

```sh
scripts/vm-tools/lifttrace trace.txt 85 0.1 &
scripts/vm-tools/probe vpn 50                     # a card that streams in a box at a time
wait; cat trace.txt
```

About ten times a second it records the stone's top row and the first grey row at x 352 (the top
of the card, or of the pill when there is none). Each line is `seconds stone_top card_top`. At rest
the stone's top is constant (853 on 1600x900). Read it for how far and how long it leaves that
value:

- With Hyprland's default layer animation, the stone dipped by up to 50 px at every box added, at the
  final re-layout and when the card went away, and came back with an overshoot over about half a
  second.
- With `hl.animation({ leaf = "layers", enabled = false })` the same card left the stone at rest in
  nearly every sample; what remained were single samples (0.1 s) at the resize.

`lifttrace` finds the stone by its colours (green at rest, orange while a turn runs), so a wallpaper
or a window with those colours at x 368 to 384 confuses it.

## The scripted-API scenarios

`scripts/vm-tools/scratch_api.py` is a scripted Anthropic API for the real `claude` CLI: no model, no
quota, no login. Two scenarios make the CLI do something the pictures should answer:

| word in the prompt | what the CLI does | what should happen |
| --- | --- | --- |
| `tsync` | `sudo systemctl restart systemd-timesyncd` | the turn ends with a "before and after" card with the service's start time before and after |
| `volup` | `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.65` | the sound picture reads 65% and the turn ends with its receipt |

Run it on the host and point agentd in the guest at it:

```sh
python3 scripts/vm-tools/scratch_api.py 18555 requests.log     # on the host
# in the guest, start agentd with:
#   ANTHROPIC_BASE_URL=http://10.0.2.2:18555 ANTHROPIC_API_KEY=sk-ant-fake agentd
scripts/vm-tools/probe tsync 25      # prints every event of the turn with its time
```

`10.0.2.2` is where a QEMU user-mode network reaches the host. `tsync` needs systemd-timesyncd
in the guest and `volup` a sound output (PipeWire). Both end without a card when nothing changed,
which is the right answer to a restart that leaves a service as it was.

## Cautions

- A copy of an installed disk carries the owner's login. A model turn on the copy makes the CLI
  refresh its token, tokens rotate, and the copy and the original then fight over one chain: the
  original can end up signed out. On a copy use launcher words, `!` commands, or the scripted API
  (`scratch-up` and `with-scratch` in the README make and drive a copy).
- Leave memory for the host. Two VMs side by side, one of them the owner's, can end with the
  kernel stopping the owner's. Give a scratch copy 2 GB (`MEM=2G scratch-up`).
- The tools only read the screen and the guest's output; they press nothing on the host and take
  nothing from a window the VM does not own.
