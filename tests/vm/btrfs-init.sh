#!/bin/bash
# PID 1 of the btrfs test VM (tests/vm/btrfs-kernel.sh installs it as bombadil-test-init).
# No systemd: mount what a test needs, mount the rest of fstab (/home on @home, /.snapshots on
# @snapshots) as boot would, run /test/run.sh as root with its output (stdout and stderr) on
# the serial console between BTRFS-VM markers, then power off.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/bin HOME=/root LANG=C.UTF-8 TERM=dumb
mnt() { mountpoint -q "${@: -1}" || mount "$@"; }
mnt -t proc proc /proc
mnt -t sysfs sys /sys
mnt -t devtmpfs dev /dev
mkdir -p /dev/pts /dev/shm
mnt -t devpts devpts /dev/pts
mnt -t tmpfs shm /dev/shm
mnt -t tmpfs run /run
mnt -t tmpfs tmp /tmp
mnt -t cgroup2 cgroup2 /sys/fs/cgroup
mount -a  # / is mounted already (rootflags=subvol=@); this adds /home and /.snapshots
cat /etc/hostname > /proc/sys/kernel/hostname
stty cols 200 rows 50  # a serial console has no size; without one tools cut lines at 80 columns

cd /test
echo "BTRFS-VM: BEGIN kernel=$(uname -r)"
./run.sh </dev/null 2>&1
rc=$?
sync
echo "BTRFS-VM: END rc=$rc"
sleep 1  # let the serial port drain
echo o > /proc/sysrq-trigger
sleep 60  # if power-off fails, PID 1 exits and panic=-1 with QEMU's -no-reboot stops the VM
