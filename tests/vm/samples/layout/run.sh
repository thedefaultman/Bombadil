#!/bin/bash
# Show the VM's kernel and btrfs layout: each subvolume a test can meet on an installed system.
set -x
uname -r
findmnt -t btrfs -o TARGET,SOURCE,OPTIONS
btrfs subvolume list -a /
# stat -f's %i is the f_fsid; btrfs gives each subvolume its own.
stat -f -c '%i %n' / /home /home/user/Projects
stat -c '%U:%G %a %n' /home/user /home/user/Projects /.snapshots
