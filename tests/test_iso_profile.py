"""What the ISO profile ships. The VM smoke proves a booted machine; these catch a dropped line sooner."""
import re
from pathlib import Path

ISO = Path(__file__).resolve().parent.parent / "iso"


def _packages() -> set[str]:
    return {line.strip() for line in (ISO / "packages.x86_64").read_text().splitlines()
            if line.strip() and not line.startswith("#")}


def test_there_is_sound():
    # pipewire alone has no ALSA plugin (pipewire-audio), so wireplumber finds no card and
    # "sound" says there is no output; sof-firmware is what most laptop codecs need.
    assert {"pipewire", "pipewire-audio", "pipewire-alsa", "pipewire-pulse", "wireplumber", "alsa-utils",
            "sof-firmware"} <= _packages()


def test_pacman_has_an_active_mirror_and_the_agent_upgrades_as_it_installs():
    # The pacman-mirrorlist package ships every server commented out; without this file the first
    # install had to find and uncomment one. An overlay in airootfs replaces the package's file.
    servers = [line for line in (ISO / "airootfs/etc/pacman.d/mirrorlist").read_text().splitlines()
               if line.startswith("Server")]
    assert servers and all(re.fullmatch(r"Server = https://\S+/\$repo/os/\$arch", s) for s in servers)
    from bombadil import providers
    assert "pacman -Syu --noconfirm --needed" in providers.SYSTEM_PROMPT
    assert "never `pacman -Sy` alone" in providers.SYSTEM_PROMPT
    # An install that updates everything is said out loud, not done quietly.
    assert "That also updates every other package, so say so in one plain sentence" in providers.SYSTEM_PROMPT


ARCH_GRUB_DEFAULTS = """\
# GRUB boot loader configuration

GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="Arch"
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"
GRUB_CMDLINE_LINUX=""

# Uncomment to use basic console
#GRUB_TERMINAL_INPUT=console

# Uncomment to disable graphical terminal
#GRUB_TERMINAL_OUTPUT=console
GRUB_TIMEOUT_STYLE=menu
"""


def _installer_grub_lines(tmp_path, defaults: str) -> list[str]:
    import subprocess
    script = (ISO / "airootfs/usr/local/bin/bombadil-install").read_text()
    start = script.index("for kv in GRUB_TIMEOUT_STYLE")
    end = script.index("grub-install", start)
    # The loop runs inside the chroot on /etc/default/grub; run the same lines on a copy.
    assert script.index("grub-mkconfig") > end
    grub = tmp_path / "grub"
    grub.write_text(defaults)
    subprocess.run(["bash", "-e", "-c", script[start:end].replace("/etc/default/grub", str(grub))], check=True)
    return grub.read_text().splitlines()


def test_grub_boots_straight_in_with_the_menu_one_esc_away(tmp_path):
    lines = _installer_grub_lines(tmp_path, ARCH_GRUB_DEFAULTS)
    for want in ("GRUB_TIMEOUT_STYLE=hidden", "GRUB_TIMEOUT=1", "GRUB_TERMINAL_OUTPUT=console"):
        assert lines.count(want) == 1
    assert "GRUB_TIMEOUT=5" not in lines and "GRUB_TIMEOUT_STYLE=menu" not in lines
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"' in lines and "#GRUB_TERMINAL_INPUT=console" in lines


def test_grub_settings_missing_from_the_defaults_are_added(tmp_path):
    lines = _installer_grub_lines(tmp_path, "GRUB_DEFAULT=0\n")
    assert lines[0] == "GRUB_DEFAULT=0"
    assert set(lines[1:]) == {"GRUB_TIMEOUT_STYLE=hidden", "GRUB_TIMEOUT=1", "GRUB_TERMINAL_OUTPUT=console"}


def test_the_pill_opens_from_super_and_from_alt_space():
    # A VM window on Windows keeps the Windows key for its Start menu, so Alt+Space is the way in there.
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
    # Anchored at the line start, so a commented-out bind does not count.
    binds = re.findall(r'^\s*hl\.bind\("([^"]+)", hl\.dsp\.exec_cmd\("bombadil pill"\)', lua, re.M)
    assert {"SUPER + SUPER_L", "SUPER + SUPER_R", "ALT + space"} <= set(binds)


def test_the_agent_is_told_a_replaced_kernel_needs_a_restart():
    # modprobe of a module (overlay, br_netfilter, docker's) fails after pacman -Syu replaced the running kernel.
    from bombadil import providers
    assert "If an upgrade replaced the kernel, tell the user a restart is needed" in providers.SYSTEM_PROMPT


USER_UNITS = ISO / "airootfs/etc/systemd/user"


def _unit(path: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        elif current is not None and "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    return sections


def test_the_daemon_the_bar_and_the_notifications_come_back_when_they_die():
    units = [USER_UNITS / "bombadil-agentd.service", USER_UNITS / "bombadil-shell.service",
             USER_UNITS / "mako.service.d/restart.conf"]
    for path in units:
        u = _unit(path)
        assert u["Service"]["Restart"] == "always", path.name
        # systemd's default of five restarts in ten seconds would leave a crash loop with no bar.
        assert u.get("Unit", {}).get("StartLimitIntervalSec", u["Service"].get("StartLimitIntervalSec")) == "0", path.name
    # Apps the agent started keep running when only the daemon restarts.
    assert _unit(units[0])["Service"]["KillMode"] == "process"
    assert _unit(units[0])["Service"]["ExecStart"] == "/usr/local/bin/agentd"
    assert _unit(units[1])["Service"]["ExecStart"] == "/usr/bin/quickshell -p /usr/share/bombadil/shell/shell.qml"


def test_the_session_starts_them_after_handing_over_its_environment():
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
    start = lua[lua.index('hl.on("hyprland.start"'):].split("\nend)")[0]
    # WAYLAND_DISPLAY and the Hyprland signature exist only in the session, not in the user manager.
    assert start.index("import-environment") < start.index("restart bombadil-agentd bombadil-shell mako")
    assert "exec_cmd(\"agentd\")" not in start and "quickshell" not in start.replace("bombadil-shell", "")
    # The key for a hung bar restarts the service instead of racing a second quickshell against it.
    assert 'hl.bind("SUPER + CTRL + Escape", hl.dsp.exec_cmd("systemctl --user restart bombadil-shell"))' in lua


def test_setup_and_the_cli_restart_the_daemon_through_systemd():
    setup = (ISO / "airootfs/usr/local/bin/bombadil-setup").read_text()
    assert "systemctl --user restart bombadil-agentd" in setup and "pkill" not in setup
    cli = (ISO.parent / "bin/bombadil").read_text()
    assert "systemctl --user restart bombadil-agentd" in cli


def test_the_installed_system_prunes_its_restore_points_and_takes_no_hourly_ones():
    install = (ISO / "airootfs/usr/local/bin/bombadil-install").read_text()
    assert "set-config TIMELINE_CREATE=no NUMBER_CLEANUP=yes NUMBER_LIMIT=30" in install
    assert "systemctl enable snapper-cleanup.timer" in install
    # The config has to exist before it is changed.
    assert install.index("create-config") < install.index("set-config")


def test_the_installer_takes_the_kernel_from_the_system_it_copies_not_from_the_boot_medium():
    # With copytoram (a USB stick with RAM to spare) /run/archiso/bootmnt is gone by the time the
    # kernel is copied, which used to stop the script after the disk was wiped and copied.
    install = (ISO / "airootfs/usr/local/bin/bombadil-install").read_text()
    assert "bootmnt" not in install
    assert 'cp "$kernel" /mnt/boot/vmlinuz-linux' in install
    # It is found, and checked, before the disk is erased.
    assert install.index('kernel=$(ls -d /usr/lib/modules/*/vmlinuz') < install.index('sgdisk -Z "$disk"')


def test_the_smokes_key_requests_are_numbered_per_boot_so_the_host_sends_them_after_a_reboot_too():
    import re
    smoke = (ISO / "airootfs/usr/local/bin/bombadil-smoke").read_text()
    host = (ISO.parent / "scripts/test-vm.sh").read_text()
    assert 'say "KEYS $bootid-$nkeys $*"' in smoke and "/proc/sys/kernel/random/boot_id" in smoke
    pattern = re.search(r'grep -ao "(BOMBADIL-SMOKE: KEYS [^"]*)"', host).group(1)
    # The undo round trip boots twice; each boot's first request has its own number.
    log = "BOMBADIL-SMOKE: KEYS 1a2b3c4d-1 meta_l\nBOMBADIL-SMOKE: KEYS 9f8e7d6c-1 meta_l\n"
    assert re.findall(pattern, log) == ["BOMBADIL-SMOKE: KEYS 1a2b3c4d-1 meta_l", "BOMBADIL-SMOKE: KEYS 9f8e7d6c-1 meta_l"]
