#!/bin/bash
# Runs in the container as root: an ordinary user with passwordless sudo, like Bombadil's.
set -e
useradd -m u
echo 'u ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/u
cp /usr/bin/sway /usr/local/bin/sway   # the copy drops the file capability docker will not grant
chown u /out
cd /home/u
sudo -u u -H env HOME=/home/u python3 -u /e2e/driver.py 2>&1 | tee /out/driver.log
