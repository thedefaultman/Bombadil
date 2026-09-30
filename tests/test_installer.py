"""The installer and the undo script, run as sourced functions against temporary folders.

Nothing here needs a disk: what needs one (partitioning, btrfs, LUKS, GRUB) is tested in a VM by
scripts/test-vm.sh. These tests hold the decisions that are plain shell: what is refused, what a
file is given, what a refresh keeps and what it never replaces.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "iso/airootfs/usr/local/bin"
INSTALL = BIN / "bombadil-install"
ROLLBACK = BIN / "bombadil-rollback"


def sh(script: str, *, env: dict | None = None, path_first: Path | None = None, check: bool = True):
    """Run bash with the installer sourced (its `run` does not start), then `script`."""
    full = {
        "PATH": f"{path_first}:/usr/bin:/bin" if path_first else "/usr/bin:/bin",
        "BOMBADIL_SRC": str(ROOT / "src"),
        "BOMBADIL_CARRY_LIST": str(ROOT / "install/carry.list"),
        "HOME": "/nonexistent",
        **(env or {}),
    }
    return subprocess.run(["bash", "-c", f'source "{INSTALL}"\n{script}'], env=full, capture_output=True,
                          text=True, check=check)


def shim(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    f = directory / name
    f.write_text("#!/usr/bin/env bash\n" + body + "\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR)


# -- the scripts themselves ------------------------------------------------------------------------------

@pytest.mark.parametrize("script", [INSTALL, ROLLBACK])
def test_the_scripts_parse(script):
    subprocess.run(["bash", "-n", str(script)], check=True)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck is not installed")
@pytest.mark.parametrize("script", [INSTALL, ROLLBACK])
def test_the_scripts_pass_shellcheck(script):
    subprocess.run(["shellcheck", "-x", str(script)], check=True)


def test_run_with_nothing_to_do_says_how_to_use_it_and_changes_nothing():
    r = subprocess.run(["bash", str(INSTALL)], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "BOMBADIL_INSTALL_LOG": "/dev/null"})
    assert r.returncode == 2 and "bombadil-install --plan FILE" in r.stdout + r.stderr


def test_a_second_disk_or_a_stray_argument_is_refused():
    r = sh("parse_args /dev/vda /dev/vdb", check=False)
    assert r.returncode != 0 and "one disk at a time" in r.stderr
    r = sh("parse_args /dev/vda --wipe-everything", check=False)
    assert r.returncode != 0 and "unknown argument" in r.stderr


def test_a_bad_plan_stops_before_anything_else(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"disk": "/dev/vda", "password": "hunter2"}))
    r = sh(f"parse_args --plan {plan}; load_plan", check=False)
    assert r.returncode == 2 and "install plan:" in r.stderr and "hunter2" not in r.stdout + r.stderr


# -- the password -----------------------------------------------------------------------------------------

def test_the_password_comes_from_the_descriptor_and_turns_encryption_on():
    r = sh('parse_args /dev/vda --password-fd 7; load_plan; read_password 7< <(echo "correct horse"); '
           'echo "$encrypt|$password"')
    assert r.stdout.strip() == "yes|correct horse"


def test_no_password_means_no_encryption_and_an_encrypted_plan_without_one_is_refused(tmp_path):
    r = sh("parse_args /dev/vda; load_plan; read_password; echo \"$encrypt\"")
    assert r.stdout.strip() == "no"
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"disk": "/dev/vda", "encrypt": True}))
    r = sh(f"parse_args --plan {plan}; load_plan; read_password", check=False)
    assert r.returncode != 0 and "needs a password" in r.stderr


def test_an_empty_password_or_a_bad_descriptor_is_refused():
    r = sh('parse_args /dev/vda --password-fd 7; load_plan; read_password 7< <(echo "")', check=False)
    assert r.returncode != 0 and "empty" in r.stderr
    r = sh("parse_args /dev/vda --password-fd abc; load_plan; read_password", check=False)
    assert r.returncode != 0 and "file descriptor" in r.stderr


# -- what the machine is ------------------------------------------------------------------------------------

def test_the_usb_stick_is_found_from_the_kernel_command_line(tmp_path):
    cmdline = tmp_path / "cmdline"
    shims = tmp_path / "shims"
    shim(shims, "blkid", '[[ "$1 $2" == "-U 2026-09-30-12-00-00-00" ]] && echo /dev/sdb1 || exit 2')
    shim(shims, "lsblk", 'echo sdb')
    cmdline.write_text("BOOT_IMAGE=/vmlinuz archisobasedir=arch archisosearchuuid=2026-09-30-12-00-00-00 quiet\n")
    env = {"BOMBADIL_CMDLINE_FILE": str(cmdline)}
    assert sh("stick_disk", env=env, path_first=shims).stdout.strip() == "/dev/sdb"
    # A CD drive has no disk above the volume.
    shim(shims, "lsblk", "true")
    assert sh("stick_disk", env=env, path_first=shims).stdout.strip() == "/dev/sdb1"
    # A stick taken out after it was copied to memory is not in the way.
    shim(shims, "blkid", "exit 2")
    assert sh("stick_disk", env=env, path_first=shims).stdout.strip() == ""
    cmdline.write_text("BOOT_IMAGE=/vmlinuz quiet\n")
    assert sh("stick_disk", env=env, path_first=shims).stdout.strip() == ""


def test_the_newest_kernel_is_the_one_that_is_installed(tmp_path):
    mods = tmp_path / "modules"
    for v in ("6.9.1-arch1-1", "6.10.2-arch1-1", "6.9.10-arch1-1"):
        (mods / v).mkdir(parents=True)
        (mods / v / "vmlinuz").write_text("k")
    assert sh("newest_kernel", env={"BOMBADIL_MODULES": str(mods)}).stdout.strip().endswith("6.10.2-arch1-1/vmlinuz")
    empty = tmp_path / "none"
    empty.mkdir()
    assert sh("newest_kernel", env={"BOMBADIL_MODULES": str(empty)}, check=False).returncode != 0


# -- GRUB -----------------------------------------------------------------------------------------------------

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


def grub_lines(tmp_path, defaults: str, *, encrypt: str = "no", env: dict | None = None) -> list[str]:
    grub = tmp_path / "grub"
    grub.write_text(defaults)
    sh(f'encrypt={encrypt}; grub_defaults "{grub}"', env=env)
    return grub.read_text().splitlines()


def test_grub_boots_straight_in_with_the_menu_one_esc_away(tmp_path):
    lines = grub_lines(tmp_path, ARCH_GRUB_DEFAULTS)
    for want in ("GRUB_TIMEOUT_STYLE=hidden", "GRUB_TIMEOUT=1", "GRUB_TERMINAL_OUTPUT=console",
                 'GRUB_DISTRIBUTOR="Bombadil"'):
        assert lines.count(want) == 1
    assert "GRUB_TIMEOUT=5" not in lines and "GRUB_TIMEOUT_STYLE=menu" not in lines
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"' in lines and "#GRUB_TERMINAL_INPUT=console" in lines
    assert not [ln for ln in lines if "CRYPTODISK" in ln]


def test_grub_settings_missing_from_the_defaults_are_added(tmp_path):
    lines = grub_lines(tmp_path, "GRUB_DEFAULT=0\n")
    assert lines[0] == "GRUB_DEFAULT=0"
    assert {"GRUB_TIMEOUT_STYLE=hidden", "GRUB_TIMEOUT=1", "GRUB_TERMINAL_OUTPUT=console"} <= set(lines[1:])


def test_grub_is_set_up_twice_with_the_same_result(tmp_path):
    once = grub_lines(tmp_path, ARCH_GRUB_DEFAULTS, encrypt="yes")
    grub = tmp_path / "grub"
    sh(f'encrypt=yes; grub_defaults "{grub}"')
    assert grub.read_text().splitlines() == once


def test_an_encrypted_disk_lets_grub_open_it(tmp_path):
    assert grub_lines(tmp_path, ARCH_GRUB_DEFAULTS, encrypt="yes").count("GRUB_ENABLE_CRYPTODISK=y") == 1


def test_a_test_install_can_show_grub_on_the_serial_line_and_add_kernel_arguments(tmp_path):
    lines = grub_lines(tmp_path, ARCH_GRUB_DEFAULTS, env={
        "BOMBADIL_INSTALL_GRUB_SERIAL": "1", "BOMBADIL_INSTALL_CMDLINE": "bombadil.smoke=layout"})
    assert 'GRUB_TERMINAL_OUTPUT="console serial"' in lines
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="bombadil.smoke=layout loglevel=3 quiet"' in lines


# -- fstab, crypttab, initramfs -----------------------------------------------------------------------------

def test_fstab_names_disks_by_identifier_and_the_system_by_subvolume(tmp_path):
    shims = tmp_path / "shims"
    shim(shims, "blkid", 'case "${@: -1}" in /dev/mapper/root) echo FS-UUID;; /dev/vda1) echo ESP-UUID;; esac')
    target = tmp_path / "target"
    (target / "etc").mkdir(parents=True)
    sh(f'target="{target}"; root_dev=/dev/mapper/root; esp=/dev/vda1; write_fstab', path_first=shims)
    rows = [ln.split("\t") for ln in (target / "etc/fstab").read_text().splitlines() if not ln.startswith("#")]
    by_mount = {r[1]: r for r in rows}
    assert by_mount["/"][0] == "UUID=FS-UUID" and "subvol=@," in by_mount["/"][3] + ","
    assert by_mount["/home"][3].startswith("subvol=@home")
    assert by_mount["/.snapshots"][3] == "subvol=@snapshots"
    assert by_mount["/var/log"][3].startswith("subvol=@log")
    assert by_mount["/var/cache/pacman/pkg"][3] == "subvol=@pkg"
    assert by_mount["/efi"][0] == "UUID=ESP-UUID" and by_mount["/efi"][2] == "vfat"
    assert "/boot" not in by_mount  # the kernel is in the root itself


def test_an_unencrypted_install_has_no_crypttab_and_no_encrypt_hook(tmp_path):
    target = tmp_path / "target"
    (target / "etc/mkinitcpio.conf.d").mkdir(parents=True)
    (target / "etc/mkinitcpio.d").mkdir()
    (target / "etc/crypttab").write_text("stale\n")
    sh(f'target="{target}"; encrypt=no; write_initramfs_config')
    conf = (target / "etc/mkinitcpio.conf.d/bombadil.conf").read_text()
    assert "sd-encrypt" not in conf and "FILES=" not in conf and "base systemd autodetect" in conf
    assert not (target / "etc/crypttab").exists()


def test_an_encrypted_install_carries_the_key_file_and_opens_the_disk_in_the_initramfs(tmp_path):
    target = tmp_path / "target"
    (target / "etc/mkinitcpio.conf.d").mkdir(parents=True)
    (target / "etc/mkinitcpio.d").mkdir()
    key = tmp_path / "root.key"
    key.write_bytes(b"k" * 64)
    sh(f'target="{target}"; encrypt=yes; keyfile="{key}"; luks_uuid=1234-ABCD; write_initramfs_config')
    conf = (target / "etc/mkinitcpio.conf.d/bombadil.conf").read_text()
    assert "sd-encrypt" in conf and "FILES=(/etc/cryptsetup-keys.d/root.key)" in conf
    crypttab = (target / "etc/crypttab").read_text().split()
    assert crypttab[:3] == ["root", "UUID=1234-ABCD", "/etc/cryptsetup-keys.d/root.key"] and "x-initrd.attach" in crypttab[3]
    keyfile = target / "etc/cryptsetup-keys.d/root.key"
    assert keyfile.read_bytes() == b"k" * 64 and stat.S_IMODE(keyfile.stat().st_mode) == 0o400
    assert stat.S_IMODE((target / "etc/crypttab").stat().st_mode) == 0o600
    # Hooks in the order the boot needs: the encrypt hook after the drivers for the disk and before the filesystems.
    hooks = next(ln for ln in conf.splitlines() if ln.startswith("HOOKS="))
    assert hooks.index("block") < hooks.index("sd-encrypt") < hooks.index("filesystems")


def test_the_kernel_and_initramfs_are_made_in_the_roots_own_boot_folder(tmp_path):
    target = tmp_path / "target"
    (target / "etc/mkinitcpio.conf.d").mkdir(parents=True)
    (target / "etc/mkinitcpio.d").mkdir()
    sh(f'target="{target}"; encrypt=no; write_initramfs_config')
    preset = (target / "etc/mkinitcpio.d/linux.preset").read_text()
    for want in ('ALL_kver="/boot/vmlinuz-linux"', 'default_image="/boot/initramfs-linux.img"',
                 'fallback_image="/boot/initramfs-linux-fallback.img"'):
        assert want in preset


# -- what comes along -----------------------------------------------------------------------------------------

def carry_fixture(tmp_path):
    live = tmp_path / "live"
    target = tmp_path / "target"
    (live / ".claude").mkdir(parents=True)
    (live / ".claude/credentials.json").write_text("stick-login")
    (live / ".claude.json").write_text('{"stick": true}')
    (live / ".config/bombadil").mkdir(parents=True)
    (live / ".config/bombadil/config.toml").write_text('provider = "claude"\n')
    (live / ".local/state/bombadil").mkdir(parents=True)
    (live / ".local/state/bombadil/undo.json").write_text('{"snapshot": 12}')
    (live / "Documents").mkdir()
    (live / "Documents/notes.txt").write_text("not on the list")
    (target / "home/user").mkdir(parents=True)
    return live, target


def run_carry(tmp_path, live, target, mode, carry=None):
    carry = carry if carry is not None else [".claude", ".claude.json", ".config/bombadil", ".local/state/bombadil", "Apps"]
    arr = " ".join(f'"{c}"' for c in carry)
    sh(f'target="{target}"; live_home="{live}"; mode={mode}; P_CARRY=({arr}); carry')


def test_a_fresh_install_brings_what_the_carry_list_names_and_nothing_else(tmp_path):
    live, target = carry_fixture(tmp_path)
    run_carry(tmp_path, live, target, "fresh")
    home = target / "home/user"
    assert (home / ".claude/credentials.json").read_text() == "stick-login"
    assert (home / ".claude.json").read_text() == '{"stick": true}'
    assert (home / ".config/bombadil/config.toml").exists()
    assert not (home / "Documents").exists() and not (home / "Apps").exists()
    # A restore-point number from another system means nothing on this one.
    assert not (home / ".local/state/bombadil/undo.json").exists()


def test_only_what_the_person_chose_to_bring_comes(tmp_path):
    live, target = carry_fixture(tmp_path)
    run_carry(tmp_path, live, target, "fresh", carry=[".config/bombadil"])
    home = target / "home/user"
    assert (home / ".config/bombadil/config.toml").exists() and not (home / ".claude").exists()


def test_a_refresh_never_replaces_a_sign_in_the_home_folder_already_has(tmp_path):
    live, target = carry_fixture(tmp_path)
    home = target / "home/user"
    (home / ".claude").mkdir()
    (home / ".claude/credentials.json").write_text("home-login")
    (home / ".claude.json").write_text('{"home": true}')
    (home / ".local/state/bombadil").mkdir(parents=True)
    (home / ".local/state/bombadil/undo.json").write_text('{"snapshot": 3}')
    run_carry(tmp_path, live, target, "refresh")
    assert (home / ".claude/credentials.json").read_text() == "home-login"
    assert (home / ".claude.json").read_text() == '{"home": true}'
    # What the home folder lacks still comes, so a refresh of a home that never signed in gets the stick's.
    assert (home / ".config/bombadil/config.toml").exists()
    # The number of a restore point from before the refresh is gone.
    assert not (home / ".local/state/bombadil/undo.json").exists()


def test_a_refresh_brings_the_sign_in_into_a_home_that_has_none(tmp_path):
    live, target = carry_fixture(tmp_path)
    run_carry(tmp_path, live, target, "refresh")
    assert (target / "home/user/.claude/credentials.json").read_text() == "stick-login"


# -- a refresh keeps what is this computer's -------------------------------------------------------------------

def test_a_refresh_keeps_the_name_the_clock_the_networks_and_the_keys_of_the_old_system(tmp_path):
    old = tmp_path / "top/@.before-refresh-20260930-120000"
    target = tmp_path / "target"
    for rel, text in {
        "etc/hostname": "daniels-laptop\n",
        "etc/machine-id": "0123456789abcdef0123456789abcdef\n",
        "etc/locale.conf": "LANG=pt_PT.UTF-8\n",
        "etc/NetworkManager/system-connections/home.nmconnection": "[wifi]\nssid=home\n",
        "etc/ssh/ssh_host_ed25519_key": "hostkey",
        "etc/ssh/ssh_host_ed25519_key.pub": "hostkey.pub",
        "etc/ssh/ssh_config": "not a host key",
        "etc/cryptsetup-keys.d/root.key": "k",
        "var/lib/bombadil/recovery-key": "a-b-c",
        "etc/shadow": "root:!:1::::::\nuser:$6$salt$hash:1::::::\n",
        "etc/passwd": "not kept",
    }.items():
        f = old / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    (old / "etc/localtime").symlink_to("/usr/share/zoneinfo/Europe/Lisbon")
    (target / "etc").mkdir(parents=True)
    shims = tmp_path / "shims"
    shim(shims, "pacman", 'printf "base\\nvim\\n"')
    sh(f'work="{tmp_path}"; target="{target}"; old="{old.name}"; image=/run/none; old_packages=""; refresh_keep; '
       f'echo "$old_hash" >"{tmp_path}/hash"', path_first=shims)
    assert (target / "etc/hostname").read_text() == "daniels-laptop\n"
    assert (target / "etc/machine-id").exists() and (target / "etc/locale.conf").exists()
    assert (target / "etc/NetworkManager/system-connections/home.nmconnection").exists()
    assert (target / "etc/ssh/ssh_host_ed25519_key").exists() and (target / "etc/ssh/ssh_host_ed25519_key.pub").exists()
    assert not (target / "etc/ssh/ssh_config").exists() and not (target / "etc/passwd").exists()
    assert (target / "etc/cryptsetup-keys.d/root.key").read_text() == "k"
    assert (target / "etc/localtime").is_symlink() and os.readlink(target / "etc/localtime").endswith("Europe/Lisbon")
    assert (tmp_path / "hash").read_text().strip() == "$6$salt$hash"


def test_the_refresh_note_names_the_programs_the_old_system_had_that_the_new_one_does_not(tmp_path):
    shims = tmp_path / "shims"
    shim(shims, "pacman", 'printf "base\\nvim\\n"')
    target = tmp_path / "target"
    (target / "etc").mkdir(parents=True)
    old = tmp_path / "top/@.before-refresh-20260930-120000"
    old.mkdir(parents=True)
    sh(f'work="{tmp_path}"; target="{target}"; old="{old.name}"; image=/run/none; '
       'old_packages="$(printf "base\\nhtop\\nvim\\nzoxide\\n")"; refresh_keep', path_first=shims)
    note = target / "var/lib/bombadil/refresh-20260930-120000.txt"
    assert note.read_text().split() == ["htop", "zoxide"]


# -- the record -----------------------------------------------------------------------------------------------

def record(tmp_path, *, mode="fresh", zone="Europe/Lisbon", source="detected", recovery="", old=""):
    target = tmp_path / "target"
    (target / "etc").mkdir(parents=True, exist_ok=True)
    link = target / "etc/localtime"
    link.unlink(missing_ok=True)
    link.symlink_to(f"/usr/share/zoneinfo/{zone}")
    sh(f'target="{target}"; mode={mode}; version="Bombadil 0.1"; encrypt={"yes" if recovery else "no"}; '
       f'kind=usb; recovery="{recovery}"; old="{old}"; stick_brought=(); P_TIMEZONE_SOURCE={source}; record')
    return json.loads((target / "var/lib/bombadil/install.json").read_text()), target


def test_the_record_says_what_was_installed_where_the_zone_came_from_and_what_is_kept(tmp_path):
    rec, target = record(tmp_path)
    assert rec["layout"] == 1 and rec["mode"] == "fresh" and rec["encrypted"] is False
    assert rec["timezone"] == "Europe/Lisbon" and rec["timezone_source"] == "detected"
    assert rec["target_kind"] == "usb" and rec["recovery_key_pending"] is False and rec["kept_aside"] == []
    assert not (target / "var/lib/bombadil/recovery-key").exists()


def test_a_recovery_key_waits_for_its_first_showing_and_only_the_owner_can_read_it(tmp_path):
    rec, target = record(tmp_path, recovery="abcdefgh-abcdefgh-abcdefgh-abcdefgh-abcdefgh-abcdefgh-abcdefgh-abcdefgh")
    key = target / "var/lib/bombadil/recovery-key"
    assert rec["recovery_key_pending"] is True and rec["encrypted"] is True
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and key.read_text().startswith("abcdefgh-")
    # The record itself never holds the key.
    assert "abcdefgh" not in (target / "var/lib/bombadil/install.json").read_text()


def test_a_refresh_keeps_the_zone_it_found_and_a_utc_only_system_has_not_been_told_one(tmp_path):
    rec, _ = record(tmp_path, mode="refresh", zone="America/Vancouver", source="unset", old="@.before-refresh-1")
    assert rec["timezone_source"] == "chosen" and rec["kept_aside"] == ["@.before-refresh-1", "@snapshots.@.before-refresh-1"]
    rec, _ = record(tmp_path, mode="refresh", zone="UTC", source="detected", old="@.before-refresh-1")
    assert rec["timezone_source"] == "unset"


# -- undo -----------------------------------------------------------------------------------------------------

def rollback_fixture(tmp_path, *, with_kernel=True):
    """A fake top level: what `mount -o subvolid=5` would show, put in place by a mount shim."""
    top = tmp_path / "fake-top"
    (top / "@/boot").mkdir(parents=True)
    (top / "@/boot/vmlinuz-linux").write_text("new kernel")
    (top / "@/marker").write_text("current")
    snap = top / "@snapshots/12/snapshot"
    (snap / "boot").mkdir(parents=True)
    if with_kernel:
        (snap / "boot/vmlinuz-linux").write_text("kernel of 12")
    (snap / "marker").write_text("snapshot 12")
    shims = tmp_path / "shims"
    shim(shims, "btrfs", '[[ "$1 $2" == "subvolume snapshot" ]] || exit 1\ncp -a "$3" "$4"')
    shim(shims, "findmnt", "echo /dev/mapper/root")
    return top, shims


def run_rollback(tmp_path, top, shims, *args):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    # The mount shim copies into the script's temporary folder; umount copies back out, which is what
    # lets the test read the renames the script made at the top level.
    shim(shims, "mount", f'cp -a "{top}/." "${{@: -1}}/"')
    shim(shims, "umount", f'rm -rf "{work}/after"; mkdir -p "{work}/after"; cp -a "$1/." "{work}/after/"; find "$1" -mindepth 1 -delete')
    r = subprocess.run([str(ROLLBACK), "--device", "/dev/null", *args], capture_output=True, text=True,
                       env={"PATH": f"{shims}:/usr/bin:/bin", "TMPDIR": str(tmp_path / "tmp")})
    return r, work / "after"


def test_undo_puts_the_restore_point_in_as_the_system_and_keeps_the_old_one(tmp_path):
    top, shims = rollback_fixture(tmp_path)
    r, after = run_rollback(tmp_path, top, shims, "12")
    assert r.returncode == 0, r.stderr
    assert (after / "@/marker").read_text() == "snapshot 12"
    undone = [p for p in after.iterdir() if p.name.startswith("@.undone-")]
    assert len(undone) == 1 and (undone[0] / "marker").read_text() == "current"


def test_undo_refuses_a_restore_point_with_no_kernel_to_start_and_changes_nothing(tmp_path):
    top, shims = rollback_fixture(tmp_path, with_kernel=False)
    r, after = run_rollback(tmp_path, top, shims, "12")
    assert r.returncode == 1 and "no kernel of its own" in r.stderr
    assert (after / "@/marker").read_text() == "current" and not any(p.name.startswith("@.undone-") for p in after.iterdir())


def test_a_system_from_before_the_kernel_moved_into_the_root_still_undoes(tmp_path):
    # Its kernel is on the EFI partition, so no restore point has one and none needs one.
    top, shims = rollback_fixture(tmp_path, with_kernel=False)
    (top / "@/boot/vmlinuz-linux").unlink()
    r, after = run_rollback(tmp_path, top, shims, "12")
    assert r.returncode == 0, r.stderr
    assert (after / "@/marker").read_text() == "snapshot 12"


def test_undo_of_a_restore_point_that_does_not_exist_is_refused(tmp_path):
    top, shims = rollback_fixture(tmp_path)
    r, _ = run_rollback(tmp_path, top, shims, "99")
    assert r.returncode == 1 and "no snapshot 99" in r.stderr


def test_undoing_a_refresh_puts_the_old_system_and_its_restore_points_back(tmp_path):
    top, shims = rollback_fixture(tmp_path)
    old = top / "@.before-refresh-20260930-120000"
    (old / "boot").mkdir(parents=True)
    (old / "boot/vmlinuz-linux").write_text("old kernel")
    (old / "marker").write_text("before the refresh")
    (top / "@snapshots.@.before-refresh-20260930-120000/7").mkdir(parents=True)
    r, after = run_rollback(tmp_path, top, shims, "refresh")
    assert r.returncode == 0, r.stderr
    assert (after / "@/marker").read_text() == "before the refresh"
    assert (after / "@snapshots/7").is_dir()
    assert any(p.name.startswith("@.after-refresh-") and (p / "marker").read_text() == "current" for p in after.iterdir())


def test_undoing_a_refresh_with_nothing_to_go_back_to_is_refused(tmp_path):
    top, shims = rollback_fixture(tmp_path)
    r, _ = run_rollback(tmp_path, top, shims, "refresh")
    assert r.returncode == 1 and "no system from before a refresh" in r.stderr
