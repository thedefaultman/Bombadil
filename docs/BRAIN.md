# The brain

An index of everything on the machine that writes itself from what actually happened, and
the Brain window that shows one thing at a time with everything around it. Design:
`/mnt/project-files/design/brain-brief.md` (pieces 1 and 2 are built here).

## What runs

```
 bombadil-brain-watch   root system service, own mount namespace (PrivateMounts=yes)
   fanotify on the whole btrfs filesystem, through a private mount of its top level
   + a fork map from the kernel's process connector, so a writer that already exited
     is still named by its parent
   -> /run/bombadil-brain/watch.sock   JSON lines, each user gets the paths under their home
                                       and /etc; spooled to /var/lib/bombadil-brain when nobody listens

 bombadil-brain         user service
   ~/.local/state/bombadil/brain.db    SQLite (WAL) + FTS5 trigram, outside home snapshots
   also reads turns.jsonl, Chromium's History, pacman.log, memory.md
   -> $XDG_RUNTIME_DIR/bombadil/brain.sock   requests and pushes, JSON lines

 agentd       numbers turns across restarts, logs the files each turn wrote and read,
              and tells the brain when a turn starts and ends (never waits for it)
 the pill     "brain" opens the Brain on what is in front; "why is this here?" answers
              in the line, from the index, without a model
 Brain app    share/apps/brain, a kit app: Focus
```

Every piece degrades. Without the watcher (the live ISO, a dev machine, an ext4 root) the
brain still learns from turns, History, pacman.log and its own walks of home; without the
brain, agentd, the pill and apps carry on as before.

## Who did it

The watcher reads each writer's cgroup. procs.py runs every turn of the machine's agent in
its own `bombadil-turn-*.scope`, coding sessions run under `bombadil-dev-<project>.slice`,
and generated apps run as `bombadil-app run <name>`; anything else in your session is you,
named by the app whose window it was ("you, in the terminal"). Root outside a turn is the
system. Who made a file, and where a download came from, are also written on the file as
xattrs (`user.bombadil.made_by`, `user.bombadil.origin`), so they survive a move and a
rebuild of brain.db.

## fanotify on btrfs, checked

A fanotify mark that reports file handles (the only kind that reports creates, renames and
deletes with the writer's pid) fails with EXDEV on a btrfs subvolume that is not the
filesystem's top level, and the installer mounts `/`, `/home` and `/.snapshots` from the
subvolumes `@`, `@home` and `@snapshots`. The watcher therefore mounts the top level
(`subvolid=5`) read-only at `/run/bombadil-brain/top` inside its own mount namespace, marks
that, and maps paths back through `/proc/self/mountinfo` (`/run/bombadil-brain/top/@home/user/a`
is `/home/user/a`). Checked on Arch's own kernel under QEMU with the installer's layout,
including a nested subvolume (`tests/vm/btrfs-kernel.sh`).

## What gets in, and what stays out

What a person would call a thing: files and folders under home, projects, apps, pages you
read, downloads, turns, packages and the `/etc` files that changed. Caches, most dot-folders,
git-ignored files, `node_modules`, build output, editors' swap files and the Trash stay out;
an app's `data/` counts as the app changing. Private things (`~/.ssh`, `~/.gnupg`, password
stores, keyrings, key and `.env` files, apps' data) are known by name only: never read,
previewed or described.

## Focus

The Brain window puts one thing in the middle. Where it came from is on the left, what was
read while it changed on the right, what it is used with below (amber when another turn
or session is changing it right now), and its changes on top. Every line says who and
when. Click one and it moves to the middle, with a trail of where you have been. A folder
or project in the middle is its listing from disk, so the Brain is also a file manager.
"Ask about this" puts "About ~/path: " in the pill for you to finish.

## Commands

```
bombadil brain status           what it knows, and whether the watcher is on
bombadil brain why PATH         the one-line answer the pill gives
bombadil brain find WORDS       search names, titles and paths
bombadil brain focus REF        the Focus answer as text (--json for the window's data)
bombadil brain rebuild          start the index over from files, xattrs and logs
```
