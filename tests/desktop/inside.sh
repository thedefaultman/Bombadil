#!/bin/bash
# Runs in the container as root: an ordinary user with passwordless sudo, like Bombadil's.
set -e
useradd -m u
echo 'u ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/u
cp /usr/bin/sway /usr/local/bin/sway   # the copy drops the file capability docker will not grant
# The coding tools' hooks, as the bombadil package ships them.
mkdir -p /etc/claude-code/managed-settings.d
cp /repo/packaging/bombadil/root/etc/claude-code/managed-settings.d/50-bombadil.json /etc/claude-code/managed-settings.d/
chown u /out
cd /home/u
sudo -u u -H env HOME=/home/u python3 -u /e2e/driver.py 2>&1 | tee /out/driver.log
# What the desktop costs left alone (idle.py), once the driver is done with it
sudo -u u -H env HOME=/home/u python3 -u /e2e/idle.py 2>&1 | tee /out/idle.log
