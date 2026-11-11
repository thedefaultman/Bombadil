"""The bombadil package: what it holds, what it enables, what it changes in files other packages own.

Nothing here builds a package or needs pacman: the PKGBUILD's package() is run in bash on a staged tree, the scriptlet
and the migrate steps on a fake root with a fake systemctl. A real build and install is checked in a container by hand.
"""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from payload import PAYLOAD, ROOT

PKG = ROOT / "packaging/bombadil"
PKGBUILD = PKG / "PKGBUILD"
SCRIPTLET = PKG / "bombadil.install"
BUILD_PACKAGE = ROOT / "scripts/build-package.sh"
CONF_SCRIPT = ROOT / "scripts/pacman-conf-with-repo.sh"
MIGRATE = PAYLOAD / "usr/share/bombadil/migrate"
AIROOTFS = ROOT / "iso/airootfs"
UNITS = PAYLOAD / "usr/lib/systemd"
VERSION_LINE = "2026.11.12 (abc1234)"


def bash(script: str, *args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    full = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", **(env or {})}
    return subprocess.run(["bash", "-c", script, "_", *args], env=full, capture_output=True, text=True, check=check)


def git_mode(path: Path) -> str:
    """The mode git holds for a tracked file; the file's own when git does not know it (not committed yet)."""
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "--", str(path)], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.split()[0]
    return "100755" if os.access(path, os.X_OK) else "100644"


def kinds(tree: Path) -> dict[str, str]:
    """Every path under tree as "dir" or "file" (a link is a file, whatever it points at)."""
    return {p.relative_to(tree).as_posix(): "dir" if p.is_dir() and not p.is_symlink() else "file"
            for p in tree.rglob("*")}


def stage(tmp_path: Path) -> Path:
    """The staging tree the way scripts/build-package.sh lays it out."""
    dest = tmp_path / "stage"
    bash('source "$1"; stage_tree "$2" "$3"', str(BUILD_PACKAGE), str(dest), VERSION_LINE)
    return dest


def pkgbuild_depends() -> list[str]:
    out = bash('source "$1"; printf "%s\\n" "${depends[@]}"', str(PKGBUILD), env={"BOMBADIL_PKGVER": "1"}).stdout
    return out.split()


def has_install_section(path: Path) -> bool:
    return bool(re.search(r"^\[Install\]", path.read_text(), re.M))


def enable_list(kind: str) -> list[str]:
    lines = (PAYLOAD / f"usr/share/bombadil/enable.{kind}").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


# -- one owner for every path --------------------------------------------------------------------------------------

def test_no_path_is_in_both_the_image_tree_and_the_package(tmp_path):
    # mkarchiso copies airootfs first and pacstrap installs the package after: a path in both is a file pacman refuses
    # to overwrite ("exists in filesystem") and the build stops. Only a directory may be in both.
    image = kinds(AIROOTFS)
    for name, tree in (("packaging/bombadil/root", kinds(PAYLOAD)), ("the staged package", kinds(stage(tmp_path)))):
        clash = sorted(p for p in image.keys() & tree.keys() if not (image[p] == "dir" and tree[p] == "dir"))
        assert clash == [], f"in iso/airootfs and in {name}: {clash}"


def test_the_image_tree_holds_only_what_the_stick_alone_needs():
    # What moved into the package must not come back: its units, its commands, its desktop files and its skel.
    shipped = sorted(p for p, kind in kinds(AIROOTFS).items() if kind == "file")
    assert "etc/systemd/system/bombadil-live.service" in shipped and "etc/systemd/system/pacman-init.service" in shipped
    assert [p for p in shipped if re.search(r"bombadil-(brain|connect|smoke|mail|browserd|agentd|shell)|usr/local|etc/skel|"
                                            r"etc/greetd|etc/sudoers|usr/share/(icons|pixmaps|applications)", p)] == []
    # The image's own /etc/environment points at the command where the package puts it.
    assert "BROWSER=/usr/bin/bombadil-browser" in (AIROOTFS / "etc/environment").read_text().splitlines()


def test_nothing_the_package_ships_names_usr_local():
    # /usr/local is no package's: the commands are in /usr/bin. (The migrate step that fixes an old line names it.)
    texts = [p for p in PAYLOAD.rglob("*") if p.is_file() and not p.is_symlink() and MIGRATE not in p.parents]
    assert [p.relative_to(ROOT).as_posix() for p in texts if "/usr/local" in p.read_text(errors="replace")] == []


# -- the dependencies ------------------------------------------------------------------------------------------------

def test_the_package_depends_only_on_what_the_image_lists_and_the_image_lists_the_package():
    packages = {ln.strip() for ln in (ROOT / "iso/packages.x86_64").read_text().splitlines()
                if ln.strip() and not ln.startswith("#")}
    depends = pkgbuild_depends()
    assert depends and len(depends) == len(set(depends))
    assert [d for d in depends if d not in packages] == []
    assert "bombadil" in packages
    # sudo first: its package owns /etc/sudoers.d (mode 750), and this one puts a file in it.
    assert depends[0] == "sudo"


def test_the_pkgbuild_says_what_the_package_is():
    text = PKGBUILD.read_text()
    assert re.search(r"^pkgname=bombadil$", text, re.M) and re.search(r"^arch=\(any\)$", text, re.M)
    assert "pkgver=${BOMBADIL_PKGVER:?" in text and "pkgrel=${BOMBADIL_PKGREL:-1}" in text
    assert re.search(r"^source=\(\)$", text, re.M) and "install=bombadil.install" in text
    assert "options=(!strip !debug !docs !emptydirs)" in text
    # The two files a person may edit and an update must not overwrite.
    assert "backup=(etc/bombadil/config.toml etc/pacman.d/bombadil.conf)" in text
    assert (ROOT / "LICENSE").read_text().startswith("MIT License") and re.search(r"^license=\(MIT\)$", text, re.M)
    assert SCRIPTLET.is_file()


# -- package(): what the package holds, from the staged tree ------------------------------------------------------------

def run_package(tmp_path: Path, *, restrictive: bool = False) -> Path:
    stage_dir = stage(tmp_path)
    if restrictive:
        # A checkout made under umask 077 (and a staging tree that inherits it): nobody but the owner could read it.
        for p in [stage_dir, *stage_dir.rglob("*")]:
            if p.is_symlink():
                continue
            p.chmod(0o700 if p.is_dir() or p.stat().st_mode & 0o100 else 0o600)
    pkgdir = tmp_path / "pkgdir"
    pkgdir.mkdir()
    bash('pkgdir="$1"; BOMBADIL_STAGE="$2"; source "$3"; package', str(pkgdir), str(stage_dir), str(PKGBUILD),
         env={"BOMBADIL_PKGVER": "2026.11.12"})
    return pkgdir


@pytest.mark.parametrize("restrictive", [False, True])
def test_package_makes_a_link_in_usr_bin_for_every_command_and_sets_every_mode(tmp_path, restrictive):
    pkgdir = run_package(tmp_path, restrictive=restrictive)
    commands = sorted(p.name for p in (ROOT / "bin").iterdir() if p.is_file())
    assert commands and {"agentd", "bombadil", "bombadil-brain", "bombadil-connect", "bombadil-mail"} <= set(commands)
    for name in commands:
        link = pkgdir / "usr/bin" / name
        assert link.is_symlink() and os.readlink(link) == f"../share/bombadil/bin/{name}", name
        # The link resolves to the file in the tree, which launchers rely on to find src/ beside it.
        assert link.resolve() == (pkgdir / "usr/share/bombadil/bin" / name).resolve() and os.access(link, os.X_OK)
    # The package's own scripts are files in /usr/bin, not links.
    own = sorted(p.name for p in (PAYLOAD / "usr/bin").iterdir())
    assert own and all((pkgdir / "usr/bin" / n).is_file() and not (pkgdir / "usr/bin" / n).is_symlink() for n in own)
    assert sorted(p.name for p in (pkgdir / "usr/bin").iterdir()) == sorted(commands + own)
    # Modes: sudo's file is 440; directories 755; nothing is writable by group or others; what had x keeps it.
    assert stat.S_IMODE((pkgdir / "etc/sudoers.d/bombadil").stat().st_mode) == 0o440
    modes = {p: stat.S_IMODE(p.lstat().st_mode) for p in pkgdir.rglob("*") if not p.is_symlink()}
    # (sudo's folder keeps sudo's 750, so pacman has no mode to differ on when the package's entry for it arrives.)
    assert [p for p, m in modes.items() if p.is_dir() and m != 0o755 and p != pkgdir / "etc/sudoers.d"] == []
    assert modes[pkgdir / "etc/sudoers.d"] == 0o750
    assert [p for p, m in modes.items() if p.is_file() and m & 0o022] == []
    others_read = [p for p, m in modes.items() if p.is_file() and p.name != "bombadil" and not m & 0o004]
    assert others_read == [], "a file the user's own programs cannot read"
    for name in own:
        assert modes[pkgdir / "usr/bin" / name] == 0o755, name
    for step in ("run", "001-pacman-repo.sh", "002-os-release-logo.sh", "003-browser-env.sh"):
        assert modes[pkgdir / "usr/share/bombadil/migrate" / step] == 0o755, step
    assert modes[pkgdir / "etc/grub.d/000_bombadil_screen"] == 0o755
    assert modes[pkgdir / "usr/share/bombadil/src/bombadil/paths.py"] == 0o644
    # The tree is where paths.share_dir() looks, with the stamp the commands print.
    assert (pkgdir / "usr/share/bombadil/VERSION").read_text() == VERSION_LINE + "\n"
    assert (pkgdir / "usr/share/bombadil/share/hypr/bombadil.lua").is_file()
    assert (pkgdir / "usr/share/bombadil/packages.x86_64").read_text() == (ROOT / "iso/packages.x86_64").read_text()


def test_the_pkgbuild_sets_the_sudoers_file_to_440_because_git_cannot_hold_it():
    assert re.search(r'^\s*chmod 440 "\$pkgdir/etc/sudoers\.d/bombadil"$', PKGBUILD.read_text(), re.M)
    assert git_mode(PAYLOAD / "etc/sudoers.d/bombadil") == "100644"


def test_the_staged_tree_leaves_out_what_a_running_system_writes_and_what_is_not_the_package(tmp_path):
    # Bytecode written by agentd or the installer would be a file no package owns, and the next upgrade would stop at it.
    staged = stage(tmp_path)
    names = [p.relative_to(staged).as_posix() for p in staged.rglob("*")]
    assert [n for n in names if "__pycache__" in n or n.endswith(".pyc")] == []
    top = sorted(p.name for p in (staged / "usr/share/bombadil").iterdir())
    assert {"bin", "src", "shell", "share", "install", "packages.x86_64", "VERSION", "enable.system", "enable.user",
            "greetd", "migrate"} <= set(top)
    # Not the tests, the docs, the build files, nor the image profile.
    assert not {"tests", "docs", "scripts", "iso", "packaging", "out", ".git"} & set(top)


# -- build-package.sh and build-iso.sh -------------------------------------------------------------------------------

def test_the_build_scripts_parse_and_pass_shellcheck():
    scripts = [BUILD_PACKAGE, CONF_SCRIPT, ROOT / "scripts/build-iso.sh", ROOT / "scripts/build-in-container.sh"]
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
        assert git_mode(script) == "100755", script.name
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-x", *map(str, scripts)], check=True)


def test_build_package_builds_as_a_throwaway_user_from_a_clean_cache_into_a_repository_of_one():
    text = BUILD_PACKAGE.read_text()
    assert 'runuser -u bombadil-build -- "${build[@]}" makepkg --nodeps --noconfirm --force --nocolor' in text
    assert "useradd --system --no-create-home" in text and "$EUID -eq 0" in text
    # A cached package of the same name would be used by pacman in place of this build.
    assert "rm -f /var/cache/pacman/pkg/bombadil-*.pkg.tar.*" in text
    # Only this build is in the repository, and the cache user can read it.
    assert 'rm -f "$repo"/bombadil-*.pkg.tar.* "$repo"/bombadil.db* "$repo"/bombadil.files*' in text
    assert "repo-add" in text and 'chmod 755 "$repo"' in text
    # The commit and the release number come in from outside (git cannot read a checkout a container sees).
    for var in ("BOMBADIL_COMMIT", "BOMBADIL_PKGVER", "BOMBADIL_PKGREL", "SOURCE_DATE_EPOCH", "WORK"):
        assert var in text, var
    assert "--exclude=__pycache__" in text and "--exclude='*.pyc'" in text


def test_the_iso_build_makes_the_package_and_reads_it_back_from_the_image_tree():
    text = (ROOT / "scripts/build-iso.sh").read_text()
    # No tree copy and no link loop of its own any more.
    assert "usr/local/bin" not in text and "ln -sfn" not in text and 'cp -a "$root/bin"' not in text
    assert text.count("export SOURCE_DATE_EPOCH=") == 1
    assert text.index("export SOURCE_DATE_EPOCH=") < text.index('"$root/scripts/build-package.sh" "$work/repo"')
    assert '"$root/scripts/pacman-conf-with-repo.sh" "$root/iso/pacman.conf" "$profile/pacman.conf" "$work/repo"' in text
    assert 'cp -a "$work/repo" "$profile/airootfs/var/lib/bombadil/repo"' in text
    assert 'echo bombadil >> "$profile/packages.x86_64"' in text
    # A work directory that is reused skips pacstrap, so the build tree is removed first.
    assert text.index('rm -rf "$work/build"') < text.index("mkarchiso -v")
    # After mkarchiso, and before the image is read back, the tree is asked for what the package should have left.
    assert text.index("mkarchiso -v") < text.index("verify_package\n") < text.index("verify_image\necho")
    for what in ("enable.$kind", "-path \"*.wants/$unit\"", "/usr/bin/agentd", "[bombadil]", "$work/repo"):
        assert what in text, what
    # iso/pacman.conf is never written: the build edits a copy in the profile.
    assert not re.search(r'(>|sed -i[^\n]*|tee)\s*"?\$root/iso/pacman\.conf', text)
    container = (ROOT / "scripts/build-in-container.sh").read_text()
    assert "archiso git nodejs npm fakeroot binutils" in container and "BOMBADIL_PKGREL" in container


def test_the_image_pacman_conf_gets_the_repository_above_core_and_the_source_file_never_does(tmp_path):
    source = (ROOT / "iso/pacman.conf").read_text()
    # The checked-in file knows nothing of the repository, which is a path on the build machine.
    assert "[bombadil]" not in source and "file://" not in source and "TrustAll" not in source
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "pacman.conf"
    subprocess.run([str(CONF_SCRIPT), str(ROOT / "iso/pacman.conf"), str(out), str(repo)], check=True)
    lines = out.read_text().splitlines()
    section = lines.index("[bombadil]")
    assert section < lines.index("[core]") < lines.index("[extra]")
    block = lines[section:lines.index("[core]")]
    assert block[:3] == ["[bombadil]", "SigLevel = Optional TrustAll", f"Server = file://{repo}"]
    # Everything else is the source file, in order.
    assert [ln for ln in lines if ln not in block] == source.splitlines()
    # The same file again changes nothing about the source, and a file with no [core] is refused.
    assert (ROOT / "iso/pacman.conf").read_text() == source
    bad = tmp_path / "bad.conf"
    bad.write_text("[options]\nSigLevel = Required\n")
    r = subprocess.run([str(CONF_SCRIPT), str(bad), str(tmp_path / "bad.out"), str(repo)], capture_output=True, text=True)
    assert r.returncode != 0 and "no [core] section" in r.stderr
    if shutil.which("pacman-conf"):
        got = subprocess.run(["pacman-conf", "--config", str(out), "-r", "bombadil", "Server"], capture_output=True,
                             text=True, check=True).stdout.split()
        assert got == [f"file://{repo}"]


# -- the enable lists and the units -----------------------------------------------------------------------------------

def test_the_enable_lists_name_units_that_exist_have_an_install_section_and_leave_out_the_ones_started_elsewhere():
    for kind in ("system", "user"):
        names = enable_list(kind)
        assert names and len(names) == len(set(names)), kind
        for name in names:
            assert re.fullmatch(r"[\w@.-]+\.service", name), name
            unit = UNITS / kind / name
            assert unit.is_file(), f"{name} is listed but there is no {kind} unit of that name"
            assert has_install_section(unit), f"{name} has no [Install] section, so enabling it does nothing"
    # Hyprland starts the daemon and the bar; the stick's own units are not the installed system's.
    listed = set(enable_list("system")) | set(enable_list("user"))
    assert not {"bombadil-agentd.service", "bombadil-shell.service", "bombadil-live.service", "pacman-init.service"} & listed
    assert not [n for n in ("bombadil-agentd", "bombadil-shell") if has_install_section(UNITS / "user" / f"{n}.service")]
    # And the other way round: a unit with an [Install] section that nobody lists would never be turned on.
    for kind in ("system", "user"):
        wanted = sorted(p.name for p in (UNITS / kind).glob("*.service") if has_install_section(p))
        assert wanted == sorted(enable_list(kind)), kind
    # The image keeps only its own two units and their links; none of the package's.
    assert not [p for p in (AIROOTFS / "etc/systemd").rglob("bombadil-*") if p.name not in ("bombadil-live.service",)]


def test_every_unit_starts_a_command_the_package_links_or_ships():
    commands = {p.name for p in (ROOT / "bin").iterdir() if p.is_file()} | {p.name for p in (PAYLOAD / "usr/bin").iterdir()}
    # greetd's own command, started by the drop-in: another package's, which this one depends on.
    foreign = {"greetd": "greetd"}
    depends = set(pkgbuild_depends())
    seen = []
    for unit in [*UNITS.rglob("*.service"), *UNITS.rglob("*.conf")]:
        for line in unit.read_text().splitlines():
            m = re.match(r"ExecStart=[-@:+!]*(\S+)", line)
            if not m or not m.group(1):
                continue
            seen.append(unit.name)
            exe = m.group(1)
            assert exe.startswith("/usr/bin/"), f"{unit.name} starts {exe}"
            name = exe.removeprefix("/usr/bin/")
            assert name in commands or foreign.get(name) in depends, f"{unit.name} starts {exe}, which nothing provides"
    assert {"bombadil-connect.service", "bombadil-brain-watch.service", "bombadil-smoke.service", "bombadil-agentd.service",
            "bombadil-shell.service", "bombadil.conf"} <= set(seen)


def test_the_greetd_drop_in_runs_greetd_with_the_packages_config_after_clearing_the_one_it_has():
    drop_in = UNITS / "system/greetd.service.d/bombadil.conf"
    lines = [ln for ln in drop_in.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    # An empty ExecStart= first, or systemd would refuse a second command for a service that has one.
    assert lines == ["[Service]", "ExecStart=", "ExecStart=/usr/bin/greetd -c /usr/share/bombadil/greetd/config.toml"]
    assert (PAYLOAD / "usr/share/bombadil/greetd/config.toml").is_file()
    assert not (AIROOTFS / "etc/greetd").exists() and not (PAYLOAD / "etc/greetd").exists()


def test_tmpfiles_makes_the_linger_file_and_sysusers_the_connection_account():
    lines = [ln for ln in (PAYLOAD / "usr/lib/tmpfiles.d/bombadil.conf").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    assert lines == ["d /var/lib/systemd/linger 0755 root root -", "f /var/lib/systemd/linger/user - - - -"]
    sysusers = [ln.split() for ln in (PAYLOAD / "usr/lib/sysusers.d/bombadil-connect.conf").read_text().splitlines()
                if ln.strip() and not ln.startswith("#")]
    assert [s[:3] for s in sysusers] == [["u", "bombadil-connect", "-"]]


def test_the_repository_file_lists_the_laptops_host_first_and_the_copy_on_the_image_second():
    lines = [ln for ln in (PAYLOAD / "etc/pacman.d/bombadil.conf").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    assert lines == ["SigLevel = Optional TrustAll", "Server = http://10.0.2.2:8123", "Server = file:///var/lib/bombadil/repo"]
    assert (PAYLOAD / "etc/pacman.d/bombadil.conf").read_text().startswith("#"), "a comment says what the file is"


def test_the_hook_runs_the_migrate_steps_after_a_change_to_a_package_that_owns_a_file_they_edit():
    text = (PAYLOAD / "usr/share/libalpm/hooks/bombadil-migrate.hook").read_text()
    sections: list[tuple[str, dict[str, list[str]]]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            sections.append((line.strip("[]"), {}))
        else:
            key, _, value = line.partition("=")
            sections[-1][1].setdefault(key.strip(), []).append(value.strip())
    assert [name for name, _ in sections] == ["Trigger", "Trigger", "Action"]
    package, path, action = (body for _, body in sections)
    # pacman.conf is pacman's, /etc/environment is pam's, os-release is filesystem's; bombadil's own change re-runs them.
    assert package == {"Operation": ["Install", "Upgrade"], "Type": ["Package"],
                       "Target": ["bombadil", "pacman", "filesystem", "pam"]}
    assert path == {"Operation": ["Install", "Upgrade"], "Type": ["Path"], "Target": ["usr/lib/os-release"]}
    assert action["When"] == ["PostTransaction"] and action["Exec"] == ["/usr/share/bombadil/migrate/run"]
    assert action["Description"][0]
    assert (MIGRATE / "run").is_file() and git_mode(MIGRATE / "run") == "100755"


# -- the migrate steps ---------------------------------------------------------------------------------------------------

STOCK_PACMAN_CONF = (ROOT / "iso/pacman.conf").read_text()
ARCH_OS_RELEASE = ('NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\nBUILD_ID=rolling\nANSI_COLOR="38;2;23;147;209"\n'
                   'HOME_URL="https://archlinux.org/"\nLOGO=archlinux-logo\n')


def step(root: Path, name: str, *, migrate: Path = MIGRATE) -> subprocess.CompletedProcess:
    return bash('bash "$1"', str(migrate / name), env={"BOMBADIL_ROOT": str(root)}, check=False)


def fake_root(tmp_path: Path, *, conf=STOCK_PACMAN_CONF, os_release=ARCH_OS_RELEASE,
              environment="# Every link\nBROWSER=/usr/local/bin/bombadil-browser\n") -> Path:
    root = tmp_path / "root"
    for d in ("etc", "usr/lib"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "etc/pacman.conf").write_text(conf)
    (root / "etc/pacman.conf").chmod(0o640)
    (root / "usr/lib/os-release").write_text(os_release)
    (root / "usr/lib/os-release").chmod(0o644)
    (root / "etc/environment").write_text(environment)
    (root / "etc/environment").chmod(0o600)
    return root


def only_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_step_001_puts_the_repository_above_core_once_and_keeps_the_files_mode(tmp_path):
    root = fake_root(tmp_path)
    conf = root / "etc/pacman.conf"
    before_files = only_files(root)
    first = step(root, "001-pacman-repo.sh")
    assert (first.returncode, first.stdout.strip()) == (0, "changed")
    lines = conf.read_text().splitlines()
    at = lines.index("[bombadil]")
    assert lines[at:at + 4] == ["[bombadil]", "Include = /etc/pacman.d/bombadil.conf", "", "[core]"]
    # Only those three lines were added: the rest is the file as it was, in order.
    assert lines[:at] + lines[at + 3:] == STOCK_PACMAN_CONF.splitlines()
    assert stat.S_IMODE(conf.stat().st_mode) == 0o640
    # Again: nothing to do, and not one byte moves. No temporary file is left in /etc.
    snapshot = conf.read_bytes()
    second = step(root, "001-pacman-repo.sh")
    assert (second.returncode, second.stdout.strip()) == (0, "already") and conf.read_bytes() == snapshot
    assert only_files(root) == before_files


def test_step_001_refuses_a_pacman_conf_with_no_core_and_leaves_it_as_it_is(tmp_path):
    root = fake_root(tmp_path, conf="[options]\nHoldPkg = pacman\n\n[custom]\nServer = https://example.org\n")
    conf = root / "etc/pacman.conf"
    before = conf.read_bytes()
    r = step(root, "001-pacman-repo.sh")
    assert r.returncode == 1 and "no [core] section" in r.stdout + r.stderr
    assert conf.read_bytes() == before and stat.S_IMODE(conf.stat().st_mode) == 0o640
    assert [p for p in (root / "etc").iterdir() if p.name.startswith("pacman.conf.")] == []
    # A file that is not there is refused the same way, and a repository the person already named is theirs.
    (root / "etc/pacman.conf").unlink()
    assert step(root, "001-pacman-repo.sh").returncode == 1
    (root / "etc/pacman.conf").write_text("[options]\n\n[bombadil]\nServer = https://example.org\n\n[core]\nInclude = x\n")
    assert step(root, "001-pacman-repo.sh").stdout.strip() == "already"


def test_step_002_gives_the_system_bombadils_logo_once_and_leaves_a_logo_the_person_chose(tmp_path):
    root = fake_root(tmp_path)
    file = root / "usr/lib/os-release"
    first = step(root, "002-os-release-logo.sh")
    assert (first.returncode, first.stdout.strip()) == (0, "changed")
    assert file.read_text() == ARCH_OS_RELEASE.replace("LOGO=archlinux-logo", "LOGO=bombadil")
    assert "ID=arch\n" in file.read_text() and "ID_LIKE" not in file.read_text()
    assert stat.S_IMODE(file.stat().st_mode) == 0o644
    snapshot = file.read_bytes()
    assert step(root, "002-os-release-logo.sh").stdout.strip() == "already" and file.read_bytes() == snapshot
    # No LOGO line at all (and no newline at the end): one is added.
    file.write_text('NAME="Arch Linux"\nID=arch')
    assert step(root, "002-os-release-logo.sh").stdout.strip() == "changed"
    assert file.read_text() == 'NAME="Arch Linux"\nID=arch\nLOGO=bombadil\n'
    # A logo that is neither Arch's nor ours was set on purpose.
    file.write_text("NAME=x\nLOGO=my-own\n")
    r = step(root, "002-os-release-logo.sh")
    assert r.returncode == 0 and r.stdout.startswith("already") and file.read_text() == "NAME=x\nLOGO=my-own\n"
    # A real file in /etc takes precedence, as systemd reads it first; a link there leaves /usr/lib/os-release.
    (root / "etc/os-release").write_text(ARCH_OS_RELEASE)
    assert step(root, "002-os-release-logo.sh").stdout.strip() == "changed"
    assert "LOGO=bombadil" in (root / "etc/os-release").read_text() and file.read_text() == "NAME=x\nLOGO=my-own\n"
    (root / "etc/os-release").unlink()
    (root / "etc/os-release").symlink_to("../usr/lib/os-release")
    file.write_text(ARCH_OS_RELEASE)
    assert step(root, "002-os-release-logo.sh").stdout.strip() == "changed"
    assert (root / "etc/os-release").is_symlink() and "LOGO=bombadil" in file.read_text()


def test_step_003_points_the_browser_variable_at_the_new_command_and_touches_no_other_line(tmp_path):
    root = fake_root(tmp_path)
    env = root / "etc/environment"
    first = step(root, "003-browser-env.sh")
    assert (first.returncode, first.stdout.strip()) == (0, "changed")
    assert env.read_text() == "# Every link\nBROWSER=/usr/bin/bombadil-browser\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert step(root, "003-browser-env.sh").stdout.strip() == "already"
    # Only the exact line: a variable with more on it, or another variable, is somebody's own.
    own = "BROWSER=/usr/local/bin/bombadil-browser --x\nEDITOR=/usr/local/bin/bombadil-browser\nBROWSER=firefox\n"
    env.write_text(own)
    assert step(root, "003-browser-env.sh").stdout.strip() == "already" and env.read_text() == own
    env.unlink()
    assert step(root, "003-browser-env.sh").stdout.strip() == "already"


def test_the_runner_runs_every_step_logs_each_and_always_exits_0(tmp_path):
    root = fake_root(tmp_path)
    r = bash('bash "$1"', str(MIGRATE / "run"), env={"BOMBADIL_ROOT": str(root)}, check=False)
    assert r.returncode == 0 and r.stderr == ""
    log = (root / "var/log/bombadil-migrate.log").read_text().splitlines()
    assert [ln.split()[1:] for ln in log] == [["001-pacman-repo", "changed"], ["002-os-release-logo", "changed"],
                                              ["003-browser-env", "changed"]]
    assert all(re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} ", ln) for ln in log)
    # A second run changes nothing and says so.
    bash('bash "$1"', str(MIGRATE / "run"), env={"BOMBADIL_ROOT": str(root)})
    # (A step with nothing to do is not logged: the hook runs on every upgrade of the packages it watches.)
    log = (root / "var/log/bombadil-migrate.log").read_text().splitlines()
    assert len(log) == 3
    # A step that fails is logged with its words and does not stop the next, nor the runner's exit.
    steps = tmp_path / "migrate"
    shutil.copytree(MIGRATE, steps)
    (steps / "002-os-release-logo.sh").write_text("#!/usr/bin/env bash\necho 'no os-release here'\nexit 3\n")
    (steps / "004-after.sh").write_text("#!/usr/bin/env bash\ntouch \"$BOMBADIL_ROOT/ran-after\"\necho changed\n")
    root2 = fake_root(tmp_path / "second")
    r = bash('bash "$1"', str(steps / "run"), env={"BOMBADIL_ROOT": str(root2)}, check=False)
    assert r.returncode == 0
    assert r.stderr.strip().count("\n") == 0 and "002-os-release-logo failed" in r.stderr
    log = (root2 / "var/log/bombadil-migrate.log").read_text().splitlines()
    assert [ln.split(None, 2)[1] for ln in log] == ["001-pacman-repo", "002-os-release-logo", "003-browser-env", "004-after"]
    assert "FAILED rc=3 no os-release here" in log[1] and log[3].endswith("004-after changed")
    assert (root2 / "ran-after").exists() and "LOGO=bombadil" not in (root2 / "usr/lib/os-release").read_text()
    # A root with no log directory to write is not a reason to fail either.
    (root2 / "var/log").chmod(0o500)
    assert bash('bash "$1"', str(steps / "run"), env={"BOMBADIL_ROOT": str(root2)}, check=False).returncode == 0
    (root2 / "var/log").chmod(0o755)


def test_the_migrate_scripts_pass_shellcheck_and_run_with_pipefail_where_it_does_not_defeat_them():
    scripts = [MIGRATE / n for n in ("run", "001-pacman-repo.sh", "002-os-release-logo.sh", "003-browser-env.sh")]
    for script in scripts:
        text = script.read_text()
        assert text.startswith("#!/usr/bin/env bash\n")
        subprocess.run(["bash", "-n", str(script)], check=True)
        # The runner reads each step's status, so it must not stop on one: no -e there.
        assert "set -euo pipefail" in text or (script.name == "run" and "set -uo pipefail" in text), script.name
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-x", *map(str, scripts), str(SCRIPTLET)], check=True)


# -- the scriptlet ---------------------------------------------------------------------------------------------------------

def scriptlet_world(tmp_path: Path, fail: str = "") -> tuple[Path, dict]:
    root = tmp_path / "root"
    share = root / "usr/share/bombadil"
    share.mkdir(parents=True)
    for kind in ("system", "user"):
        shutil.copy(PAYLOAD / f"usr/share/bombadil/enable.{kind}", share)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "systemctl"
    fake.write_text('#!/bin/sh\necho "$*" >> "$SYSTEMCTL_LOG"\nfor a in "$@"; do\n'
                    '  case " $SYSTEMCTL_FAIL " in *" $a "*) exit 1 ;; esac\ndone\nexit 0\n')
    fake.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "BOMBADIL_ROOT": str(root), "SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
           "SYSTEMCTL_FAIL": fail}
    return root, env


def scriptlet(function: str, env: dict) -> subprocess.CompletedProcess:
    return bash('source "$1"; "$2"; echo "rc=$?"', str(SCRIPTLET), function, env=env)


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "systemctl.log"
    return log.read_text().splitlines() if log.exists() else []


def test_the_scriptlet_enables_each_listed_unit_once_and_never_starts_anything(tmp_path):
    root, env = scriptlet_world(tmp_path)
    first = scriptlet("post_install", env)
    assert first.stdout.strip() == "rc=0" and first.stderr == ""
    assert calls(tmp_path) == [f"enable {n}" for n in enable_list("system")] + [f"--global enable {n}" for n in enable_list("user")]
    ledger = (root / "var/lib/bombadil/enabled-units").read_text().splitlines()
    assert ledger == [f"system {n}" for n in enable_list("system")] + [f"user {n}" for n in enable_list("user")]
    # An upgrade enables nothing that is in the ledger, so a unit the person turned off afterwards stays off.
    (tmp_path / "systemctl.log").unlink()
    assert scriptlet("post_upgrade", env).stdout.strip() == "rc=0" and calls(tmp_path) == []
    # A unit the new version adds is enabled, once.
    with (root / "usr/share/bombadil/enable.user").open("a") as f:
        f.write("\n  # a comment\nbombadil-new.service\n")
    scriptlet("post_upgrade", env)
    scriptlet("post_upgrade", env)
    assert calls(tmp_path) == ["--global enable bombadil-new.service"]
    assert (root / "var/lib/bombadil/enabled-units").read_text().splitlines()[-1] == "user bombadil-new.service"
    # Nothing is started, stopped or restarted, here or in the file.
    assert not [c for c in calls(tmp_path) if re.search(r"start|stop|restart|--now|disable", c)]
    code = "\n".join(ln for ln in SCRIPTLET.read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"--now|systemctl[^\n]*\b(start|restart)\b|grub-mkconfig|migrate", code)


def test_a_unit_that_would_not_enable_stays_out_of_the_ledger_and_the_transaction_goes_on(tmp_path):
    root, env = scriptlet_world(tmp_path, fail="bombadil-mail.service")
    r = scriptlet("post_install", env)
    assert r.stdout.strip() == "rc=0"
    assert r.stderr.strip().splitlines() == ["bombadil: could not enable bombadil-mail.service (user); it stays off"]
    ledger = (root / "var/lib/bombadil/enabled-units").read_text().splitlines()
    assert "user bombadil-mail.service" not in ledger and len(ledger) == 5
    # The others were not held up by it, and the next upgrade tries it again.
    assert "--global enable bombadil-browserd.service" in calls(tmp_path)
    env["SYSTEMCTL_FAIL"] = ""
    (tmp_path / "systemctl.log").unlink()
    scriptlet("post_upgrade", env)
    assert calls(tmp_path) == ["--global enable bombadil-mail.service"]
    # No lists, no systemctl: still nothing that can fail a transaction.
    bare = tmp_path / "bare"
    bare.mkdir()
    r = scriptlet("post_install", {**env, "BOMBADIL_ROOT": str(bare), "PATH": "/usr/bin:/bin"})
    assert r.stdout.strip() == "rc=0"
    unwritable = tmp_path / "unwritable"
    (unwritable / "var").mkdir(parents=True)
    (unwritable / "var").chmod(0o500)
    assert scriptlet("post_install", {**env, "BOMBADIL_ROOT": str(unwritable)}).stdout.strip() == "rc=0"
    (unwritable / "var").chmod(0o755)


# -- the stamp and the record, and the stub (the pieces of other files this package rests on) ------------------------------

def test_the_home_stub_loads_the_packages_part_falls_back_and_leaves_the_rest_to_the_person():
    stub = (PAYLOAD / "etc/skel/.config/hypr/hyprland.lua").read_text()
    lines = stub.splitlines()
    assert lines[0].startswith("-- Bombadil's part comes from the system and follows its updates.")
    assert lines[1] == 'local ok = pcall(require, "/usr/share/bombadil/share/hypr/bombadil")'
    assert lines[-1].startswith("-- Your own lines go below.")
    assert "hyprland.lua.before-split" in stub and "dofile(old)" in stub
    # Nothing to fall back to: the daemon and the bar still start, with the environment the session has.
    start = next(ln for ln in lines if "hl.exec_cmd(" in ln)
    assert ("systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell") in start
    assert len(lines) < 30 and not [ln for ln in lines if ln.strip().startswith("hl.bind")]
    # The path it loads is the one the package installs.
    assert (ROOT / "share/hypr/bombadil.lua").is_file()
    # The module is the old file, with the defaults a person's own lines can override.
    module = (ROOT / "share/hypr/bombadil.lua").read_text()
    assert 'kb_layout = "us"' in module and module.index('hl.on("hyprland.start"') < module.index("hl.config(")


def test_every_command_in_bin_is_executable_in_git_so_the_links_in_usr_bin_run():
    files = [p for p in (ROOT / "bin").iterdir() if p.is_file()]
    assert files and [p.name for p in files if git_mode(p) != "100755"] == []
