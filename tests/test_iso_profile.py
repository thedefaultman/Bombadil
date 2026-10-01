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


def test_hyprland_does_not_slide_the_bar_when_a_picture_resizes_it():
    # The bar is a layer that is resized whenever a picture appears or grows. With Hyprland's default
    # "layers" animation the pill dipped and swung back for half a second each time.
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
    assert re.search(r'^\s*hl\.animation\(\{ leaf = "layers", enabled = false \}\)', lua, re.M)


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


def test_the_image_has_what_remote_control_runs_in():
    # `bombadil remote` keeps Claude Code's remote-control server in a detached tmux session.
    assert "tmux" in _packages()


def test_the_image_is_built_without_erofs_tail_packing():
    # erofs-utils 1.9.4 zeroed the last block of some incompressible files with `-E ztailpacking`, among them kernel
    # modules; the installer reads every module back and refuses an install from an image like that.
    profiledef = (ISO / "profiledef.sh").read_text()
    options = re.search(r"^airootfs_image_tool_options=\((.*)\)", profiledef, re.M).group(1)
    assert "ztailpacking" not in options


def test_the_build_reads_the_image_back_and_compares_it_with_its_tree():
    script = (ISO.parent / "scripts/build-iso.sh").read_text()
    assert "verify_image" in script and "diff -rq" in script


def test_every_script_the_image_adds_to_the_path_or_to_grub_is_executable():
    # mkarchiso copies airootfs without modes, so a script that is not listed in file_permissions is not runnable
    # (and grub-mkconfig skips a file in /etc/grub.d that is not executable).
    profiledef = (ISO / "profiledef.sh").read_text()
    listed = set(re.findall(r'\["(/[^"]+)"\]="0:0:755"', profiledef))
    for folder in ("usr/local/bin", "etc/grub.d"):
        shipped = {f"/{folder}/{p.name}" for p in (ISO / "airootfs" / folder).iterdir() if p.is_file()}
        assert shipped <= listed, sorted(shipped - listed)
