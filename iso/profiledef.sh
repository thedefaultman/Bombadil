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
airootfs_image_tool_options=('-zlzma,109' '-E' 'ztailpacking')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/etc/sudoers.d/bombadil"]="0:0:440"
  ["/usr/local/bin/bombadil-setup"]="0:0:755"
  ["/usr/local/bin/bombadil-install"]="0:0:755"
  ["/usr/local/bin/bombadil-smoke"]="0:0:755"
)
