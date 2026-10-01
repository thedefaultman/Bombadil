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
    assert "pacman -Syu --noconfirm --needed" in providers.system_prompt()
    assert "never `pacman -Sy` alone" in providers.system_prompt()


# The installer's GRUB defaults are tested with the installer, in tests/test_installer.py.


def test_the_pill_opens_from_super_and_from_alt_space():
    # A VM window on Windows keeps the Windows key for its Start menu, so Alt+Space is the way in there.
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
    # Anchored at the line start, so a commented-out bind does not count.
    binds = re.findall(r'^\s*hl\.bind\("([^"]+)", hl\.dsp\.exec_cmd\("bombadil pill"\)', lua, re.M)
    assert {"SUPER + SUPER_L", "SUPER + SUPER_R", "ALT + space"} <= set(binds)


def test_the_agent_is_told_a_replaced_kernel_needs_a_restart():
    # modprobe of a module (overlay, br_netfilter, docker's) fails after pacman -Syu replaced the running kernel.
    from bombadil import providers
    assert "If an upgrade replaced the kernel, tell the user a restart is needed" in providers.system_prompt()


def test_the_stick_carries_everything_the_installer_calls():
    # bombadil-install checks for these tools before it touches a disk, and mkinitcpio builds the
    # initramfs that opens an encrypted one; the microcode packages are what the installed system's initramfs embeds.
    assert {"grub", "efibootmgr", "btrfs-progs", "snapper", "dosfstools", "gptfdisk", "arch-install-scripts",
            "cryptsetup", "mkinitcpio", "amd-ucode", "intel-ucode", "parted"} <= _packages()


def test_the_installed_clock_is_kept_right_by_timesyncd():
    # The installed system is a copy of the image; a service enabled in the image is enabled there.
    link = ISO / "airootfs/etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service"
    assert link.is_symlink() and link.readlink().name == "systemd-timesyncd.service"
