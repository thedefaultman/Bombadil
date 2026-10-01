# Bombadil's GRUB theme

The screen an installed Bombadil shows while GRUB asks for the disk password: the ground
(`#101214`) and the mark, in ink, with GRUB's own prompt line at the top left. Design:
`docs/design/identity-brief.md` ("Where the mark goes").

- `background.png`: 1920x1080, the ground with the mark (rendered from
  `docs/brand/bombadil-mark.svg`, so the b is cut through the stone). GRUB scales it to the screen.
- `theme.txt`: a gfxterm theme, not wired (below). It names the font `Inter Regular 16`, which
  `inter-16.pf2` provides.

The installer copies `background.png` to `$ESP/grub/themes/bombadil/` and makes the font next to it:

```sh
grub-mkfont -s 16 -o "$ESP/grub/themes/bombadil/inter-16.pf2" "$(fc-match -f '%{file}' 'Inter:style=Regular')"
```

Both live on the ESP, because `grub.cfg` is read there before the disk is unlocked.
`/etc/grub.d/000_bombadil_screen` then loads the font and sets the picture as GRUB's background before
`00_header` asks for the password, and `/etc/default/grub.d/bombadil.cfg` (written by the installer
once the font exists) switches GRUB to the graphical terminal. Checked in a VM: the mark is on the
screen and GRUB's own line ("Enter passphrase for ...") is at the top left, in Inter.

`theme.txt` is not wired. GRUB applies a theme only when its menu shows, which is after the password
(and never, unless Esc is held), and this theme has no menu components, so it would leave that Esc
menu empty. It stays as the starting point for a themed menu: add a `boot_menu` component, then
set `GRUB_THEME=/efi/grub/themes/bombadil/theme.txt` in a drop-in and copy the file to the ESP.
