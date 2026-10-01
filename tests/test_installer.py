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


def sh(script: str, *, env: dict | None = None, path_first: Path | None = None, check: bool = True,
       input: str | None = None):
    """Run bash with the installer sourced (its `run` does not start), then `script`."""
    full = {
        "PATH": f"{path_first}:/usr/bin:/bin" if path_first else "/usr/bin:/bin",
        "BOMBADIL_SRC": str(ROOT / "src"),
        "BOMBADIL_CARRY_LIST": str(ROOT / "install/carry.list"),
        "HOME": "/nonexistent",
        **(env or {}),
    }
    return subprocess.run(["bash", "-c", f'source "{INSTALL}"\n{script}'], env=full, capture_output=True,
                          text=True, check=check, input=input)


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
    (live / "Apps/demo").mkdir(parents=True)
    (live / "Apps/demo/main.qml").write_text("made before")
    (target / "home/user").mkdir(parents=True)
    return live, target


def run_carry(tmp_path, live, target, mode, carry=None):
    """carry() with P_CARRY from the plan module, as the installer has it: the shipped list, or the plan's own."""
    args = "--plan " + str(tmp_path / "plan.json")
    (tmp_path / "plan.json").write_text(json.dumps({"disk": "/dev/vda", **({"carry": carry} if carry is not None else {})}))
    sh(f'parse_args {args}; load_plan; target="{target}"; live_home="{live}"; mode={mode}; carry',
       env={"BOMBADIL_NM_CONNECTIONS": str(tmp_path / "none")})


def test_a_fresh_install_brings_what_the_carry_list_names_and_nothing_else(tmp_path):
    live, target = carry_fixture(tmp_path)
    run_carry(tmp_path, live, target, "fresh")
    home = target / "home/user"
    assert (home / ".claude/credentials.json").read_text() == "stick-login"
    assert (home / ".claude.json").read_text() == '{"stick": true}'
    assert (home / ".config/bombadil/config.toml").exists()
    assert (home / "Apps/demo/main.qml").read_text() == "made before"   # on the shipped list
    assert not (home / "Documents").exists()                            # not on it
    # A restore-point number from another system means nothing on this one.
    assert not (home / ".local/state/bombadil/undo.json").exists()


def test_only_what_the_person_chose_to_bring_comes(tmp_path):
    live, target = carry_fixture(tmp_path)
    run_carry(tmp_path, live, target, "fresh", carry=[".config/bombadil"])
    home = target / "home/user"
    assert (home / ".config/bombadil/config.toml").exists()
    assert not (home / ".claude").exists() and not (home / "Apps").exists()


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


def old_system(top, stamp, marker, *, complete=True, incomplete=False):
    """A system a refresh put aside."""
    d = top / f"@.before-refresh-{stamp}"
    (d / "boot").mkdir(parents=True)
    (d / "boot/vmlinuz-linux").write_text("old kernel")
    (d / "usr/lib/modules").mkdir(parents=True)
    (d / "marker").write_text(marker)
    if complete:
        (d / "etc").mkdir()
        (d / "etc/fstab").write_text("# fstab\n")
    if incomplete:
        (d / "var/lib/bombadil").mkdir(parents=True)
        (d / "var/lib/bombadil/install-incomplete").write_text("")
    return d


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
    old_system(top, "20260930-120000", "before the refresh")
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


def test_undoing_a_refresh_goes_back_to_the_whole_system_not_to_a_half_made_one(tmp_path):
    # A refresh that stopped part way leaves its own half system newer than the user's; it must never be chosen.
    top, shims = rollback_fixture(tmp_path)
    old_system(top, "20260930-100000", "the user's own system")
    old_system(top, "20260930-110000", "half of a refresh", incomplete=True)
    old_system(top, "20260930-120000", "a bare folder", complete=False)
    r, after = run_rollback(tmp_path, top, shims, "refresh")
    assert r.returncode == 0, r.stderr
    assert (after / "@/marker").read_text() == "the user's own system"


def test_undo_finishes_when_an_earlier_one_stopped_with_no_system_in_place(tmp_path):
    # Two renames with a stop between them leave no @; running undo again puts one in.
    top, shims = rollback_fixture(tmp_path)
    shutil.move(top / "@", top / "@.undone-earlier")
    r, after = run_rollback(tmp_path, top, shims, "12")
    assert r.returncode == 0, r.stderr
    assert (after / "@/marker").read_text() == "snapshot 12" and (after / "@.undone-earlier/marker").exists()


def test_undo_takes_the_snapshot_before_it_moves_the_system_so_a_failure_leaves_the_system_alone(tmp_path):
    top, shims = rollback_fixture(tmp_path)
    shim(shims, "btrfs", "exit 1")
    r, after = run_rollback(tmp_path, top, shims, "12")
    assert r.returncode != 0
    assert (after / "@/marker").read_text() == "current"


def test_undo_works_where_the_system_cannot_exchange_two_names_in_one_step(tmp_path):
    top, shims = rollback_fixture(tmp_path)
    real_mv = shutil.which("mv")
    shim(shims, "mv", f'[[ "$1" == --exchange ]] && exit 1\nexec {real_mv} "$@"')
    r, after = run_rollback(tmp_path, top, shims, "12")
    assert r.returncode == 0, r.stderr
    assert (after / "@/marker").read_text() == "snapshot 12"
    assert any((p / "marker").read_text() == "current" for p in after.iterdir() if p.name.startswith("@.undone-"))


# -- btrfs, as a folder with a list of which folders are subvolumes ---------------------------------------------

def btrfs_shim(shims: Path, state: Path | None = None) -> None:
    """A subvolume is a folder with a marker file in it, so it stays one when it is renamed."""
    shim(shims, "btrfs", """mark=.subvolume
case "$1 $2" in
  "subvolume create") mkdir "$3" && touch "$3/$mark" ;;
  "subvolume show") [[ -e "$3/$mark" ]] ;;
  "subvolume delete") rm -rf "${@: -1}" ;;
  "subvolume snapshot") cp -a "$3" "$4" && touch "$4/$mark" ;;
  "subvolume get-default") echo "ID 5 (FS_TREE)" ;;
  *) exit 1 ;;
esac""")


def make_top(tmp_path, *, layout1: bool):
    work = tmp_path / "work"
    top = work / "top"
    state = None
    shims = tmp_path / "shims"
    btrfs_shim(shims)
    names = ["@", "@home", "@snapshots"] + (["@log", "@pkg"] if layout1 else [])
    for n in names:
        (top / n).mkdir(parents=True)
        (top / n / ".subvolume").touch()
    (top / "@/boot").mkdir()
    (top / "@/boot/vmlinuz-linux").write_text("kernel")
    (top / "@/etc").mkdir()
    (top / "@/etc/fstab").write_text("UUID=x\t/boot\tvfat\tumask=0077\t0 2\n")
    (top / "@/marker").write_text("the user's system")
    (top / "@snapshots/3").mkdir()
    (top / "@home/user").mkdir()
    if layout1:
        (top / "@log/journal-marker").write_text("logs")
    shim(shims, "pacman", "printf 'base\\nvim\\n'")
    shim(shims, "mount", "true")
    shim(shims, "umount", "true")
    return work, top, shims, state


def result(r):
    """The fields the last line of a run printed."""
    return r.stdout.strip().splitlines()[-1].split("|")


def refresh_aside(tmp_path, *, layout1: bool):
    work, top, shims, state = make_top(tmp_path, layout1=layout1)
    r = sh(f'work="{work}"; esp=/dev/null; old=""; resume=0; refresh_aside; echo "$old|$made_log|$made_pkg|$phase"',
           path_first=shims, check=False)
    return r, work, top, state


def test_refresh_of_a_disk_that_is_already_layout_1_keeps_its_logs_and_package_cache(tmp_path):
    # Every layout-1 disk has @log and @pkg; making them again failed, after the system was already moved aside.
    r, work, top, state = refresh_aside(tmp_path, layout1=True)
    assert r.returncode == 0, r.stderr
    old, made_log, made_pkg, phase = result(r)
    assert old.startswith("@.before-refresh-") and (made_log, made_pkg, phase) == ("0", "0", "aside")
    assert (top / "@log/journal-marker").read_text() == "logs"
    assert (top / old / "marker").read_text() == "the user's system"
    assert (top / "@").is_dir() and not (top / "@/marker").exists()
    assert (top / f"@snapshots.{old}/3").is_dir()


def test_refresh_of_an_older_disk_makes_the_logs_and_package_subvolumes_and_notes_it(tmp_path):
    r, work, top, state = refresh_aside(tmp_path, layout1=False)
    assert r.returncode == 0, r.stderr
    assert result(r)[1:3] == ["1", "1"]
    assert (top / "@log").is_dir() and (top / "@pkg").is_dir()


def test_the_old_fstab_is_pointed_at_the_new_boot_partition_and_its_old_text_is_kept_to_go_back(tmp_path):
    r, work, top, state = refresh_aside(tmp_path, layout1=False)
    old = result(r)[0]
    assert "\t/efi\tvfat" in (top / old / "etc/fstab").read_text()
    assert "\t/boot\tvfat" in (top / old / "etc/fstab.bombadil-backup").read_text()


def test_a_refresh_that_stops_before_the_boot_files_change_puts_the_old_system_straight_back(tmp_path):
    r, work, top, state = refresh_aside(tmp_path, layout1=False)
    old = result(r)[0]
    (top / "@/half-copied").write_text("x")
    shims = tmp_path / "shims"
    r = sh(f'work="{work}"; old="{old}"; made_log=1; made_pkg=1; refresh_restore; echo rc=$?', path_first=shims, check=False)
    assert "rc=0" in r.stdout, r.stderr
    assert (top / "@/marker").read_text() == "the user's system" and not (top / "@/half-copied").exists()
    assert (top / "@snapshots/3").is_dir() and not (top / "@log").exists() and not (top / "@pkg").exists()
    # The old fstab is as it was, so the old system starts as it always did.
    assert "\t/boot\tvfat" in (top / "@/etc/fstab").read_text() and not (top / "@/etc/fstab.bombadil-backup").exists()


@pytest.mark.parametrize("phase,mode,say", [
    ("checking", "fresh", "before anything on the disk was changed"),
    ("erased", "fresh", "after the disk was erased"),
    ("boot", "refresh", "Run the refresh again"),
])
def test_what_the_installer_says_when_it_stops_is_what_happened(tmp_path, phase, mode, say):
    shims = tmp_path / "shims"
    shim(shims, "mountpoint", "exit 1")
    r = sh(f'work="{tmp_path}/w"; target="{tmp_path}/t"; phase={phase}; mode={mode}; old=@.before-refresh-1; '
           'set +e; (exit 1); cleanup', path_first=shims, check=False)
    assert r.returncode == 1 and say in r.stdout


def test_a_refresh_that_put_everything_back_says_so(tmp_path):
    r, work, top, state = refresh_aside(tmp_path, layout1=False)
    old = result(r)[0]
    shims = tmp_path / "shims"
    shim(shims, "mountpoint", '[[ "$2" == "' + str(work / "top") + '" ]]')
    r = sh(f'work="{work}"; target="{tmp_path}/t"; phase=aside; mode=refresh; old="{old}"; made_log=1; made_pkg=1; '
           'set +e; (exit 1); cleanup', path_first=shims, check=False)
    assert "put everything back" in r.stdout and (top / "@/marker").exists()


# -- the subvolumes inside the home folder --------------------------------------------------------------------

def home_fixture(tmp_path):
    shims = tmp_path / "shims"
    btrfs_shim(shims)
    target = tmp_path / "target"
    (target / "home/user").mkdir(parents=True)
    return shims, None, target


def make_sub(shims, target, rel, check=True):
    return sh(f'target="{target}"; make_subvolume "{target}/home/user/{rel}"', path_first=shims, check=check)


def test_a_folder_with_files_becomes_a_subvolume_with_the_same_files_and_no_leftover(tmp_path):
    shims, state, target = home_fixture(tmp_path)
    d = target / "home/user/Projects"
    (d / "demo").mkdir(parents=True)
    (d / "demo/main.py").write_text("code")
    d.chmod(0o750)
    make_sub(shims, target, "Projects")
    assert (d / "demo/main.py").read_text() == "code" and (d / ".subvolume").exists()
    assert stat.S_IMODE(d.stat().st_mode) == 0o750
    assert not (target / "home/user/Projects.bombadil-old").exists() and not (target / "home/user/Projects.bombadil-new").exists()
    # Run again: nothing changes.
    make_sub(shims, target, "Projects")
    assert (d / "demo/main.py").read_text() == "code"


def test_a_folder_the_install_did_not_finish_converting_is_not_lost_or_orphaned(tmp_path):
    shims, state, target = home_fixture(tmp_path)
    home = target / "home/user"
    # Stopped between the two renames: the files are in the .bombadil-old folder and the folder is missing.
    (home / ".claude.bombadil-old").mkdir()
    (home / ".claude.bombadil-old/credentials.json").write_text("login")
    make_sub(shims, target, ".claude")
    assert (home / ".claude/credentials.json").read_text() == "login"
    assert not (home / ".claude.bombadil-old").exists()


def test_an_old_copy_beside_a_folder_that_has_files_stops_the_install_instead_of_hiding_either(tmp_path):
    shims, state, target = home_fixture(tmp_path)
    home = target / "home/user"
    (home / "Projects").mkdir()
    (home / "Projects/a.txt").write_text("a")
    (home / "Projects.bombadil-old").mkdir()
    (home / "Projects.bombadil-old/b.txt").write_text("b")
    r = make_sub(shims, target, "Projects", check=False)
    assert r.returncode != 0 and "left from an earlier try" in r.stderr
    assert (home / "Projects/a.txt").exists() and (home / "Projects.bombadil-old/b.txt").exists()


def test_a_new_folder_is_made_as_a_subvolume_owned_by_the_account_and_private_where_it_holds_a_sign_in(tmp_path):
    shims, state, target = home_fixture(tmp_path)
    make_sub(shims, target, ".claude")
    make_sub(shims, target, ".local/share/bombadil/browser")
    make_sub(shims, target, ".cache")
    home = target / "home/user"
    assert stat.S_IMODE((home / ".claude").stat().st_mode) == 0o700
    assert stat.S_IMODE((home / ".local/share/bombadil/browser").stat().st_mode) == 0o700
    assert stat.S_IMODE((home / ".cache").stat().st_mode) == 0o755
    assert (home / ".claude").stat().st_uid == (home).stat().st_uid


def test_a_link_is_left_alone(tmp_path):
    shims, state, target = home_fixture(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (target / "home/user/Projects").symlink_to(elsewhere)
    make_sub(shims, target, "Projects")
    assert (target / "home/user/Projects").is_symlink()


# -- the account -------------------------------------------------------------------------------------------------

def configure_run(tmp_path, *, mode, password="", old_hash=""):
    target = tmp_path / "target"
    (target / "etc/mkinitcpio.conf.d").mkdir(parents=True)
    (target / "etc/mkinitcpio.d").mkdir()
    log = tmp_path / "chroot.log"
    shims = tmp_path / "shims"
    shim(shims, "blkid", 'echo UUID-X')
    shim(shims, "arch-chroot", f'echo "CHROOT $*" >>"{log}"\ncat >>"{log}"')
    pw = password.replace("'", "'\\''")
    sh(f"""target="{target}"; mode={mode}; encrypt=no; root_dev=/dev/x; esp=/dev/y; P_HOSTNAME=""; P_TIMEZONE=UTC; P_KEYMAP=""
        password='{pw}'; old_hash='{old_hash}'; configure""", path_first=shims)
    return log.read_text()


def test_the_password_goes_to_the_account_on_standard_input_and_never_on_a_command_line(tmp_path):
    log = configure_run(tmp_path, mode="fresh", password="it's a $ecret: with spaces")
    assert "user:it's a $ecret: with spaces" in log
    chroot_lines = [ln for ln in log.splitlines() if ln.startswith("CHROOT")]
    assert not any("ecret" in ln for ln in chroot_lines)
    assert any(ln.endswith("chpasswd") for ln in chroot_lines)


def test_a_refresh_keeps_the_account_its_old_password_even_when_a_password_was_given_to_open_the_disk(tmp_path):
    log = configure_run(tmp_path, mode="refresh", password="the disk password", old_hash="$6$salt$hash")
    assert "user:$6$salt$hash" in log and "chpasswd -e" in log and "the disk password" not in log


def test_no_password_leaves_the_account_without_one(tmp_path):
    log = configure_run(tmp_path, mode="fresh", password="")
    assert "passwd -d user" in log


# -- what is refused before anything is touched -------------------------------------------------------------------

def probe_fixture(tmp_path, *, efi=True, size=40 << 30, types="", mounts="", stick="", tools=None):
    shims = tmp_path / "shims"
    for t in (tools if tools is not None else ["sgdisk", "wipefs", "mkfs.fat", "mkfs.btrfs", "btrfs", "blkid", "partprobe",
                                               "arch-chroot", "snapper", "grub-install", "cryptsetup", "systemd-cryptenroll"]):
        shim(shims, t, "true")
    shim(shims, "blockdev", f"echo {size}")
    shim(shims, "lsblk", f"""case "$*" in
  *FSTYPE*) printf '%s\\n' {types or "''"} ;; *TYPE*) echo disk ;; *MOUNTPOINTS*) echo '{mounts}' ;; *TRAN*) echo sata ;; *) echo ;;
esac""")
    shim(shims, "mountpoint", "exit 1")
    efi_dir = tmp_path / "efi"
    if efi:
        efi_dir.mkdir(exist_ok=True)
    return shims, {"BOMBADIL_EFI_SYSFS": str(efi_dir), "BOMBADIL_MODULES": str(tmp_path / "modules")}, stick


def run_probe(tmp_path, **kw):
    shims, env, stick = probe_fixture(tmp_path, **kw)
    (tmp_path / "modules/6.1.0").mkdir(parents=True, exist_ok=True)
    (tmp_path / "modules/6.1.0/vmlinuz").write_text("k")
    body = f'is_block() {{ true; }}; stick_disk() {{ echo "{stick}"; }}; disk=/dev/vda; mode=fresh; encrypt=no; P_KIND=""; probe; echo "kernel=$kernel"'
    return sh(body, env=env, path_first=shims, check=False)


def test_a_disk_that_passes_every_check_is_let_through(tmp_path):
    r = run_probe(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "kernel=" in r.stdout and r.stdout.strip().endswith("vmlinuz")


@pytest.mark.parametrize("kw,message", [
    ({"efi": False}, "did not start in UEFI mode"),
    ({"stick": "/dev/vda"}, "is the USB stick Bombadil started from"),
    ({"size": 8 << 30}, "smaller than 16 GB"),
    ({"types": "iso9660"}, "holds a Bombadil install image"),
    ({"mounts": "/mnt/data"}, "is in use"),
    ({"tools": ["sgdisk"]}, "is missing from this system"),
])
def test_a_disk_that_should_not_be_written_is_refused_with_the_reason(tmp_path, kw, message):
    r = run_probe(tmp_path, **kw)
    assert r.returncode != 0 and message in r.stderr


def test_the_stick_is_refused_for_what_it_is_not_for_its_size(tmp_path):
    # An 8 GB stick is also too small; the message that matters is the one about the stick.
    r = run_probe(tmp_path, stick="/dev/vda", size=8 << 30)
    assert "USB stick Bombadil started from" in r.stderr


def test_a_key_file_and_a_recovery_key_are_needed_only_when_the_disk_is_encrypted(tmp_path):
    shims, env, _ = probe_fixture(tmp_path, tools=["sgdisk", "wipefs", "mkfs.fat", "mkfs.btrfs", "btrfs", "blkid", "partprobe",
                                                   "arch-chroot", "snapper", "grub-install"])
    (tmp_path / "modules/6.1.0").mkdir(parents=True, exist_ok=True)
    (tmp_path / "modules/6.1.0/vmlinuz").write_text("k")
    body = 'is_block() { true; }; stick_disk() { :; }; disk=/dev/vda; mode=fresh; P_KIND=""; encrypt=%s; probe'
    assert sh(body % "no", env=env, path_first=shims, check=False).returncode == 0
    r = sh(body % "yes", env=env, path_first=shims, check=False)
    assert r.returncode != 0 and "cryptsetup is missing" in r.stderr


def test_a_password_longer_than_grub_can_take_is_refused():
    r = sh('parse_args /dev/vda --password-fd 7; load_plan; read_password 7< <(printf "%0300d\\n" 7)', check=False)
    assert r.returncode != 0 and "longer than 255" in r.stderr


def test_the_preset_does_not_name_a_config_so_the_drop_in_with_the_hooks_is_read(tmp_path):
    target = tmp_path / "target"
    (target / "etc/mkinitcpio.conf.d").mkdir(parents=True)
    (target / "etc/mkinitcpio.d").mkdir()
    sh(f'target="{target}"; encrypt=no; write_initramfs_config')
    preset = (target / "etc/mkinitcpio.d/linux.preset").read_text()
    # mkinitcpio -c (which a preset's ALL_config makes it do) skips /etc/mkinitcpio.conf.d.
    assert not [ln for ln in preset.splitlines() if ln.startswith("ALL_config")]
    assert "PRESETS=('default' 'fallback')" in preset


# -- programs installed on the stick ---------------------------------------------------------------------------------

def test_programs_installed_on_the_stick_come_along_from_the_stick_own_package_files(tmp_path):
    target = tmp_path / "target"
    (target / "var/cache/pacman/pkg").mkdir(parents=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    for f in ("spotify-2-1-x86_64.pkg.tar.zst", "spotify-2-1-x86_64.pkg.tar.zst.sig", "libfoo-3-2-x86_64.pkg.tar.zst"):
        (cache / f).write_text("pkg")
    log = tmp_path / "log"
    shims = tmp_path / "shims"
    shim(shims, "pacman", """if [[ "$1" == --dbpath ]]; then printf 'base 1-1\\nvim 9-1\\nlibfoo 3-1\\n'
else printf 'base 1-1\\nvim 9-1\\nspotify 2-1\\nlibfoo 3-2\\nmissing 1-1\\n'; fi""")
    shim(shims, "arch-chroot", f'echo "CHROOT $*" >>"{log}"')
    r = sh(f'target="{target}"; image=/run/img; record() {{ echo RECORD >>"{log}"; }}; snapshot() {{ echo "SNAP $1" >>"{log}"; }}; '
           'replay_stick_packages; echo "brought=${stick_brought[*]}"',
           env={"BOMBADIL_STICK_CACHE": str(cache)}, path_first=shims)
    assert "brought=libfoo spotify" in r.stdout
    assert "missing 1-1 came from the stick but its package file is gone" in r.stderr + r.stdout
    text = log.read_text()
    assert "pacman -U --noconfirm --needed /var/cache/pacman/pkg/libfoo-3-2-x86_64.pkg.tar.zst /var/cache/pacman/pkg/spotify-2-1-x86_64.pkg.tar.zst" in text
    assert "SNAP Brought from the USB stick: libfoo, spotify" in text
    assert (target / "var/cache/pacman/pkg/spotify-2-1-x86_64.pkg.tar.zst.sig").exists()


def test_nothing_is_brought_when_the_stick_has_what_the_image_has(tmp_path):
    shims = tmp_path / "shims"
    shim(shims, "pacman", "printf 'base 1-1\\n'")
    shim(shims, "arch-chroot", "exit 9")
    r = sh(f'target="{tmp_path}"; image=/run/img; replay_stick_packages; echo "brought=${{stick_brought[*]:-}}"',
           env={"BOMBADIL_STICK_CACHE": str(tmp_path)}, path_first=shims)
    assert r.stdout.strip().endswith("brought=")


# -- the VM test scripts ---------------------------------------------------------------------------------------------

def test_the_vm_test_reads_serial_lines_that_end_in_a_carriage_return():
    # A serial console ends its lines with CR LF; a pattern anchored with a bare $ never matches them.
    text = (ROOT / "scripts/test-vm.sh").read_text()
    anchored = [ln for ln in text.splitlines() if "BOMBADIL-SMOKE: PASS" in ln and '$"' in ln]
    assert anchored, "the test no longer checks that the installs passed"
    assert all("[[:space:]]*$" in ln for ln in anchored), anchored


def test_the_installed_systems_own_restart_is_answered_at_grub_every_time_it_asks():
    text = (ROOT / "scripts/test-vm.sh").read_text()
    assert "seen > typed" in text  # not once per boot() call: the undo test restarts the machine by itself


def test_the_smoke_test_recognises_the_stick_refusal_by_its_own_words_not_by_a_phrase_other_messages_share():
    text = (ROOT / "iso/airootfs/usr/local/bin/bombadil-smoke").read_text()
    assert "is the USB stick Bombadil started from" in text
    installer = INSTALL.read_text()
    assert "is the USB stick Bombadil started from" in installer


# -- the check at the end of an install -----------------------------------------------------------------

def _finished_install(tmp_path: Path, cryptomount: str | None):
    """A target that looks like a finished install, and the shims verify() asks for."""
    t = tmp_path / "target"
    for f in ("boot/vmlinuz-linux", "boot/initramfs-linux.img", "boot/initramfs-linux-fallback.img",
              "efi/EFI/BOOT/BOOTX64.EFI", "efi/grub/x86_64-efi/luks2.mod"):
        (t / f).parent.mkdir(parents=True, exist_ok=True)
        (t / f).write_text("x")
    lines = ["linux /@/boot/vmlinuz-linux root=UUID=1 rootflags=subvol=@",
             "initrd /@/boot/initramfs-linux.img", "initrd /@/boot/initramfs-linux-fallback.img"]
    if cryptomount:
        lines += ["\tinsmod luks2", f"\tcryptomount -u {cryptomount}", "\tset root='cryptouuid/x'"]
    (t / "efi/grub").mkdir(parents=True, exist_ok=True)
    (t / "efi/grub/grub.cfg").write_text("\n".join(lines) + "\n")
    for m in ("good.ko.zst", "also-good.ko.zst"):
        (t / "usr/lib/modules/7.0/kernel" / m).parent.mkdir(parents=True, exist_ok=True)
        (t / "usr/lib/modules/7.0/kernel" / m).write_text("fine")
    shims = tmp_path / "shims"
    shim(shims, "btrfs", 'echo "ID 5 (FS_TREE)"')
    # zstd -tq FILE...: fails for a file whose text says it is damaged
    shim(shims, "zstd", 'rc=0; for f in "${@:2}"; do grep -q damaged "$f" && rc=1; done; exit $rc')
    return t, shims


LISTING = "etc/cryptsetup-keys.d/root.key\netc/crypttab\nusr/lib/systemd/systemd-cryptsetup"


@pytest.mark.parametrize("spelling", ["3fd2b9ec-88c2-4c23-b979-2aa13b001965", "3fd2b9ec88c24c23b9792aa13b001965"])
def test_the_check_accepts_the_disk_uuid_the_way_grub_writes_it_with_or_without_dashes(tmp_path, spelling):
    # grub-mkconfig writes the dashed form in cryptomount and the plain one in the root name; either opens the disk.
    t, shims = _finished_install(tmp_path, spelling)
    r = sh(f'target={t}; encrypt=yes; luks_uuid=3fd2b9ec-88c2-4c23-b979-2aa13b001965; '
           f'chroot_run() {{ printf "%s\\n" "{LISTING}"; }}; verify', path_first=shims, check=False)
    assert r.returncode == 0, r.stderr


def test_the_check_refuses_a_boot_loader_that_opens_another_disk(tmp_path):
    t, shims = _finished_install(tmp_path, "00000000-1111-2222-3333-444444444444")
    r = sh(f'target={t}; encrypt=yes; luks_uuid=3fd2b9ec-88c2-4c23-b979-2aa13b001965; '
           f'chroot_run() {{ printf "%s\\n" "{LISTING}"; }}; verify', path_first=shims, check=False)
    assert r.returncode != 0 and "different disk" in r.stderr


def test_the_check_wants_no_disk_to_be_opened_when_nothing_is_encrypted(tmp_path):
    t, shims = _finished_install(tmp_path, None)
    r = sh(f"target={t}; encrypt=no; verify", path_first=shims, check=False)
    assert r.returncode == 0, r.stderr


def test_a_damaged_kernel_module_is_found_and_named_so_a_bad_stick_is_not_installed_from_twice(tmp_path):
    t, shims = _finished_install(tmp_path, None)
    (t / "usr/lib/modules/7.0/kernel/also-good.ko.zst").write_text("damaged")
    r = sh(f"target={t}; encrypt=no; verify", path_first=shims, check=False)
    assert r.returncode != 0
    assert "also-good.ko.zst" in r.stderr and "write it again" in r.stderr and "good.ko.zst) " not in r.stderr


def test_a_system_with_no_kernel_modules_is_not_a_finished_install(tmp_path):
    t, shims = _finished_install(tmp_path, None)
    shutil.rmtree(t / "usr")
    r = sh(f"target={t}; encrypt=no; verify", path_first=shims, check=False)
    assert r.returncode != 0 and "no kernel modules" in r.stderr


# -- the install a person runs by hand ------------------------------------------------------------------

def _disk(path, size_gb, model, children=(), tran="sata"):
    return {"path": path, "size": size_gb * 1000**3, "type": "disk", "tran": tran, "model": model,
            "children": list(children)}


def _part(path, fstype, partlabel="", mount=None):
    return {"path": path, "type": "part", "fstype": fstype, "partlabel": partlabel, "mountpoints": [mount]}


DISKS = {"blockdevices": [
    _disk("/dev/nvme0n1", 512, "SAMSUNG MZVLB512", tran="nvme",
          children=[_part("/dev/nvme0n1p1", "vfat", "EFI system partition"), _part("/dev/nvme0n1p3", "ntfs", "Basic data partition")]),
    _disk("/dev/sda", 1000, "WD Blue", children=[_part("/dev/sda1", "vfat", "EFI"), _part("/dev/sda2", "crypto_LUKS", "Bombadil")]),
    _disk("/dev/sdb", 32, "Flash", tran="usb", children=[_part("/dev/sdb1", "iso9660", mount="/run/archiso/bootmnt")]),
]}


def guided_session(tmp_path, answers, disks=DISKS, then=""):
    """Run guided() with `answers` typed, and print what it decided: the plan, and the password as read back."""
    shims = tmp_path / "shims"
    shim(shims, "lsblk", f"cat <<'EOF'\n{json.dumps(disks)}\nEOF")
    zones = tmp_path / "zoneinfo"
    for z in ("UTC", "Europe/Lisbon"):
        (zones / z).parent.mkdir(parents=True, exist_ok=True)
        (zones / z).write_bytes(b"TZif2" + b"\0" * 40)
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("quiet\n")
    script = ('guided; echo "PLAN=$(cat "$plan_file")"; echo "MODE=$mode"; '
              'if [[ -n "$pw_fd" ]]; then IFS= read -r -u "$pw_fd" p; echo "PW=$p"; else echo "PW=none"; fi; ' + then)
    return sh(script, env={"BOMBADIL_ZONEINFO": str(zones), "BOMBADIL_CMDLINE_FILE": str(cmdline)},
              path_first=shims, input=answers, check=False)


def _decided(r):
    assert r.returncode == 0, r.stdout + r.stderr
    got = dict(line.split("=", 1) for line in r.stdout.splitlines() if line.startswith(("PLAN=", "MODE=", "PW=")))
    return json.loads(got["PLAN"]), got["MODE"], got["PW"]


def test_a_new_encrypted_install_asks_for_disk_password_zone_and_name_and_writes_the_plan(tmp_path):
    answers = "1\ncorrect horse\ncorrect horse\nEurope/Lisbon\nspare-laptop\n/dev/nvme0n1\n"
    plan, mode, pw = _decided(guided_session(tmp_path, answers))
    assert plan == {"disk": "/dev/nvme0n1", "mode": "fresh", "timezone": "Europe/Lisbon", "timezone_source": "chosen",
                    "hostname": "spare-laptop", "encrypt": True}
    assert mode == "fresh" and pw == "correct horse"


def test_the_plan_a_person_makes_is_one_the_plan_checker_accepts(tmp_path):
    answers = "1\nhunter22\nhunter22\nEurope/Lisbon\nspare-laptop\n/dev/nvme0n1\n"
    plan, _, _ = _decided(guided_session(tmp_path, answers))
    from bombadil import installplan
    zones = tmp_path / "zoneinfo"
    checked = installplan.validate(plan, carry_list=installplan.read_carry_list(ROOT / "install/carry.list"), zoneinfo=zones)
    assert checked["encrypt"] is True and checked["hostname"] == "spare-laptop"


def test_the_stick_is_listed_but_not_offered_and_the_disks_say_what_is_on_them(tmp_path):
    r = guided_session(tmp_path, "1\npw-one-two\npw-one-two\nUTC\n\n/dev/nvme0n1\n")
    assert "1)  /dev/nvme0n1  512 GB SAMSUNG MZVLB512: Windows is on it" in r.stdout
    assert "2)  /dev/sda  1.0 TB WD Blue: Bombadil is on it, encrypted" in r.stdout
    assert "/dev/sdb  32 GB Flash (USB): not offered, it is a Bombadil install image" in r.stdout


def test_the_time_zone_left_at_the_live_default_is_not_taken_as_a_choice(tmp_path):
    plan, _, _ = _decided(guided_session(tmp_path, "1\npw-one-two\npw-one-two\n\n\n/dev/nvme0n1\n"))
    assert (plan["timezone"], plan["timezone_source"]) == ("UTC", "unset")
    assert plan["hostname"]   # the computer's own suggestion, accepted


def test_no_password_needs_a_second_yes_and_then_means_no_encryption(tmp_path):
    answers = "1\n\nno\npw-one-two\npw-one-two\nUTC\nhost1\n/dev/nvme0n1\n"   # empty, "no", then a real one
    plan, _, pw = _decided(guided_session(tmp_path, answers))
    assert plan["encrypt"] is True and pw == "pw-one-two"
    plan, _, pw = _decided(guided_session(tmp_path, "1\n\nyes\nUTC\nhost1\n/dev/nvme0n1\n"))
    assert plan["encrypt"] is False and pw == "none"


def test_a_password_typed_twice_must_match_and_be_plain_text_the_boot_screen_can_read(tmp_path):
    answers = "1\nfirst-one\nsecond-one\nüberpass\nüberpass\nright-one\nright-one\nUTC\nhost1\n/dev/nvme0n1\n"
    r = guided_session(tmp_path, answers)
    plan, _, pw = _decided(r)
    assert pw == "right-one" and "different" in r.stdout and "ordinary symbols" in r.stdout


def test_a_bad_disk_number_and_a_bad_zone_and_a_bad_name_are_asked_again(tmp_path):
    answers = "7\nx\n1\npw-one-two\npw-one-two\nMars/Olympus\nUTC\nBad Name\ngood-name\n/dev/nvme0n1\n"
    r = guided_session(tmp_path, answers)
    plan, _, _ = _decided(r)
    assert plan["disk"] == "/dev/nvme0n1" and plan["hostname"] == "good-name"
    assert "from 1 to 2" in r.stdout + r.stderr and "not a time zone" in r.stdout


def test_the_disk_is_only_erased_after_its_name_is_typed(tmp_path):
    r = guided_session(tmp_path, "1\npw-one-two\npw-one-two\nUTC\nhost1\nyes\n")
    assert r.returncode != 0 and "nothing was changed" in r.stderr and "PLAN=" not in r.stdout
    assert "ERASES everything on /dev/nvme0n1 (512 GB SAMSUNG MZVLB512: Windows is on it)" in r.stdout


def test_a_disk_that_already_has_bombadil_can_be_refreshed_with_its_own_password_and_no_other_questions(tmp_path):
    answers = "2\n1\nthe old password\n/dev/sda\n"
    plan, mode, pw = _decided(guided_session(tmp_path, answers))
    assert plan == {"disk": "/dev/sda", "mode": "refresh"}
    assert mode == "refresh" and pw == "the old password"


def test_a_disk_that_has_bombadil_can_also_be_erased_instead(tmp_path):
    answers = "2\n2\npw-one-two\npw-one-two\nUTC\nhost1\n/dev/sda\n"
    plan, mode, _ = _decided(guided_session(tmp_path, answers))
    assert mode == "fresh" and plan["encrypt"] is True


def test_with_no_disk_to_offer_it_says_so_and_changes_nothing(tmp_path):
    only_stick = {"blockdevices": [DISKS["blockdevices"][2]]}
    r = guided_session(tmp_path, "", disks=only_stick)
    assert r.returncode != 0 and "no disk here" in r.stderr


def test_a_single_disk_is_chosen_by_pressing_enter(tmp_path):
    one = {"blockdevices": [DISKS["blockdevices"][0]]}
    plan, _, _ = _decided(guided_session(tmp_path, "\npw-one-two\npw-one-two\nUTC\nhost1\n/dev/nvme0n1\n", disks=one))
    assert plan["disk"] == "/dev/nvme0n1"
