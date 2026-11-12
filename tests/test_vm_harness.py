"""The VM test harness and the laptop tooling: the smoke script's modes, the boot entries that start them,
scripts/test-vm.sh, the build in a container, scripts/wsl-vm.sh and scripts/vm-tools.

None of it boots anything. The host script is run against stand-ins for QEMU, qemu-img and the
container runtime, and the smoke script's own functions are run in bash with the guest's commands
stood in for. A real run is MODE=update (and the others) of scripts/test-vm.sh, with a VM.
"""

import os
import re
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path

import pytest
from payload import PAYLOAD, ROOT

SMOKE = PAYLOAD / "usr/bin/bombadil-smoke"
TEST_VM = ROOT / "scripts/test-vm.sh"
WSL_VM = ROOT / "scripts/wsl-vm.sh"
IN_CONTAINER = ROOT / "scripts/build-in-container.sh"
BUILD_ISO = ROOT / "scripts/build-iso.sh"
ENTRIES = ROOT / "iso/efiboot/loader/entries"
VM_TOOLS = ROOT / "scripts/vm-tools"
SCRIPTS = [SMOKE, TEST_VM, WSL_VM, IN_CONTAINER]
RETIRED = "update" + "-in-" + "place"  # spelled in two parts: no file, this one included, may name it


def git_mode(path: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-s", "--", str(path)], capture_output=True, text=True
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.split()[0]
    return "100755" if os.access(path, os.X_OK) else "100644"


# -- the scripts parse, and are clean where the repo is -----------------------


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_scripts_parse_and_are_executable(script):
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert git_mode(script) == "100755", script.name


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_the_scripts_have_no_shellcheck_warnings(script):
    # Warnings and errors. The notes a script has always had (a single-quoted python program, `ls` for the
    # newest file) stay; build-in-container.sh has none at all.
    severity = [] if script == IN_CONTAINER else ["-S", "warning"]
    subprocess.run(["shellcheck", "-x", *severity, str(script)], check=True)


# -- the boot entries and the modes -------------------------------------------


def entry(path: Path) -> dict[str, str]:
    """A systemd-boot entry as {key: value}."""
    out = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition(" ")
        out[key] = value.strip()
    return out


def entry_files() -> list[Path]:
    return sorted(ENTRIES.glob("*.conf"))


def smoke_word(options: str) -> str | None:
    """The mode a boot entry asks the smoke script for: "" for the plain word, None when there is none."""
    m = re.search(r"(?:^| )bombadil\.smoke(?:=(\S+))?(?: |$)", options)
    return None if m is None else (m.group(1) or "")


def test_the_entries_are_numbered_without_gaps_and_sorted_by_their_names():
    files = entry_files()
    assert [f.name[:2] for f in files] == [f"{n:02d}" for n in range(1, len(files) + 1)]
    for f in files:
        assert entry(f)["sort-key"] == f.name[:2], f.name
    assert {smoke_word(entry(f)["options"]) for f in files} >= {
        None,
        "",
        "install",
        "install-encrypted",
        "refresh",
        "install-update",
    }


def test_entry_06_is_entry_03_with_another_mode_word():
    three, six = (
        entry(ENTRIES / "03-bombadil-install-test.conf"),
        entry(ENTRIES / "06-bombadil-install-update-test.conf"),
    )
    assert smoke_word(three["options"]) == "install" and smoke_word(six["options"]) == "install-update"
    assert (
        six["options"].replace("bombadil.smoke=install-update", "bombadil.smoke=install") == three["options"]
    )
    assert (six["linux"], six["initrd"]) == (three["linux"], three["initrd"])
    assert six["title"] != three["title"] and six["sort-key"] == "06"


def test_every_test_entry_keeps_its_kernel_output_for_the_serial_log():
    for f in entry_files():
        options = entry(f)["options"]
        if smoke_word(options) is None:
            continue
        assert "console=ttyS0,115200" in options and "quiet" not in options.split(), f.name


def test_a_release_build_drops_every_test_entry_and_keeps_the_first():
    import fnmatch

    glob = re.search(r'rm -f "\$profile"/efiboot/loader/entries/(\S+)', BUILD_ISO.read_text()).group(1)
    names = [f.name for f in entry_files()]
    assert [n for n in names if not fnmatch.fnmatch(n, glob)] == ["01-bombadil.conf"]


def modes_the_smoke_handles() -> set[str]:
    """Every word the smoke script compares its mode with."""
    text = SMOKE.read_text()
    words = set(re.findall(r'"\$mode" (?:==|!=) "?([a-z-]+)"?', text))
    for alternatives in re.findall(r'"\$mode" =~ \^\(([^)]*)\)\$', text):
        words |= set(alternatives.split("|")) - {""}
    return words


def test_every_mode_word_an_entry_or_the_host_names_is_handled_by_the_smoke():
    handled = modes_the_smoke_handles()
    named = {smoke_word(entry(f)["options"]) for f in entry_files()} - {None, ""}
    named |= set(re.findall(r"bombadil\.smoke=([a-z-]+)", TEST_VM.read_text()))
    assert named, "no mode words found"
    # The installed system starts as undo or update, which the smoke puts on the installed cmdline itself.
    installed = set(re.findall(r"installed_mode=([a-z]+)", SMOKE.read_text()))
    assert installed == {"undo", "update"}
    assert "bombadil.smoke=$installed_mode" in SMOKE.read_text()
    assert named <= handled | {"install-update"}, named - handled
    # install-update is install in the live part: it becomes install before any check looks at the mode.
    text = SMOKE.read_text()
    assert 'if [[ "$mode" == install-update ]]; then mode=install; installed_mode=update; fi' in text
    assert text.index("mode=install; installed_mode=update") < text.index("check user-exists")
    assert {"install", "install-encrypted", "refresh", "refreshed", "undo", "update"} <= handled
    # The header says each of them too.
    for word in named | {"undo", "update", "refreshed"}:
        assert f"# bombadil.smoke={word}" in text, word


def smoke_snippet(text: str) -> str:
    """The lines that turn the cmdline's word into the mode, ready to run in bash with $mode set."""
    start = text.index("installed_mode=undo\n")
    end = text.index("\n", text.index("round_trip=1"))
    return text[start : end + 1]


@pytest.mark.parametrize(
    "word,mode,installed,round_trip",
    [
        ("install-update", "install", "update", ""),
        ("install", "install", "undo", ""),
        ("update", "update", "undo", "1"),
        ("undo", "undo", "undo", "1"),
        ("refresh", "refresh", "undo", ""),
        ("", "", "undo", ""),
    ],
)
def test_install_update_is_install_and_the_round_trips_skip_the_fake_mail_and_connections(
    word, mode, installed, round_trip
):
    script = f'mode="{word}"\n{smoke_snippet(SMOKE.read_text())}\necho "$mode|$installed_mode|$round_trip"'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout.strip()
    assert out == f"{mode}|{installed}|{round_trip}"
    assert (
        SMOKE.read_text().count('if [[ -z "$round_trip" ]]; then') == 2
    )  # the mail block and the connect block


def test_the_host_picks_the_entry_whose_mode_it_means():
    """MENU_DOWN counts presses of Down from the first entry: the entry numbers follow the files."""
    modes = [smoke_word(entry(f)["options"]) for f in entry_files()]
    text = TEST_VM.read_text()
    branches = re.split(
        r"^  ([a-z-]+(?:\|[a-z-]+)*)\)\n", text[text.index('case "$mode" in\n  install)') :], flags=re.M
    )
    by_name = dict(zip(branches[1::2], branches[2::2]))
    for branch, word in {
        "install": "install",
        "install-encrypted": "install-encrypted",
        "refresh": "refresh",
        "update": "install-update",
    }.items():
        downs = re.findall(r"MENU_DOWN=(\d+) (?:RAM=\S+ )?boot (?:live-install|refresh) ", by_name[branch])
        assert len(downs) == 1, branch
        assert modes[int(downs[0])] == word, (branch, downs)
    assert modes[1] == "" and "MENU_DOWN=1 boot live " in by_name["live"]


# -- the package checks in the smoke ------------------------------------------


def smoke_function(name: str) -> str:
    """One function of the smoke script, as written (a one-liner or up to its closing brace)."""
    lines = SMOKE.read_text().splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"^(\s*){re.escape(name)}\(\) \{{", line)
        if not m:
            continue
        if line.rstrip().endswith("}"):
            return line + "\n"
        for j in range(i + 1, len(lines)):
            if lines[j] == f"{m.group(1)}}}":
                return "\n".join(lines[i : j + 1]) + "\n"
    raise AssertionError(f"no function {name} in the smoke script")


def run_functions(
    names: list[str], script: str, *, replace: dict[str, str] | None = None, env: dict | None = None
):
    """The smoke script's own functions `names`, in bash, then `script`. `replace` swaps a path they name."""
    body = "".join(smoke_function(n) for n in names)
    for old, new in (replace or {}).items():
        assert old in body, old
        body = body.replace(old, new)
    return subprocess.run(
        ["bash", "-c", f"set -u\n{body}\n{script}"],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], **(env or {})},
    )


def test_the_package_checks_run_in_every_mode_before_any_mode_block():
    text = SMOKE.read_text()
    checks = [ln for ln in text.splitlines() if ln.startswith("check package-")]
    assert len(checks) >= 20
    first, last = text.index("check package-owns-share-agentd"), text.index("check package-hypr-config-clean")
    for block in (
        'if [[ "$mode" == "install" ]]; then',
        'if [[ "$mode" == "install-encrypted" ]]; then',
        'if [[ "$mode" == "refresh" ]]; then',
        'if [[ "$mode" == "undo" ]]; then',
        'if [[ "$mode" == "update" ]]; then',
        'if [[ "$mode" == "refreshed" ]]; then',
    ):
        assert last < text.index(block), block
    # No mode test around them: they are at the top level of the script, with the live checks.
    assert first > text.index("check idle-cpu idle_share")
    assert "$mode" not in text[first:last]


def test_the_package_checks_name_the_six_owned_paths_and_the_facts_of_the_brief():
    text = SMOKE.read_text()
    for path in (
        "/usr/share/bombadil/bin/agentd",
        "/usr/bin/agentd",
        "/usr/bin/bombadil-install",
        "/usr/lib/systemd/user/bombadil-agentd.service",
        "/etc/sudoers.d/bombadil",
        "/etc/skel/.config/hypr/hyprland.lua",
    ):
        assert f"owned_by_bombadil {path}" in text, path
    for fact in (
        "package-no-missing-files",
        "package-agentd-is-a-link",
        "package-nothing-loose-in-local-bin",
        "package-version-line",
        "package-repo-above-core",
        "package-no-build-path-in-etc",
        "package-repo-db-readable-by-everyone",
        "package-ledger-has-the-six-units",
        "package-greetd-runs-the-packages-config",
        "package-os-release-logo",
        "package-browser-env",
        "package-sudoers-mode",
        "package-hypr-part-loaded",
        "package-hypr-binds",
        "package-hypr-config-clean",
    ):
        assert f"check {fact} " in text, fact
    assert "pacman -Qk bombadil" in text and "/tmp/bombadil-work" in text
    assert "BROWSER=/usr/bin/bombadil-browser" in text and "systemctl show -p ExecStart greetd" in text
    assert "/usr/share/bombadil/greetd/config.toml" in text and "LOGO=" in text and "prints 440" in text
    assert "check layout-linger " in text and text.index("check layout-linger") > text.index(
        "layout_checks() {"
    )


def enable_list(name: str) -> list[str]:
    path = PAYLOAD / "usr/share/bombadil" / name
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


def test_the_smokes_unit_lists_are_the_packages_own():
    text = SMOKE.read_text()
    ledger = re.search(r"six_units=\((.*?)\)\n", text, re.S).group(1)
    assert sorted(re.findall(r'"([^"]+)"', ledger)) == sorted(
        [f"system {u}" for u in enable_list("enable.system")]
        + [f"user {u}" for u in enable_list("enable.user")]
    )
    assert len(enable_list("enable.system")) + len(enable_list("enable.user")) == 6
    loops = re.findall(r"for u in ([a-z -]+); do", smoke_function("unit_checks"))
    system, user, static = (set(words.split()) for words in loops)
    assert system == {u.removesuffix(".service") for u in enable_list("enable.system")}
    assert user == {u.removesuffix(".service") for u in enable_list("enable.user")}
    # The two that Hyprland starts have no [Install] section, and are not in either list.
    units = PAYLOAD / "usr/lib/systemd/user"
    no_install = {
        p.stem for p in units.glob("*.service") if not re.search(r"^\[Install\]", p.read_text(), re.M)
    }
    assert static == no_install == {"bombadil-agentd", "bombadil-shell"}


def test_the_ledger_check_wants_exactly_the_six_lines(tmp_path):
    ledger = tmp_path / "enabled-units"
    six = [
        "system bombadil-brain-watch.service",
        "system bombadil-connect.service",
        "system bombadil-smoke.service",
        "user bombadil-brain.service",
        "user bombadil-browserd.service",
        "user bombadil-mail.service",
    ]

    def run():
        script = (
            f"ledger={ledger}\nsix_units=({' '.join(chr(34) + u + chr(34) for u in six)})\nledger_has_the_six"
        )
        return run_functions(["ledger_has_the_six"], script)

    ledger.write_text("\n".join(reversed(six)) + "\n")
    assert run().returncode == 0
    ledger.write_text("\n".join(six[:5]) + "\n")
    assert run().returncode != 0
    ledger.write_text("\n".join(six + ["user bombadil-extra.service"]) + "\n")
    assert run().returncode != 0
    ledger.unlink()
    assert run().returncode != 0


@pytest.mark.parametrize(
    "line,ok",
    [
        ("Bombadil 2026.11.12 (abc1234)", True),
        ("Bombadil 2026.11.12 (abc1234), installed 2 Oct", True),
        ("Bombadil 2026.11.12 (abc1234+update), installed 2 Oct, refreshed 4 Nov", True),
        ("Bombadil (version unknown)", False),
        ("Bombadil 2026.11.12", False),
        ("Bombadil 2026-11-12 (abc1234)", False),
        ("", False),
    ],
)
def test_the_version_line_check_wants_the_shape_of_bombadil_version(line, ok):
    r = run_functions(
        ["version_has_its_shape"], f"as_user() {{ printf '%s\\n' '{line}'; }}\nversion_has_its_shape"
    )
    assert (r.returncode == 0) == ok, r.stdout


@pytest.mark.parametrize(
    "conf,ok",
    [
        (
            "[options]\nSigLevel = Required\n[bombadil]\nInclude = x\n[core]\nInclude = m\n",
            True,
        ),
        ("[options]\n[core]\nInclude = m\n[bombadil]\nInclude = /etc/pacman.d/bombadil.conf\n", False),
        ("[options]\n[core]\nInclude = m\n", False),
        ("[options]\n[bombadil]\nInclude = x\n", False),
    ],
)
def test_the_repo_must_be_above_core_in_pacman_conf(tmp_path, conf, ok):
    path = tmp_path / "pacman.conf"
    path.write_text(conf)
    r = run_functions(["repo_above_core"], "repo_above_core", replace={"/etc/pacman.conf": str(path)})
    assert (r.returncode == 0) == ok


def test_the_update_conf_is_the_options_of_the_real_one_and_only_the_bombadil_section(tmp_path):
    real = ROOT / "iso/pacman.conf"
    out = tmp_path / "update.conf"
    r = run_functions(
        ["write_update_conf"],
        f"update_conf={out}\nwrite_update_conf",
        replace={"/etc/pacman.conf": str(real)},
    )
    assert r.returncode == 0, r.stderr
    lines = out.read_text().splitlines()
    options = real.read_text().split("[options]\n", 1)[1].split("\n[", 1)[0].splitlines()
    assert lines == ["[options]", *options, "", "[bombadil]", "Include = /etc/pacman.d/bombadil.conf"]
    assert [ln for ln in lines if ln.startswith("[")] == ["[options]", "[bombadil]"]
    assert "core" not in out.read_text() and "mirrorlist" not in out.read_text()
    # A pacman.conf with no [options] is no base for one.
    bare = tmp_path / "bare.conf"
    bare.write_text("[core]\nInclude = m\n")
    r = run_functions(
        ["write_update_conf"],
        f"update_conf={out}\nwrite_update_conf",
        replace={"/etc/pacman.conf": str(bare)},
    )
    assert r.returncode != 0


def test_the_migrate_log_check_reads_only_what_this_update_wrote(tmp_path):
    log = tmp_path / "migrate.log"
    old = "2026-11-01T10:00:00+0000 001-pacman-repo FAILED rc=1 no [core]\n"
    new_ok = "2026-11-12T10:00:00+0000 002-os-release-logo changed\n"
    new_bad = "2026-11-12T10:01:00+0000 003-browser-env FAILED rc=1 nope\n"

    def run(before: int):
        return run_functions(["migrate_log_clean"], f"migrate_log={log}\nmigrate_log_clean {before}")

    log.write_text(old + new_ok)
    assert run(1).returncode == 0  # the failure was there before the update
    assert run(0).returncode != 0
    log.write_text(old + new_ok + new_bad)
    assert run(1).returncode != 0
    log.unlink()
    assert run(0).returncode == 0  # a migrate step that had nothing to do logs nothing


@pytest.mark.parametrize(
    "binds,ok",
    [
        (
            '[{"key": "SUPER_L", "release": true}, {"key": "Return"}, {"key": "space"}, {"key": "Escape"}]',
            True,
        ),
        (
            '[{"key": "SUPER_L", "release": false}, {"key": "Return"}, {"key": "space"}, {"key": "Escape"}]',
            False,
        ),
        ('[{"key": "SUPER_L", "release": true}, {"key": "Return"}]', False),
        ("[]", False),
    ],
)
def test_the_binds_check_wants_the_release_bind_and_the_keys_of_bombadils_part(binds, ok):
    r = run_functions(["bombadil_binds"], f"hypr_json() {{ printf '%s' '{binds}'; }}\nbombadil_binds")
    assert (r.returncode == 0) == ok, r.stdout


def test_the_state_check_compares_what_the_command_prints_not_its_exit_status():
    r = run_functions(["prints"], "prints disabled bash -c 'echo disabled; exit 1'")
    assert r.returncode == 0 and r.stdout.strip() == "disabled"
    assert run_functions(["prints"], "prints static echo enabled").returncode != 0


def test_the_stub_check_wants_the_stub_as_the_head_of_the_file_and_the_planted_line_kept(tmp_path):
    skel = tmp_path / "skel.lua"
    lua = tmp_path / "hyprland.lua"
    skel.write_text("-- stub\nlocal ok = pcall(require, 'x')\n")
    line = 'hl.bind("SUPER + F9", hl.dsp.exec_cmd("true")) -- smoke: a line of the person\'s'
    names = ["starts_with_the_stub", "old_file_kept"]

    def run(what: str):
        return run_functions(names, f"skel_lua={skel}\nlua={lua}\n{what}", env={"smoke_user_line": line})

    lua.write_text(skel.read_text() + "\n" + line + "\n")
    assert run("starts_with_the_stub").returncode == 0
    lua.write_text("-- not the stub\n" + line + "\n")
    assert run("starts_with_the_stub").returncode != 0
    # the old file is kept whole: the person's line and Bombadil's start hook
    Path(f"{lua}.before-split").write_text('hl.on("hyprland.start", function() end)\n' + line + "\n")
    assert run("old_file_kept").returncode == 0
    Path(f"{lua}.before-split").write_text('hl.on("hyprland.start", function() end)\n-- ' + line + "\n")
    assert run("old_file_kept").returncode != 0


def test_homefix_step_one_must_be_in_the_state_file(tmp_path):
    state = tmp_path / "home.json"
    names = ["homefix_recorded_step_one"]
    replace = {"/home/user/.local/state/bombadil/home.json": str(state)}
    state.write_text('{"version": 1, "steps": {"1": {"result": "migrated"}, "2": {}}}')
    assert run_functions(names, "homefix_recorded_step_one", replace=replace).returncode == 0
    state.write_text('{"version": 1, "steps": {"2": {}}}')
    assert run_functions(names, "homefix_recorded_step_one", replace=replace).returncode != 0
    state.write_text("{")
    assert run_functions(names, "homefix_recorded_step_one", replace=replace).returncode != 0


def test_the_refresh_plants_a_line_of_the_persons_in_the_old_hyprland_lua_and_the_refreshed_phase_finds_it():
    text = SMOKE.read_text()
    planted = 'hl.bind(\\"SUPER + F9\\", hl.dsp.exec_cmd(\\"true\\")) -- smoke: a line of the person\'s'
    assert f'smoke_user_line="{planted}"' in text
    plant = text[text.index("  plant() {") : text.index("  check refresh-plant plant")]
    assert 'printf \'\\n%s\\n\' "$smoke_user_line" >>"$top/@home/user/.config/hypr/hyprland.lua"' in plant
    refreshed = text[text.index('if [[ "$mode" == "refreshed" ]]; then') :]
    for name in (
        "refresh-homefix-wrote-the-stub",
        "refresh-homefix-kept-the-old-file",
        "refresh-homefix-kept-the-persons-line",
        "refresh-homefix-recorded-step-one",
        "refresh-homefix-hypr-config-ok",
        "refresh-homefix-left-the-sign-in",
    ):
        assert f"check {name} " in refreshed, name
    assert "check refresh-homefix-wrote-the-stub wait_for 90" in refreshed
    assert "check refresh-homefix-recorded-step-one wait_for 90" in refreshed
    # The old system comes back and its own checks run: nothing of the homefix is checked after the undo.
    assert refreshed.index("check refresh-homefix-hypr-config-ok") < refreshed.index("check refresh-undo")


def test_the_update_block_does_what_the_brief_says_in_the_order_it_says():
    text = SMOKE.read_text()
    block = text[
        text.index('if [[ "$mode" == "update" ]]; then') : text.index('if [[ "$mode" == "refreshed" ]]; then')
    ]
    order = [
        "update-restore-point",
        "update-turn-a-unit-off",
        "update-host-serves-the-package",
        "update-conf-written",
        "update-pacman-runs",
        "update-version-is-the-served-build",
        "update-package-version-changed",
        "update-package-owns-agentd",
        "update-package-no-missing-files",
        "update-migrate-log-clean",
        "update-hyprland-runs",
        "update-hypr-config-ok",
        "update-bar-layer",
        "update-bar-answers",
        "update-ledger-unchanged",
        "update-undo ",
        "update-undo-brought-the-old-version-back",
        "update-undo-brought-the-old-package-back",
        "update-home-keeps-the-stub",
    ]
    at = [block.index(f"check {name}") for name in order]
    assert at == sorted(at), [n for n, a, b in zip(order, at, sorted(at)) if a != b]
    assert "unit_checks update" in block and "reboot_for_phase_two" in block
    # The restore point is taken before the unit is turned off, so the undo brings the unit back on.
    assert block.index("update-restore-point") < block.index("update-turn-a-unit-off")
    assert 'snapshots.Snapshots().create("turn:1: smoke update")' in block
    # A user unit enabled for everyone is turned off with --global; --user would leave the link.
    assert 'systemctl --global disable "$off_unit"' in block
    assert not re.search(r"^\s*check .*systemctl --user disable", text, re.M)
    # Only [options] and [bombadil]: no route to the Arch mirrors from the VM.
    assert (
        "Include = /etc/pacman.d/bombadil.conf" in block
        and 'pacman --config "$update_conf" -Syu --noconfirm' in block
    )
    assert "+update)" in block and "10.0.2.2:8123" in block
    # The first boot hands what it saw to the second through the home folder, which an undo does not roll
    # back.
    assert "/home/user/.bombadil-smoke-update-was" in block and 'rm -f "$phase" "$was_file"' in block
    # Its own checks stay out of the undo round trip's (the brain, the layout).
    assert "brain" not in block and "layout_checks" not in block


def test_the_undo_the_refresh_and_the_update_end_their_first_boot_the_same_way():
    text = SMOKE.read_text()
    assert (
        text.count("reboot_for_phase_two\n") == 3
    )  # the undo, the refresh and the update; the definition is the 4th
    assert text.count("phase=/home/user/.bombadil-smoke-phase") == 1
    helper = smoke_function("reboot_for_phase_two")
    assert (
        'say "REBOOT pass=$ok fail=$fail"' in helper and "systemctl reboot" in helper and "exit 0" in helper
    )


# -- scripts/test-vm.sh, run against stand-ins --------------------------------

STUB_QEMU = r"""#!/usr/bin/env bash
# Stands in for qemu-system-x86_64: records what it was asked, looks at the host's server the way the guest
# would, and writes the lines the real smoke run prints into the serial log the host script reads.
echo "$*" >> "$STUB/qemu.calls"
log=""
for a in "$@"; do [[ "$a" == *logfile=* ]] && log="${a##*logfile=}"; done
if [[ " $* " == *usb-storage* ]]; then
  printf '%s\n' "Boot in 3s" "BOMBADIL-SMOKE: PASS install" "BOMBADIL-SMOKE: DONE pass=1 fail=0" >> "$log"
else
  python3 -c 'import urllib.request as u
opener = u.build_opener(u.ProxyHandler({}))
print(len(opener.open("http://127.0.0.1:8123/bombadil.db", timeout=5).read()))' >> "$STUB/served" 2>&1
  printf '%s\n' "BOMBADIL-SMOKE: REBOOT pass=2 fail=${STUB_REBOOT_FAIL:-0}" \
    "BOMBADIL-SMOKE: DONE pass=3 fail=0" >> "$log"
fi
"""
STUB_QEMU_IMG = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB/qemu-img.calls"
[[ "$1" == create ]] && : > "${@: -2:1}"
exit 0
"""
STUB_DOCKER = r"""#!/usr/bin/env bash
# Stands in for docker: records its arguments, one per line, and leaves a repository where the build would.
printf '%s\n' "$@" > "$STUB/docker.args"
for ((i = 1; i <= $#; i++)); do
  if [[ "${!i}" == -v ]]; then j=$((i + 1)); [[ "${!j}" == *:/src ]] && src="${!j%:/src}"; fi
done
mkdir -p "$src/out/repo"; echo db > "$src/out/repo/bombadil.db"
"""
ENTRY_TEXT = b"options archisobasedir=%INSTALL_DIR% console=ttyS0,115200 bombadil.smoke=install-update\n"
ISO_NAME = "bombadil-2026.01.02-x86_64.iso"


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )  # as http.server does: a closed connection is not a user
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def wait_closed(port: int) -> bool:
    """Nothing listens on the port (the script's exit trap has had a moment to end the server)."""
    for _ in range(40):
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.1)
    return False


class Host:
    """A copy of the host scripts with stand-ins for QEMU, qemu-img and the container runtime on the path."""

    def __init__(self, tmp_path: Path, *, entry: bool = True):
        self.tree = tmp_path / "tree"
        self.stub = tmp_path / "stub"
        (self.tree / "scripts").mkdir(parents=True)
        (self.stub / "bin").mkdir(parents=True)
        for name in ("test-vm.sh", "build-in-container.sh", "qmp.py"):
            shutil.copy2(ROOT / "scripts" / name, self.tree / "scripts" / name)
        for name, body in (
            ("qemu-system-x86_64", STUB_QEMU),
            ("qemu-img", STUB_QEMU_IMG),
            ("docker", STUB_DOCKER),
        ):
            f = self.stub / "bin" / name
            f.write_text(body)
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        subprocess.run(["git", "init", "-q", str(self.tree)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.tree),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "x",
            ],
            check=True,
        )
        self.commit = subprocess.run(
            ["git", "-C", str(self.tree), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.iso = tmp_path / ISO_NAME
        self.iso.write_bytes(
            b"\0" * 2048 + (ENTRY_TEXT if entry else b"options console=ttyS0,115200 bombadil.smoke\n")
        )
        self.firmware = tmp_path / "OVMF_CODE.fd"
        self.firmware.write_bytes(b"\0")
        self.disk = self.tree / "out/test/disk.qcow2"

    def run(self, mode: str, **env: str) -> subprocess.CompletedProcess:
        full = {
            "PATH": f"{self.stub / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.stub),
            "STUB": str(self.stub),
            "MODE": mode,
            "ISO": str(self.iso),
            "OVMF_CODE": str(self.firmware),
            "TIMEOUT": "60",
            "CONTAINER_RUNTIME": str(self.stub / "bin/docker"),
            **env,
        }
        return subprocess.run(
            ["bash", str(self.tree / "scripts/test-vm.sh")],
            env=full,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def calls(self, name: str) -> list[str]:
        f = self.stub / name
        return f.read_text().splitlines() if f.exists() else []


def test_mode_update_refuses_an_iso_without_the_entry_before_anything_is_touched(tmp_path):
    host = Host(tmp_path, entry=False)
    host.disk.parent.mkdir(parents=True)
    host.disk.write_bytes(b"a disk someone cares about")
    r = host.run("update")
    assert r.returncode == 2
    assert (
        "has no boot entry for bombadil.smoke=install-update" in r.stdout
        and "BOMBADIL_TEST_ENTRIES=1" in r.stdout
    )
    assert host.disk.read_bytes() == b"a disk someone cares about"
    assert (
        host.calls("qemu-img.calls") == []
        and host.calls("qemu.calls") == []
        and not (host.stub / "docker.args").exists()
    )


def test_mode_update_with_reuse_disk_needs_the_disk_an_earlier_run_left(tmp_path):
    host = Host(tmp_path)
    r = host.run("update", REUSE_DISK="1")
    assert r.returncode == 2 and "REUSE_DISK=1 needs" in r.stdout
    assert host.calls("qemu-img.calls") == [] and host.calls("qemu.calls") == []


@pytest.mark.skipif(not port_free(8123), reason="port 8123 is in use here")
@pytest.mark.parametrize("reuse", [False, True], ids=["install-then-update", "reuse-the-disk"])
def test_mode_update_builds_a_newer_package_serves_it_while_the_vm_runs_and_stops_the_server(tmp_path, reuse):
    host = Host(tmp_path)
    if reuse:
        host.disk.parent.mkdir(parents=True)
        host.disk.write_bytes(b"installed")
    r = host.run("update", **({"REUSE_DISK": "1"} if reuse else {}))
    assert r.returncode == 0, r.stdout + r.stderr
    # The package is built in the container, with the commit that says +update, a release that is the time
    # now, and
    # the ISO's own day as its version.
    args = host.calls("docker.args")
    env = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args if re.match(r"^BOMBADIL_[A-Z]+=", a)}
    assert env["BOMBADIL_COMMIT"] == f"{host.commit}+update"
    assert env["BOMBADIL_PKGVER"] == "2026.01.02" and int(env["BOMBADIL_PKGREL"]) > 1_700_000_000
    # With the host of the VM listed first in its repository file, as the image's file has it: pacman would replace the
    # unmodified file on the disk with one that lacks the line if the update did not carry it. The harness's own
    # environment has none of it, so it is the script that sets it.
    assert "BOMBADIL_DEV_SERVER=1" in args
    joined = "\n".join(args)
    assert "pacman -Syu --noconfirm --needed fakeroot binutils" in joined
    assert "build-package.sh /src/out/repo" in joined and "build-iso.sh" not in joined
    # The installed disk starts with the server up (the guest's own request in the stand-in succeeded), the
    # install before it only when the disk is not reused, and the server is gone when the script is.
    qemu = host.calls("qemu.calls")
    assert len(qemu) == (1 if reuse else 2)
    assert "usb-storage" not in qemu[-1] and "disk.qcow2" in qemu[-1]
    if not reuse:
        assert "usb-storage" in qemu[0]
        assert host.calls("qemu-img.calls")[0].startswith("create -q -f qcow2")
    assert host.calls("served") == ["3"]  # the bytes of the stand-in's bombadil.db
    assert wait_closed(8123)


@pytest.mark.skipif(not port_free(8123), reason="port 8123 is in use here")
def test_mode_update_fails_when_the_first_boot_of_the_installed_system_did_not_finish_clean(tmp_path):
    host = Host(tmp_path)
    host.disk.parent.mkdir(parents=True)
    host.disk.write_bytes(b"installed")
    r = host.run("update", REUSE_DISK="1", STUB_REBOOT_FAIL="2")
    assert r.returncode == 1 and "did not finish clean" in r.stdout
    assert wait_closed(8123)


def test_build_in_container_builds_only_the_package_for_the_package_argument_and_refuses_others(tmp_path):
    host = Host(tmp_path)
    env = {
        "PATH": f"{host.stub / 'bin'}:{os.environ['PATH']}",
        "STUB": str(host.stub),
        "HOME": str(host.stub),
        "CONTAINER_RUNTIME": str(host.stub / "bin/docker"),
    }
    script = host.tree / "scripts/build-in-container.sh"
    r = subprocess.run(["bash", str(script), "package"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (host.tree / "out/repo/bombadil.db").is_file()
    args = host.calls("docker.args")
    assert "build-package.sh /src/out/repo" in "\n".join(args) and "build-iso.sh" not in "\n".join(args)
    assert "BOMBADIL_PKGREL=1" in args and f"BOMBADIL_COMMIT={host.commit}" in args
    # A package nobody asked to list the host of a test VM does not: the switch is not passed unless it is set.
    assert not [a for a in args if a.startswith("BOMBADIL_DEV_SERVER")]
    # The image build is what no argument asks for, as it always was.
    shutil.rmtree(host.tree / "out")
    (host.stub / "docker.args").unlink()
    assert subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True).returncode == 0
    joined = "\n".join(host.calls("docker.args"))
    assert (
        "build-iso.sh" in joined
        and "archiso git nodejs npm fakeroot binutils" in joined
        and "build-package.sh" not in joined
    )
    r = subprocess.run(["bash", str(script), "bogus"], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr


def test_build_in_container_passes_the_package_version_it_was_given_through(tmp_path):
    host = Host(tmp_path)
    env = {
        "PATH": f"{host.stub / 'bin'}:{os.environ['PATH']}",
        "STUB": str(host.stub),
        "HOME": str(host.stub),
        "CONTAINER_RUNTIME": str(host.stub / "bin/docker"),
        "BOMBADIL_PKGVER": "2026.02.03",
        "BOMBADIL_PKGREL": "17",
        "BOMBADIL_COMMIT": "abc1234+update",
        "BOMBADIL_DEV_SERVER": "1",
    }
    subprocess.run(
        ["bash", str(host.tree / "scripts/build-in-container.sh"), "package"],
        env=env,
        check=True,
        capture_output=True,
    )
    args = host.calls("docker.args")
    assert {"BOMBADIL_PKGVER=2026.02.03", "BOMBADIL_PKGREL=17", "BOMBADIL_COMMIT=abc1234+update"} <= set(args)
    # The switch for the test VM's repository file goes into the container too, where the package is built.
    assert "BOMBADIL_DEV_SERVER=1" in args


def test_build_in_container_gives_the_dev_server_switch_to_a_package_build_and_to_no_image_build(tmp_path):
    # The switch is for the package of a test VM. An image build decides it from BOMBADIL_TEST_ENTRIES (build-iso.sh sets
    # it for a test build, and drops it for a release), so one left in the shell is not even handed to a release build.
    host = Host(tmp_path)
    env = {
        "PATH": f"{host.stub / 'bin'}:{os.environ['PATH']}",
        "STUB": str(host.stub),
        "HOME": str(host.stub),
        "CONTAINER_RUNTIME": str(host.stub / "bin/docker"),
        "BOMBADIL_DEV_SERVER": "1",
    }
    script = host.tree / "scripts/build-in-container.sh"

    def container_args(*argv: str, **extra: str) -> list[str]:
        (host.stub / "docker.args").unlink(missing_ok=True)
        r = subprocess.run(
            ["bash", str(script), *argv], env={**env, **extra}, capture_output=True, text=True, check=False
        )
        assert r.returncode == 0, r.stderr
        return host.calls("docker.args")

    assert "BOMBADIL_DEV_SERVER=1" in container_args("package")
    for argv in ((), ("iso",)):
        args = container_args(*argv)
        assert "build-iso.sh" in "\n".join(args) and not [
            a for a in args if a.startswith("BOMBADIL_DEV_SERVER")
        ], argv
    args = container_args(BOMBADIL_TEST_ENTRIES="1")
    assert "BOMBADIL_TEST_ENTRIES=1" in args and not [a for a in args if a.startswith("BOMBADIL_DEV_SERVER")]


def test_the_host_script_names_the_new_mode_and_the_reuse_switch_in_its_header():
    header = TEST_VM.read_text().split("set -euo pipefail")[0]
    assert (
        "MODE=update scripts/test-vm.sh" in header and "REUSE_DISK=1" in header and "127.0.0.1:8123" in header
    )
    # The server ends with the script, and the VM does too: one trap, not one per VM.
    text = TEST_VM.read_text()
    assert text.count("trap ") == 1 and "trap cleanup EXIT" in text
    assert "BOMBADIL_DEV_SERVER=1" in header
    assert 'python3 -m http.server 8123 --bind 127.0.0.1 --directory "$root/out/repo"' in text
    assert text.index("need_entries()") < text.index('disk="$out/disk.qcow2"')


# -- scripts/wsl-vm.sh --------------------------------------------------------


def command_of(function: str, word: str) -> str:
    """The shell command in FUNCTION that runs WORD, with its backslash continuations joined into one line."""
    joined = function.replace("\\\n", " ")
    found = [ln for ln in joined.splitlines() if word in ln and not ln.lstrip().startswith("#")]
    assert len(found) == 1, (word, found)
    return found[0]


def test_wsl_vm_builds_serves_and_refreshes_the_way_the_laptop_flow_is_documented():
    text = WSL_VM.read_text()
    header = text.split("set -euo pipefail")[0]
    for word in ("update", "refresh", "reinstall", "live", "build", "stop"):
        assert f"wsl-vm.sh {word}" in header, word
    # Usage prints the whole header.
    last = int(re.search(r"sed -n '2,(\d+)p' \"\$0\"", text).group(1))
    lines = text.splitlines()
    assert lines[last - 1].startswith("#") and not lines[last].startswith("#"), (
        last,
        lines[last - 1],
        lines[last],
    )
    # setup() and the ISO's key.
    assert re.search(r"local pkgs=\([^)]*\bfakeroot binutils\)", text)
    key = re.search(r"^iso_key\(\) \{.*\}$", text, re.M).group(0)
    assert (
        "HEAD:packaging" in key
        and "HEAD:scripts/build-package.sh" in key
        and "HEAD:scripts/build-iso.sh" in key
    )
    # update: refuses a disk from before packages, builds with build-package.sh into out/repo and serves it on
    # the loopback.
    update = text[text.index("\nupdate() {") : text.index("\n}\n", text.index("\nupdate() {"))]
    assert '[[ -f "$out/.installed-packaged" ]] ||' in update
    for want in (
        "SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)",
        "BOMBADIL_COMMIT=$(git rev-parse --short HEAD)",
        "BOMBADIL_PKGREL=$(date +%s)",
        'scripts/build-package.sh "$out/repo"',
        'python3 -m http.server 8123 --bind 127.0.0.1 --directory "$out/repo"',
        "trap 'kill",
        "run --installed",
        'say "update" to the agent',
    ):
        assert want in update, want
    # The package carries the host of the VM (10.0.2.2:8123) in its repository file, like the ISO the disk was installed
    # from: an update without the line would drop it from the disk.
    assert "BOMBADIL_DEV_SERVER=1" in command_of(update, "scripts/build-package.sh")
    build = text[text.index("\nbuild() {") : text.index("\n}\n", text.index("\nbuild() {"))]
    iso_build = command_of(build, "scripts/build-iso.sh")
    assert "BOMBADIL_TEST_ENTRIES=1" in iso_build and "BOMBADIL_DEV_SERVER=1" in iso_build
    # install and refresh write the marker, update reads it.
    install = text[text.index("\ninstall() {") : text.index("\n}\n", text.index("\ninstall() {"))]
    refresh = text[text.index("\nrefresh() {") : text.index("\n}\n", text.index("\nrefresh() {"))]
    assert ".installed-packaged" in install and ".installed-packaged" in refresh
    assert 'rm -f "$out/.installed" "$out/.installed-packaged"' in install
    # refresh is the installer's own: a qemu-img restore point, the ISO, bombadil-install --refresh, poweroff.
    assert "qemu-img snapshot -c" in refresh and "\n  build\n" in refresh
    assert "bombadil-install --refresh /dev/vda --yes" in refresh and "poweroff" in refresh
    assert (
        refresh.index("qemu-img snapshot -c") < refresh.index("\n  build\n") < refresh.index("serial_session")
    )
    assert "vm-tools" not in refresh and "serialpump" not in refresh
    assert "  refresh) refresh; run --installed ;;" in text and "  update) update ;;" in text


def test_the_installs_monitor_pin_is_a_line_of_the_persons_under_the_stub():
    text = WSL_VM.read_text()
    install = text[text.index("\ninstall() {") : text.index("\n}\n", text.index("\ninstall() {"))]
    assert 'hl.monitor({ output = \\"Virtual-1\\", mode = \\"$RES@60\\"' in install
    assert ">> /mnt/home/user/.config/hypr/hyprland.lua" in install
    assert "BOMBADIL_INSTALL_LEAVE_MOUNTED=1" in install and "umount -R /mnt" in install
    # The pin goes after the install, never into the package's part.
    assert install.index("bombadil-install /dev/vda --yes") < install.index(
        ">> /mnt/home/user/.config/hypr/hyprland.lua"
    )
    # The refresh does not touch the home folder's file: the kept home still has the line.
    refresh = text[text.index("\nrefresh() {") : text.index("\n}\n", text.index("\nrefresh() {"))]
    assert "hyprland.lua" not in refresh


def test_the_typed_line_of_the_serial_session_cannot_match_the_log_it_waits_on():
    text = WSL_VM.read_text()
    assert "INSTALL-''DONE" in text and text.count("INSTALL-''DONE") == 2  # install and refresh
    assert 'grep -aq "INSTALL-DONE" "$log"' in text


def test_wsl_vm_does_not_name_the_retired_updater_nor_the_local_bin():
    text = WSL_VM.read_text()
    assert (
        RETIRED not in text
        and "/usr/local/bin" not in text
        and "vmlogin" not in text
        and "serialpump" not in text
    )


# -- scripts/vm-tools ---------------------------------------------------------


def test_the_retired_updater_is_gone_and_the_readme_lists_only_tools_that_exist():
    assert not (VM_TOOLS / RETIRED).exists()
    readme = (VM_TOOLS / "README.md").read_text()
    table = [ln for ln in readme.splitlines() if ln.startswith("| `")]
    assert table
    for row in table:
        for cell in re.findall(r"`([^`]+)`", row.split("|")[1]):
            assert (VM_TOOLS / cell.split()[0]).is_file(), cell
    for tool in VM_TOOLS.iterdir():
        if tool.is_file() and tool.suffix == "" and os.access(tool, os.X_OK):
            body = tool.read_text(errors="replace")
            assert RETIRED not in body, tool.name


def test_the_readme_describes_the_launcher_commands_as_they_are_now():
    readme = " ".join((VM_TOOLS / "README.md").read_text().split())  # one line: the file wraps its text
    assert "`update`" in readme and "127.0.0.1:8123" in readme and "`refresh`" in readme
    assert "installer's own refresh" in readme and "in place" not in readme
    assert "/usr/local/bin" not in readme and "iso/airootfs/usr" not in readme


def test_no_file_in_the_repo_names_the_retired_updater():
    files = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    found = []
    for name in files:
        path = ROOT / name.decode()
        if name and path.is_file() and RETIRED.encode() in path.read_bytes():
            found.append(name.decode())
    assert found == []
