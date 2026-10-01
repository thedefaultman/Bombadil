# Bombadil's GRUB theme

The screen an installed Bombadil shows while GRUB asks for the disk password: the ground
(`#101214`) and the mark, in ink, with GRUB's own line under it. Design:
`docs/design/identity-brief.md` ("Where the mark goes").

- `background.png`: 1920x1080, the ground with the mark (rendered from
  `docs/brand/bombadil-mark.svg`, so the b is cut through the stone). GRUB scales it to the screen.
- `theme.txt`: the gfxterm theme. It names the font `Inter Regular 16`, which `inter-16.pf2` must
  provide (below).

The theme lives on the ESP, because `grub.cfg` is read there before the disk is unlocked. The
installer copies this directory to `$ESP/grub/themes/bombadil/`, makes the font, and adds the
drop-in:

```sh
grub-mkfont -s 16 -o "$ESP/grub/themes/bombadil/inter-16.pf2" "$(fc-match -f '%{file}' 'Inter:style=Regular')"

# /etc/default/grub.d/bombadil.cfg
GRUB_DISTRIBUTOR="Bombadil"
GRUB_TERMINAL_OUTPUT=gfxterm
GRUB_GFXMODE=auto
GRUB_THEME=/efi/grub/themes/bombadil/theme.txt
```

Whether the "Type your password to start Bombadil." line replaces GRUB's (a `read -s` in
`grub.cfg` passed to `cryptomount -p`) is to check in the VM; if `grub-mkconfig`'s own
`cryptomount` still prompts, the screen shows the mark with GRUB's line, which is fine.
