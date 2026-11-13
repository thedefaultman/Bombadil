"""What the ISO profile ships. The VM smoke proves a booted machine; these catch a dropped line sooner."""
import os
import re
import subprocess
from pathlib import Path

from payload import HYPR_LUA, PAYLOAD

ISO = Path(__file__).resolve().parent.parent / "iso"
ROOT = ISO.parent


def _packages() -> set[str]:
    return {line.strip() for line in (ISO / "packages.x86_64").read_text().splitlines()
            if line.strip() and not line.startswith("#")}


def _enabled_units(kind: str) -> list[str]:
    """The units the package turns on once, from its list: bombadil-install reads nothing of it, bombadil.install does."""
    lines = (PAYLOAD / f"usr/share/bombadil/enable.{kind}").read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _git_mode(path: Path) -> str:
    """The mode git holds for a tracked file ("100755"); the file's own mode when the tree is not a checkout."""
    import subprocess
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "--", str(path)], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.split()[0]
    return "100755" if os.access(path, os.X_OK) else "100644"


def _assert_the_package_links_in_usr_bin(*names: str) -> None:
    """The PKGBUILD links every command of bin/ into /usr/bin (a loop, so a new command needs no list), and
    each of these is such a command: a file the tree ships, and one that can run."""
    pkgbuild = (ROOT / "packaging/bombadil/PKGBUILD").read_text()
    assert 'for f in "$pkgdir"/usr/share/bombadil/bin/*; do' in pkgbuild
    assert 'ln -s "../share/bombadil/bin/${f##*/}" "$pkgdir/usr/bin/${f##*/}"' in pkgbuild
    for name in names:
        assert (ROOT / "bin" / name).is_file(), f"{name} is linked, but not in bin/"
        assert _git_mode(ROOT / "bin" / name) == "100755", name


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
    # An install that updates everything is said out loud, not done quietly.
    assert "That also updates every other package, so say so in one plain sentence" in providers.system_prompt()


# The installer's GRUB defaults are tested with the installer, in tests/test_installer.py.


def _pill_binds():
    """The lua, and its binds that fire a global shortcut: {keys: (the shortcut, fires on release)}."""
    lua = HYPR_LUA.read_text()
    # Anchored at the line start, so a commented-out bind does not count.
    binds = {}
    for m in re.finditer(r'^\s*hl\.bind\("([^"]+)", hl\.dsp\.global\("([^"]+)"\)(, \{ release = true \})?\)', lua, re.M):
        binds[m.group(1)] = (m.group(2), bool(m.group(3)))
    return lua, binds


def test_the_pill_opens_from_super_and_from_alt_space():
    # A VM window on Windows keeps the Windows key for its Start menu, so Alt+Space is the way in there.
    _, binds = _pill_binds()
    assert binds == {"SUPER + SUPER_L": ("bombadil:pill-release", True),
                     "SUPER + SUPER_R": ("bombadil:pill-release", True),
                     "ALT + space": ("bombadil:pill-press", False)}


def test_the_pill_keys_start_no_process_the_first_letters_would_wait_for():
    # `bombadil pill` is a Python start and a round trip through agentd: on a loaded machine the first
    # letters typed after a tap on Super had gone to the window underneath before the pill had the keyboard.
    # The keys are the bar's own global shortcuts instead.
    lua, _ = _pill_binds()
    code = "\n".join(line for line in lua.splitlines() if not line.lstrip().startswith("--"))
    assert "bombadil pill" not in code


def test_the_shortcuts_the_keys_fire_are_the_ones_the_bar_registers_on_the_edge_it_listens_to():
    # Hyprland does nothing, and says nothing, for a global shortcut nobody registered, and the bar listens
    # to one edge of each: a tap (a release bind) to the release, Alt+Space to the press.
    _, binds = _pill_binds()
    qml = (ISO.parent / "shell/PillKeys.qml").read_text()
    registered = {}
    for block in re.findall(r"GlobalShortcut \{(.*?)\n    \}", qml, re.S):
        appid, name = (re.search(rf'{key}: "([^"]+)"', block).group(1) for key in ("appid", "name"))
        registered[f"{appid}:{name}"] = re.findall(r"^\s*on(Pressed|Released): ", block, re.M)
    assert registered == {"bombadil:pill-release": ["Released"], "bombadil:pill-press": ["Pressed"]}
    for keys, (shortcut, on_release) in binds.items():
        assert registered[shortcut] == (["Released"] if on_release else ["Pressed"]), keys
    # Registered once: the protocol refuses a second client with the same appid and name (the second
    # crashes), which a copy per screen would be. The bar loads it only under Hyprland.
    shell = (ISO.parent / "shell/shell.qml").read_text()
    assert shell.count("PillKeys {") == 1 and shell.index("PillKeys {") < shell.index("Variants {")
    assert re.search(r"Loader \{\s*active: root\.hyprland\s*sourceComponent: PillKeys \{ pill: pillState \}", shell)
    # The VM smoke asks Hyprland whether the bar registered them before it taps a key.
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    assert all(f"'{shortcut}'" in smoke for shortcut in registered) and "hyprctl globalshortcuts" in smoke
    assert smoke.index("check pill-shortcuts-registered") < smoke.index("keys meta_l")


def test_hyprland_does_not_slide_the_bar_when_a_picture_resizes_it():
    # The bar is a layer that is resized whenever a picture appears or grows. With Hyprland's default
    # "layers" animation the pill dipped and swung back for half a second each time.
    lua = HYPR_LUA.read_text()
    assert re.search(r'^\s*hl\.animation\(\{ leaf = "layers", enabled = false \}\)', lua, re.M)


def test_super_return_opens_a_terminal_that_does_not_belong_to_the_drawer_showing():
    # A plain `foot` opens inside the drawer that shows (a special workspace) and goes with it.
    lua = HYPR_LUA.read_text()
    assert re.search(r'^\s*hl\.bind\("SUPER \+ Return", hl\.dsp\.exec_cmd\("bombadil terminal"\)\)', lua, re.M)
    assert '"SUPER + Return", hl.dsp.exec_cmd("foot")' not in lua


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


def test_the_image_version_and_label_are_the_utc_day_and_month_in_every_time_zone():
    # The package the image carries is named with the UTC day of the same build time, and scripts/test-vm.sh reads that
    # day off the image's file name: a host west of UTC must not move either. 2026-12-01 00:20 UTC is still 30 November
    # ten hours west.
    for zone in ("UTC", "UTC+10", "UTC-12"):
        r = subprocess.run(["bash", "-c", 'declare -A file_permissions; source "$1"; echo "$iso_version $iso_label"', "_",
                            str(ISO / "profiledef.sh")], capture_output=True, text=True, check=True,
                           env={"PATH": "/usr/bin:/bin", "TZ": zone, "SOURCE_DATE_EPOCH": "1796084400"})
        assert r.stdout == "2026.12.01 BOMBADIL_202612\n", zone


def test_the_build_reads_the_image_back_and_compares_it_with_its_tree():
    script = (ISO.parent / "scripts/build-iso.sh").read_text()
    assert "verify_image" in script and "diff -rq" in script


def test_every_script_the_package_adds_to_the_path_or_to_grub_is_executable_in_git():
    # The package keeps the executable bit git holds (git has 644 and 755 only), and the image lists no modes for
    # these any more. grub-mkconfig skips a file in /etc/grub.d that is not executable.
    for folder in (PAYLOAD / "usr/bin", PAYLOAD / "etc/grub.d", PAYLOAD / "usr/share/bombadil/migrate"):
        scripts = [p for p in folder.iterdir() if p.is_file() and (folder.name != "migrate" or p.name == "run"
                                                                    or re.fullmatch(r"\d{3}-.*\.sh", p.name))]
        assert scripts, folder
        assert [p.name for p in scripts if _git_mode(p) != "100755"] == [], folder
    # What stays in the image's own tree is listed for mkarchiso, which copies airootfs without modes.
    profiledef = (ISO / "profiledef.sh").read_text()
    listed = set(re.findall(r'\["(/[^"]+)"\]="0:0:\d+"', profiledef))
    assert {"/usr/lib/node_modules/@anthropic-ai/", "/usr/lib/node_modules/@openai/"} <= listed
    # Nothing the package owns is listed there: its modes come from the package.
    assert [p for p in listed if p.startswith(("/usr/local/bin", "/usr/share/bombadil", "/etc/sudoers.d", "/etc/grub.d"))] == []


USER_UNITS = PAYLOAD / "usr/lib/systemd/user"


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


def test_the_daemon_and_the_bar_come_back_when_they_die():
    units = [USER_UNITS / "bombadil-agentd.service", USER_UNITS / "bombadil-shell.service"]
    for path in units:
        u = _unit(path)
        assert u["Service"]["Restart"] == "always", path.name
        # systemd's default of five restarts in ten seconds would leave a crash loop with no bar.
        # (systemd reads the key from [Unit] and ignores it under [Service].)
        assert u["Unit"]["StartLimitIntervalSec"] == "0", path.name
        assert "StartLimitIntervalSec" not in u["Service"], path.name
    # Apps the agent started keep running when only the daemon restarts.
    assert _unit(units[0])["Service"]["KillMode"] == "process"
    assert _unit(units[0])["Service"]["ExecStart"] == "/usr/bin/agentd"
    assert _unit(units[1])["Service"]["ExecStart"] == "/usr/bin/bombadil-shell"


def test_the_session_starts_them_after_handing_over_its_environment():
    lua = HYPR_LUA.read_text()
    start = lua[lua.index('hl.on("hyprland.start"'):].split("\nend)")[0]
    # WAYLAND_DISPLAY and the Hyprland signature exist only in the session, not in the user manager.
    assert start.index("import-environment") < start.index("restart bombadil-agentd bombadil-shell")
    assert "exec_cmd(\"agentd\")" not in start and "quickshell" not in start.replace("bombadil-shell", "")
    # The key for a hung bar restarts the service instead of racing a second quickshell against it.
    assert 'hl.bind("SUPER + CTRL + Escape", hl.dsp.exec_cmd("systemctl --user restart bombadil-shell"))' in lua


def test_setup_and_the_cli_restart_the_daemon_through_systemd():
    setup = (PAYLOAD / "usr/bin/bombadil-setup").read_text()
    assert "systemctl --user restart bombadil-agentd" in setup and "pkill" not in setup
    cli = (ISO.parent / "bin/bombadil").read_text()
    assert "systemctl --user restart bombadil-agentd" in cli


def test_the_installed_system_prunes_its_restore_points_and_takes_no_hourly_ones():
    install = (PAYLOAD / "usr/bin/bombadil-install").read_text()
    assert "set-config TIMELINE_CREATE=no NUMBER_CLEANUP=yes NUMBER_LIMIT=30" in install
    assert re.search(r"^systemctl enable .*\bsnapper-cleanup\.timer\b", install, re.M)
    # The config has to exist before it is changed.
    assert install.index("create-config") < install.index("set-config")


def test_the_installer_takes_the_kernel_from_the_system_it_copies_not_from_the_boot_medium():
    # With copytoram (a USB stick with RAM to spare) /run/archiso/bootmnt is gone by the time the
    # kernel is copied, which used to stop the script after the disk was wiped and copied.
    install = (PAYLOAD / "usr/bin/bombadil-install").read_text()
    assert "bootmnt" not in install
    assert 'install -Dm644 "$kernel" "$target/boot/vmlinuz-linux"' in install
    # It is found, and checked, before the disk is erased.
    assert install.index('kernel=$(newest_kernel "$image")') < install.index('sgdisk -Z "$disk"')


def test_the_smokes_key_requests_are_numbered_per_boot_so_the_host_sends_them_after_a_reboot_too():
    import re
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    host = (ISO.parent / "scripts/test-vm.sh").read_text()
    assert 'say "KEYS $bootid-$nkeys $*"' in smoke and "/proc/sys/kernel/random/boot_id" in smoke
    pattern = re.search(r'grep -ao "(BOMBADIL-SMOKE: KEYS [^"]*)"', host).group(1)
    # The undo round trip boots twice; each boot's first request has its own number.
    log = "BOMBADIL-SMOKE: KEYS 1a2b3c4d-1 meta_l\nBOMBADIL-SMOKE: KEYS 9f8e7d6c-1 meta_l\n"
    assert re.findall(pattern, log) == ["BOMBADIL-SMOKE: KEYS 1a2b3c4d-1 meta_l", "BOMBADIL-SMOKE: KEYS 9f8e7d6c-1 meta_l"]


def test_the_smokes_sign_in_block_never_runs_where_a_provider_is_already_chosen():
    # It picks other providers, cancels their logins and deletes the config file at its end.
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    gate = 'if [[ "$mode" =~ ^(install|live|)$ && ! -e "$signin_config" ]]; then'
    assert 'signin_config=/home/user/.config/bombadil/config.toml' in smoke and gate in smoke
    assert smoke.index(gate) < smoke.index("check signin-agentd-starts") < smoke.index("rm -f /home/user/.fake-signin /home/user/.config/bombadil/config.toml")
    # The gate is one block: what the fresh ISO runs is inside it, up to the install step.
    assert smoke.index(gate) < smoke.index('if [[ "$mode" == "install" ]]; then')
    host = (ISO.parent / "scripts/test-vm.sh").read_text()
    assert "SKIP signin" in smoke and "SKIP signin" in host


def _run_gate(home, with_config):
    """The gate's own lines, run in bash against a home with or without a config file."""
    import subprocess
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    start = smoke.index("signin_config=")
    end = smoke.index(": >/tmp/smoke.agentd.log")
    home.mkdir()
    cfg = home / "config.toml"
    if with_config:
        cfg.write_text('provider = "claude"\n')
    body = smoke[start:end].replace("/home/user/.config/bombadil/config.toml", str(cfg))
    script = f'say() {{ echo "SAY $*"; }}\nmode=live\n{body}\necho RUNS\nfi\n'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout


def test_the_gate_skips_with_a_config_and_runs_without_one(tmp_path):
    chosen = _run_gate(tmp_path / "chosen", with_config=True)
    assert "SKIP signin" in chosen and "RUNS" not in chosen
    fresh = _run_gate(tmp_path / "fresh", with_config=False)
    assert "RUNS" in fresh and "SKIP" not in fresh


def test_the_smoke_replaces_agentd_through_its_unit_and_checks_the_units_with_a_function():
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    # systemd starts a killed agentd again in the normal environment, so the smoke's own agentd
    # (another provider, a fake login) needs the unit stopped first, and the unit back after.
    restart = smoke[smoke.index("restart_agentd() {"):smoke.index("page_or_offline()")]
    assert restart.index("systemctl --user stop bombadil-agentd") < restart.index("setsid -f agentd")
    assert "systemctl --user start bombadil-agentd" in smoke and "check agentd-restored agentd_unit_again" in smoke
    # as_user is a shell function: a `bash -c` would not find it.
    assert "check services-are-units units_active" in smoke
    assert "bash -c 'for u in bombadil-agentd" not in smoke


# -- Mail (docs/MAIL.md): Thunderbird as the unseen engine, and the service in front of it --

def test_thunderbird_is_on_the_image():
    assert "thunderbird" in _packages()


def test_thunderbird_is_told_not_to_update_itself_or_report_or_ask_to_be_the_default_mail_program():
    import json
    policy = json.loads((PAYLOAD / "etc/thunderbird/policies/policies.json").read_text())["policies"]
    assert policy == {"DisableAppUpdate": True, "DisableTelemetry": True, "DontCheckDefaultClient": True}


def test_the_mail_service_is_a_user_unit_that_starts_at_login_and_is_never_given_up_on():
    unit = (USER_UNITS / "bombadil-mail.service").read_text()
    assert re.search(r"^ExecStart=/usr/bin/bombadil-mail$", unit, re.MULTILINE)
    assert re.search(r"^Restart=on-failure$", unit, re.MULTILINE)
    # Five quick failures (a locked disk at login) would otherwise leave mail stopped until the next login.
    assert re.search(r"^StartLimitIntervalSec=0$", unit, re.MULTILINE)
    assert re.search(r"^WantedBy=default\.target$", unit, re.MULTILINE)
    # Turned on by the package's own list, once, not by a link in the image.
    assert "bombadil-mail.service" in _enabled_units("user")


def test_the_package_links_mails_commands_into_usr_bin():
    _assert_the_package_links_in_usr_bin("bombadil-mail", "bombadil-mail-host")


def test_the_mail_skill_reaches_both_clis():
    skill = (ROOT / "share/skills/bombadil-mail/SKILL.md").read_text()
    assert skill.startswith("---\nname: bombadil-mail\ndescription: ")
    for tool in ("mail_search", "mail_read", "mail_mark", "mail_draft", "mail_show"):
        assert tool in skill
    skel = PAYLOAD / "etc/skel"
    for where in (".claude/skills", ".agents/skills"):
        link = skel / where / "bombadil-mail"
        assert link.is_symlink() and os.readlink(link) == "/usr/share/bombadil/share/skills/bombadil-mail"


def _persons_only_ops() -> set[str]:
    """The ops the mail service refuses to a process inside an agent's turn: those it lists in PERSONS_ONLY and
    those whose own code asks `_yours` (sending, looking at a draft, adding and removing an account)."""
    import ast
    import inspect

    from bombadil.mail import service
    ops = set(service.PERSONS_ONLY)
    for node in ast.walk(ast.parse(inspect.getsource(service))):
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("_op_") and any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr in ("_yours", "_yours_press") for c in ast.walk(node)):
            ops.add(node.name[4:])
    return ops


def test_the_mail_skill_never_tells_the_agent_to_do_what_only_the_person_may():
    skill = (ROOT / "share/skills/bombadil-mail/SKILL.md").read_text()
    persons = _persons_only_ops()
    assert {"add_account", "remove_account", "send", "set_flags", "draft_discard"} <= persons
    # The command line's words for the ops that are not their own names.
    cli = {"add": "add_account", "remove": "remove_account"}
    for paragraph in skill.split("\n\n"):
        for sub in re.findall(r"`bombadil mail (\w+)", paragraph):
            if cli.get(sub, sub) in persons:
                # Said only as something the person types, never as something to run.
                assert "themselves" in paragraph or "their own" in paragraph, paragraph
    # Nor an op by its own name, as a thing to call.
    assert [op for op in persons if re.search(rf"`{op}\b", skill)] == []


def test_the_mail_skill_is_in_the_users_home_on_the_built_image():
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    assert ("check mail-skill-installed as_user test -f /home/user/.claude/skills/bombadil-mail/SKILL.md "
            "-a -f /home/user/.agents/skills/bombadil-mail/SKILL.md") in smoke


def test_a_dev_session_keeps_all_of_its_mail_in_its_scratch_folder_the_socket_too():
    # Left at the runtime directory, agentd and the CLI would reach the real service when one runs there (the unit
    # on an installed Bombadil), and the fake could not start beside it.
    script = (ROOT / "scripts/dev-session.sh").read_text()
    for var in ("BOMBADIL_MAIL_DB", "BOMBADIL_MAIL_FILES", "BOMBADIL_PRESS_LOG", "BOMBADIL_MAIL_SOCKET"):
        assert re.search(rf'{var}="\$mail/[\w.]+"', script), var
    assert script.index('BOMBADIL_MAIL_SOCKET="$mail') < script.index('"$root/bin/agentd" &')


def _lua_string(source: str) -> str:
    return source.encode().decode("unicode_escape")


def test_thunderbirds_windows_go_silently_to_a_workspace_nobody_opens():
    lua = HYPR_LUA.read_text()
    rule = re.search(r'^hl\.window_rule\(\{ name = "mail-engine", match = \{ class = "([^"]+)" \}, '
                     r'workspace = "([^"]+)" \}\)$', lua, re.MULTILINE)
    assert rule, "no mail-engine window rule"
    pattern, workspace = _lua_string(rule.group(1)), rule.group(2)
    # Never focused ("silent") and never one of the panels Bombadil offers or toggles.
    assert workspace == "special:mail-engine silent"
    from bombadil import hypr
    assert "mail-engine" not in hypr.PANELS
    # Hyprland matches with RE2, where a leading (?i) means "without case". `Hyprland --verify-config` only
    # reads the Lua and does not compile the pattern, so nothing but this checks that RE2 takes it: real RE2 when
    # it is installed (google-re2), else Python's `re` after refusing what RE2 does not have (look-around, back
    # references, atomic groups, possessive quantifiers), since `re` would take those and Hyprland would not.
    assert pattern.startswith("(?i)")
    try:
        import re2
        window = re2.compile(pattern)
    except ImportError:
        assert not re.search(r"\(\?<?[=!]|\\[1-9]|\(\?P=|\(\?>|[*+?}]\+", pattern), pattern
        window = re.compile(pattern[4:], re.IGNORECASE)
    for cls in ("thunderbird", "Thunderbird", "org.mozilla.thunderbird", "org.mozilla.Thunderbird",
                "net.thunderbird.Thunderbird", "thunderbird-esr"):
        assert window.fullmatch(cls), cls
    for cls in ("bombadil-browser", "bombadil-app-mail", "foot", "Mail", "my-thunderbird"):
        assert not window.fullmatch(cls), cls


def test_the_session_hands_the_mail_unit_its_screen_before_thunderbird_needs_one():
    # The unit starts at login, before Hyprland has a screen; Thunderbird needs WAYLAND_DISPLAY.
    lua = HYPR_LUA.read_text()
    start = lua[lua.index('hl.on("hyprland.start"'):lua.index("hl.config(")]
    assert "systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE" in start
    assert "systemctl --user restart bombadil-mail.service" in start


def test_the_smoke_test_covers_mail_and_parses():
    import subprocess
    smoke = PAYLOAD / "usr/bin/bombadil-smoke"
    subprocess.run(["bash", "-n", str(smoke)], check=True)
    text = smoke.read_text()
    for name in ("mail-tools-installed", "mail-unit-enabled", "mail-unit-answers", "mail-engine-window-hidden",
                 "mail-engine-window-keeps-the-keyboard", "mail-skill-installed",
                 "mail-status", "mail-new-mail-is-a-notice", "mail-notice-on-the-line", "mail-word-opens-window",
                 "mail-window", "mail-unit-restored"):
        assert f"check {name} " in text, name
    # The fake engine is what the smoke runs on: no account exists on the ISO, and nothing may reach one.
    assert "BOMBADIL_MAIL_ENGINE=fake" in text


# -- Connections (docs/CONNECT.md): the service with its own user, the browser service, notifications without mako --

NOTIFICATION_DAEMONS = {"mako", "makoctl", "dunst", "swaync", "fnott", "xfce4-notifyd", "notification-daemon",
                        "mate-notification-daemon", "notify-osd"}
SYSTEM_UNITS = PAYLOAD / "usr/lib/systemd/system"
USER_UNITS = PAYLOAD / "usr/lib/systemd/user"


def _unit_keys(path: Path) -> dict[str, dict[str, list[str]]]:
    """A unit file as {section: {key: [values]}}; a key given twice keeps both values, as systemd does."""
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        else:
            key, _, value = line.partition("=")
            current.setdefault(key, []).append(value)
    return sections


def test_the_image_has_what_connections_run_on_and_no_notification_daemon():
    packages = _packages()
    # wl-clipboard is for "Copy and open" on a card from a notification, libnotify for notify-send.
    assert {"python-mcp", "python-httpx", "wl-clipboard", "libnotify"} <= packages
    assert packages & NOTIFICATION_DAEMONS == set()


def test_nothing_on_the_image_starts_or_configures_a_notification_daemon():
    # The shell's NotificationServer owns org.freedesktop.Notifications; a second owner would take web pages'
    # notifications from the line above the pill (mako, which this replaced, would have won the race at login).
    lua = HYPR_LUA.read_text()
    started = re.findall(r'^\s*hl\.exec_cmd\("([^"]+)"', lua, re.MULTILINE)
    # The session restarts the daemon and the bar as user services (docs/SESSION-SERVICES.md): the bar is the only
    # thing that owns the notification name, and it starts there.
    assert any(c.startswith("systemctl --user") and "restart" in c.split() and "bombadil-shell" in c.split()
               for c in started)
    assert [c for c in started if c.split()[0] in NOTIFICATION_DAEMONS] == []
    assert not (PAYLOAD / "etc/skel/.config/mako").exists()
    # The package's config, its units and greetd's, the live image's own units, and Bombadil's part of the Hyprland
    # config. (share/hypr/old holds lines that older versions of the file had, for homes that still have them.)
    trees = [PAYLOAD / "etc/skel/.config", PAYLOAD / "usr/lib/systemd", PAYLOAD / "etc/xdg",
             PAYLOAD / "usr/share/bombadil/greetd", ISO / "airootfs/etc/systemd"]
    shipped = [p for tree in trees for p in tree.rglob("*") if p.is_file() and not p.is_symlink()]
    shipped += [p for p in (ROOT / "share/hypr").glob("*") if p.is_file()]
    assert shipped
    DAEMON_WORD = r"\b(" + "|".join(NOTIFICATION_DAEMONS) + r")\b"
    named = [p.relative_to(ROOT).as_posix() for p in shipped
             if re.search(DAEMON_WORD,
                          "\n".join(line for line in p.read_text().splitlines()
                                    if not line.lstrip().startswith(("#", "--"))))]
    assert named == []
    # Nor a D-Bus service file that makes the bus start one when something asks for the name.
    assert [p for tree in (ISO, PAYLOAD) for p in tree.rglob("*") if "org.freedesktop.Notifications" in p.name] == []


def test_the_connection_service_is_a_system_unit_of_its_own_user_with_a_closed_state_directory(monkeypatch):
    import shlex

    from bombadil import paths
    unit = _unit_keys(SYSTEM_UNITS / "bombadil-connect.service")
    service = unit["Service"]

    def one(key):
        assert len(service[key]) == 1, key
        return service[key][0]

    assert one("User") == "bombadil-connect"
    assert one("ExecStart") == "/usr/bin/bombadil-connect"
    assert one("StateDirectory") == "bombadil-connect" and one("StateDirectoryMode") == "0700"
    # connect.sock is 0666 (the service checks each peer's uid itself), so the person has to be able to walk to it.
    assert one("RuntimeDirectory") == "bombadil-connect" and one("RuntimeDirectoryMode") == "0755"
    assert [one(k) for k in ("NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp")] == [
        "yes", "strict", "yes", "yes"]
    assert set(one("RestrictAddressFamilies").split()) == {"AF_UNIX", "AF_INET", "AF_INET6"}
    assert one("MemoryMax") == "300M" and one("Restart") == "on-failure"
    assert unit["Unit"]["StartLimitIntervalSec"] == ["0"]
    # The service reads /proc/<pid> and the cgroup of whoever presses, and listens for OAuth redirects on loopback:
    # each of these would make it refuse every press or deafen it.
    assert set(service) & {"ProtectProc", "ProcSubset", "PrivateUsers", "PrivatePIDs", "PrivateNetwork",
                           "DynamicUser"} == set()
    # Where the unit says its socket and state are is where agentd and the shell look by default.
    for var in ("BOMBADIL_CONNECT_SOCKET", "BOMBADIL_CONNECT_STATE", "BOMBADIL_CONNECT_ENGINE"):
        monkeypatch.delenv(var, raising=False)
    env = dict(pair.split("=", 1) for value in service["Environment"] for pair in shlex.split(value))
    assert Path(env["BOMBADIL_CONNECT_SOCKET"]) == paths.connect_socket()
    assert Path(env["BOMBADIL_CONNECT_STATE"]) == paths.connect_state()
    assert paths.connect_socket().parent == Path("/run") / one("RuntimeDirectory")
    assert paths.connect_state() == Path("/var/lib") / one("StateDirectory")
    assert "BOMBADIL_CONNECT_ENGINE" not in env, "the image runs the real engine"
    assert unit["Install"]["WantedBy"] == ["multi-user.target"]
    assert "bombadil-connect.service" in _enabled_units("system")


def test_the_connection_service_user_is_a_system_account_that_cannot_log_in_and_has_no_home(tmp_path):
    import shlex
    import shutil
    import subprocess
    conf = PAYLOAD / "usr/lib/sysusers.d/bombadil-connect.conf"
    lines = conf.read_text().splitlines()
    assert lines[0].startswith("#"), "a comment says what the account is for"
    entries = [shlex.split(line) for line in lines if line.strip() and not line.startswith("#")]
    assert len(entries) == 1
    kind, name, uid, comment, home, shell = entries[0]
    # `u` makes a system account with a group of its own and no login; `-` for the id takes one below 1000, so it is
    # never in the range of people the service lets in (1000 to 59999); `-` for home and shell is "/" and nologin.
    assert (kind, name, uid, home, shell) == ("u", "bombadil-connect", "-", "-", "-") and comment
    assert name == _unit_keys(SYSTEM_UNITS / "bombadil-connect.service")["Service"]["User"][0]
    if not shutil.which("systemd-sysusers"):
        return
    root = tmp_path / "root"
    (root / "usr/lib/sysusers.d").mkdir(parents=True)
    (root / "etc").mkdir()
    shutil.copy(conf, root / "usr/lib/sysusers.d")
    subprocess.run(["systemd-sysusers", f"--root={root}"], check=True, capture_output=True)
    fields = (root / "etc/passwd").read_text().strip().split(":")
    assert fields[0] == name and int(fields[2]) < 1000 and fields[5] == "/" and fields[6].endswith("nologin")
    assert (root / "etc/shadow").read_text().split(":")[1].startswith("!")


def test_the_browser_service_is_a_user_unit_that_starts_at_login_after_the_session_and_is_never_given_up_on():
    unit = _unit_keys(USER_UNITS / "bombadil-browserd.service")
    assert unit["Service"]["ExecStart"] == ["/usr/bin/bombadil-browserd"]
    assert unit["Service"]["Restart"] == ["on-failure"]
    assert unit["Unit"]["After"] == ["graphical-session.target"] and unit["Unit"]["StartLimitIntervalSec"] == ["0"]
    assert unit["Install"]["WantedBy"] == ["default.target"]
    assert "bombadil-browserd.service" in _enabled_units("user")
    # The browser it starts for a page is the person's, so it outlives a restart of the service.
    assert unit["Service"]["KillMode"] == ["process"]
    # It starts before there is a screen, but opening a page needs one (Chromium's display, the panel's compositor):
    # the session hands it both and starts it again, as it does for mail.
    lua = HYPR_LUA.read_text()
    start = lua[lua.index('hl.on("hyprland.start"'):]
    start = start[:start.index("\nend)")]
    line = next(part for part in start.split("hl.exec_cmd(") if "bombadil-browserd" in part)
    assert "import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE" in line
    assert line.index("import-environment") < line.index("restart bombadil-browserd.service")


def test_the_package_links_the_connection_services_commands_into_usr_bin():
    _assert_the_package_links_in_usr_bin("bombadil-connect", "bombadil-browserd")
    # What each unit starts is the link the package makes, to a command the tree ships and can run.
    for unit in (SYSTEM_UNITS / "bombadil-connect.service", USER_UNITS / "bombadil-browserd.service"):
        assert _unit_keys(unit)["Service"]["ExecStart"] == [f"/usr/bin/{unit.stem}"], unit
        assert os.access(ROOT / "bin" / unit.stem, os.X_OK), unit.stem


def _connect_skill() -> str:
    return (ROOT / "share/skills/bombadil-connect/SKILL.md").read_text()


def test_the_connect_skill_reaches_both_clis_and_names_the_tools_the_contract_gives():
    from bombadil.connect import driver
    skill = _connect_skill()
    assert skill.startswith("---\nname: bombadil-connect\ndescription: ")
    assert len(skill.splitlines()) < 70
    contract = (ROOT / "docs/CONNECT.md").read_text()
    start = contract.index("**The agent's tools**")
    tools = re.findall(r"^\| `(\w+)\(", contract[start:contract.index("A turn that has called", start)], re.MULTILINE)
    assert set(tools) == {"messages_unread", "message_thread", "tasks_waiting", "connections", "propose"}
    for tool in tools:
        assert f"`{tool} " in skill, tool
    for kind in driver.KINDS:
        assert f"`{kind}`" in skill, kind
    # The words that start a setup are the contract's, no more and no fewer.
    words = set(re.findall(r"`connect (\w+)`", contract[contract.index("**Launcher words**"):]))
    assert words == set(re.findall(r"`connect (\w+)`", skill)) and len(words) == 6
    skel = PAYLOAD / "etc/skel"
    for where in (".claude/skills", ".agents/skills"):
        link = skel / where / "bombadil-connect"
        assert link.is_symlink() and os.readlink(link) == "/usr/share/bombadil/share/skills/bombadil-connect"


def test_the_connect_skill_never_tells_the_agent_to_send_or_to_hold_a_key():
    from bombadil import launcher
    skill = _connect_skill()
    assert launcher.SEND_LINE in " ".join(skill.split()), "the answer to 'send it' is mail's sentence, word for word"
    # The service's ops that are the person's or a press's: named only as things not to look for, never as a call.
    assert [op for op in ("perform", "store_secret", "add_connection", "remove_connection")
            if re.search(rf"`{op}\b", skill)] == []
    # What the person types is said as theirs, never as something to run.
    for paragraph in skill.split("\n\n"):
        if re.search(r"`connect \w+`", paragraph):
            assert "themselves" in paragraph and "never run" in paragraph, paragraph
    # No token, nor the shape of one: a skill that shows one teaches the agent to look for it.
    assert not re.search(r"xox[a-z]-|xapp-|Bearer ", skill)
    for rule in ("other people's words", "no token", "never ask for one", "cannot send"):
        assert rule in " ".join(skill.lower().split()), rule


def test_the_connect_skill_is_in_the_users_home_on_the_built_image():
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    assert ("check connect-skill-installed as_user test -f /home/user/.claude/skills/bombadil-connect/SKILL.md "
            "-a -f /home/user/.agents/skills/bombadil-connect/SKILL.md") in smoke


def test_the_smoke_test_covers_connections_and_notifications():
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    for name in ("connect-tools-installed", "connect-python-installed", "mako-is-not-installed",
                 "connect-skill-installed", "connect-user-exists", "connect-unit-enabled", "connect-unit-active",
                 "connect-unit-hardened", "connect-unit-answers", "connect-state-is-closed", "browserd-unit-enabled",
                 "browserd-unit-active", "browserd-unit-answers", "mako-is-not-running",
                 "notifications-owner-is-the-shell", "notification-becomes-a-notice", "connect-fake-starts",
                 "connect-status", "connect-connections-listed", "connect-messages-listed", "connect-unit-restored"):
        assert f"check {name} " in smoke, name
    # The fake engine is what the live image's service runs on for the checks that need a connection, in a scratch
    # database, and the unit is put back as it was: no account exists on the ISO, and no sample may stay in the real one.
    assert "BOMBADIL_CONNECT_ENGINE=fake" in smoke and "smoke-fake" in smoke and "restore_connect" in smoke
    # What the smoke asks of the loaded unit is what the file says.
    unit = _unit_keys(SYSTEM_UNITS / "bombadil-connect.service")["Service"]
    asked = re.search(r"for want in ([^;]+); do", smoke).group(1).split()
    assert {"User", "NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp", "RuntimeDirectoryMode",
            "MemoryMax"} == {w.split("=")[0] for w in asked}
    for want in asked:
        key, value = want.split("=")
        assert unit[key] == [{"MemoryMax": "300M"}.get(key, value)], want
    assert f"MemoryMax={300 << 20}" in asked


def test_a_dev_session_runs_connections_on_the_fake_engine_in_its_scratch_folder_the_socket_too():
    # The same reasoning as for mail: at the system unit's socket, agentd would reach a real service, and the fake
    # could not start beside it. The service has to be up before agentd looks, and agentd has to have the variables.
    script = (ROOT / "scripts/dev-session.sh").read_text()
    for var in ("BOMBADIL_CONNECT_ENGINE=fake", "BOMBADIL_CONNECT_SOCKET", "BOMBADIL_CONNECT_STATE",
                "BOMBADIL_CONNECT_UIDS"):
        assert var in script, var
    assert re.search(r'BOMBADIL_CONNECT_SOCKET="\$connect/[\w.]+"', script)
    assert script.index('BOMBADIL_CONNECT_SOCKET="$connect') < script.index('"$root/bin/bombadil-connect" &')
    assert script.index('"$root/bin/bombadil-connect" &') < script.index('"$root/bin/agentd" &')
    import subprocess
    subprocess.run(["bash", "-n", str(ROOT / "scripts/dev-session.sh")], check=True)


def test_the_desktop_test_starts_connections_on_the_fake_engine_before_agentd_with_the_shell_on_a_session_bus():
    driver = (ROOT / "tests/desktop/driver.py").read_text()
    for want in ('BOMBADIL_CONNECT_ENGINE="fake"', 'BOMBADIL_CONNECT_UIDS="0-65535"', "BOMBADIL_CONNECT_SOCKET=",
                 "BOMBADIL_CONNECT_STATE=", "DBUS_SESSION_BUS_ADDRESS="):
        assert want in driver, want
    # The bus, then the service, then agentd and the shell, which all take the same environment.
    order = [driver.index(s) for s in ('start("dbus"', 'start("connect"', 'start("agentd"', 'start("quickshell"')]
    assert order == sorted(order)


# -- the smoke's picture checks, in a guest with no KVM --

GAVE_UP = "Could not read the network: ip did not answer."
DRAWN = "Your network is up.\\n(shown above the bar)"
BOOT_DRAWN = "What takes longest when you boot\\n2.5 s: tmpfiles\\n2.1 s: nsresourced\\n1 s: NetworkManager\\n(shown above the bar)"


def _run_picture(tmp_path, answers, kind="network", target=""):
    """The smoke's own picture() function, in bash, with os-mcp stood in for by `answers` (one per try,
    the last one repeated). Returns (exit code, the scales each try asked for, what wait_for was given)."""
    import subprocess
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    start = smoke.index("picture() {")
    body = smoke[start:smoke.index("\n}\n", start) + 3]
    (tmp_path / "answers").write_text("\n".join(answers) + "\n")
    script = f'''
d={tmp_path}
say() {{ echo "SAY $*"; }}
wait_for() {{ echo "$*" >>"$d/waits"; return 0; }}
calm() {{ return 0; }}
as_user() {{
  cat >/dev/null
  local n; n=$(( $(cat "$d/count" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$d/count"
  echo "$2" >>"$d/scales"
  local line; line=$(sed -n "${{n}}p" "$d/answers"); [[ -n "$line" ]] || line=$(tail -1 "$d/answers")
  echo "$line"
}}
{body}
picture {kind} {target}
echo "RC=$?"
'''
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    rc = int(re.search(r"RC=(\d+)", r.stdout).group(1))
    read = lambda name: (tmp_path / name).read_text().split() if (tmp_path / name).exists() else []
    return rc, read("scales"), read("waits"), r.stderr


def test_a_capture_that_gave_up_in_a_busy_guest_is_asked_again_with_more_time(tmp_path):
    rc, scales, waits, log = _run_picture(tmp_path, [GAVE_UP, GAVE_UP, DRAWN])
    assert rc == 0
    assert scales == ["BOMBADIL_BUDGET_SCALE=1", "BOMBADIL_BUDGET_SCALE=4", "BOMBADIL_BUDGET_SCALE=12"]
    assert waits == ["90", "calm", "90", "calm"]                 # the guest is let settle before each new try
    assert "(try 3, 12x the time)" in log and "ip did not answer" in log


def test_a_capture_that_works_the_first_time_asks_once_with_the_usual_time(tmp_path):
    rc, scales, waits, log = _run_picture(tmp_path, [DRAWN])
    assert (rc, scales, waits) == (0, ["BOMBADIL_BUDGET_SCALE=1"], [])
    assert "(try 1, 1x the time)" in log


def test_a_capture_that_never_answers_fails_after_three_tries(tmp_path):
    rc, scales, _, _ = _run_picture(tmp_path, [GAVE_UP])
    assert rc == 1 and len(scales) == 3


def test_a_picture_in_the_wrong_shape_fails_at_once_and_is_not_asked_again(tmp_path):
    rc, scales, waits, _ = _run_picture(tmp_path, ["something else entirely"])
    assert (rc, len(scales), waits) == (1, 1, [])
    # The boot must still be more than its last line, whatever the wait.
    rc, scales, _, _ = _run_picture(tmp_path / "b", ["The boot took 10 s.\\n10.1 s: graphical.target\\n(shown above the bar)"], "boot") \
        if (tmp_path / "b").mkdir() is None else (None, None, None, None)
    assert rc == 1 and len(scales) == 1
    (tmp_path / "c").mkdir()
    assert _run_picture(tmp_path / "c", [GAVE_UP, BOOT_DRAWN], "boot")[0] == 0


def test_the_smoke_waits_for_a_calm_guest_before_its_pictures_and_defines_calm_once():
    smoke = (PAYLOAD / "usr/bin/bombadil-smoke").read_text()
    assert smoke.count("calm() {") == 1
    waited = smoke.index('wait_for 240 calm || say "NOTE the machine was still busy when the pictures were taken"')
    assert smoke.index("calm() {") < waited < smoke.index("for kind in network boot disks sound screens")
    assert smoke.index("calm() {") < smoke.index('wait_for 240 calm || say "NOTE the machine was still busy when the keys went in"')
    assert "BOMBADIL_BUDGET_SCALE" in smoke
