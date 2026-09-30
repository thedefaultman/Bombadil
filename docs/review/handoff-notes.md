# Hand-off notes for the "Build and boot the first ISO" thread (written 2026-09-27 by the foundation thread)

Branch `claude/first-milestone` at commit "Make the ISO buildable and testable headless" is what you start from. Not pushed:

1. `unpushed-qmp-lint-fix.patch` (this folder): two `ruff` SIM115 fixes in `scripts/qmp.py` (use `with open(...)`). Apply with `git apply`.
2. An upstream-API review of the code is still running in the old thread; its confirmed findings will be written to `review-findings.md` in this folder when it finishes. Check for that file before you push fixes from the first boot, so both land together.

Environment facts from the old container (may differ in yours, re-test):
- `dockerd` was not running; `nohup dockerd --iptables=false --ip6tables=false --bridge=none &` started it. Containers then needed `--network host` plus the proxy env and `SSL_CERT_FILE=/root/.ccr/ca-bundle.crt` to reach anything; `scripts/build-in-container.sh` handles that with `BOMBADIL_HOST_NET=1` and `SSL_CERT_FILE` exported.
- No `/dev/kvm`, so `scripts/test-vm.sh` falls back to TCG; give it `TIMEOUT=2400` and expect a boot of several minutes.
- `apt-get install -y qemu-system-x86 qemu-utils ovmf` worked; OVMF at `/usr/share/OVMF/OVMF_CODE_4M.fd`.
- Docker Hub was reachable; every Arch mirror was blocked by the old network policy. Re-test `curl -sS -o /dev/null -w '%{http_code}\n' https://geo.mirror.pkgbuild.com/core/os/x86_64/core.db` first.

Things I expect the first real build/boot to hit (unverified guesses, in rough order of likelihood):
- `mkarchiso` may reject or ignore the `file_permissions` entry for `/usr/local/bin/bombadil-smoke` if the path is missing at that point; harmless.
- The `[workspace special:name silent]` exec rule and `togglespecialworkspace` in `src/bombadil/hypr.py`, and `windowrulev2` lines in `hyprland.conf`, may need updating to the current Hyprland syntax (0.50+ uses `windowrule = <rule>, match:...`).
- `shell/shell.qml` uses Quickshell `Socket` + `SplitParser` from memory; check property/signal names against the installed Quickshell.
- `share/qml/Bombadil/qmldir` declares `Theme` without the `singleton` keyword; it needs `singleton Theme 1.0 Theme.qml` for `pragma Singleton` to work.
- Under TCG, Hyprland needs software rendering; if it fails to start, try `WLR_RENDERER_ALLOW_SOFTWARE=1` / `AQ_DRM_DEVICES=/dev/dri/card0` and `-device virtio-vga` (not `-gl`).

UPDATE: review-findings.md is now in this folder (34 items, grouped by what blocks the ISO/demo first; each marked confirmed / confirmed (1) / unverified). The unpushed qmp.py lint fix patch still applies.
