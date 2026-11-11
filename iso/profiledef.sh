#!/usr/bin/env bash
# shellcheck disable=SC2034
iso_name="bombadil"
iso_label="BOMBADIL_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="Bombadil"
iso_application="Bombadil live"
iso_version="$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)"
install_dir="arch"
buildmodes=('iso')
bootmodes=('uefi-x64.systemd-boot.esp' 'uefi-x64.systemd-boot.eltorito')
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="erofs"
# No `-E ztailpacking`: erofs-utils 1.9.4 wrote zeros over the last block of some incompressible files with it,
# kernel modules among them (a Wi-Fi driver, the crypto engine), and nothing complained until a driver failed to load.
airootfs_image_tool_options=('-zlzma,109')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  # mkarchiso copies airootfs without modes, so what runs from here needs listing. Bombadil's own files
  # (the commands, the sudoers file, the GRUB script) come from the package with the modes it gives them.
  ["/usr/lib/node_modules/@anthropic-ai/"]="0:0:755"
  ["/usr/lib/node_modules/@openai/"]="0:0:755"
)
