"""What the ISO profile ships. The VM smoke proves a booted machine; these catch a dropped line sooner."""
import os
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
    # An install that updates everything is said out loud, not done quietly.
    assert "That also updates every other package, so say so in one plain sentence" in providers.system_prompt()


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
    binds = re.findall(r'^\s*hl\.bind\("([^"]+)", hl\.dsp\.exec_cmd\("bombadil pill"\)', lua, re.MULTILINE)
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
        # (systemd reads the key from [Unit] and ignores it under [Service].)
        assert u["Unit"]["StartLimitIntervalSec"] == "0", path.name
        assert "StartLimitIntervalSec" not in u["Service"], path.name
    # Apps the agent started keep running when only the daemon restarts.
    assert _unit(units[0])["Service"]["KillMode"] == "process"
    assert _unit(units[0])["Service"]["ExecStart"] == "/usr/local/bin/agentd"
    assert _unit(units[1])["Service"]["ExecStart"] == "/usr/local/bin/bombadil-shell"


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


def test_the_smokes_sign_in_block_never_runs_where_a_provider_is_already_chosen():
    # It picks other providers, cancels their logins and deletes the config file at its end.
    smoke = (ISO / "airootfs/usr/local/bin/bombadil-smoke").read_text()
    gate = 'if [[ "$mode" != "undo" && ! -e "$signin_config" ]]; then'
    assert 'signin_config=/home/user/.config/bombadil/config.toml' in smoke and gate in smoke
    assert smoke.index(gate) < smoke.index("check signin-agentd-starts") < smoke.index("rm -f /home/user/.fake-signin /home/user/.config/bombadil/config.toml")
    # The gate is one block: what the fresh ISO runs is inside it, up to the install step.
    assert smoke.index(gate) < smoke.index('if [[ "$mode" == "install" ]]; then')
    host = (ISO.parent / "scripts/test-vm.sh").read_text()
    assert "SKIP signin" in smoke and "SKIP signin" in host


def _run_gate(home, with_config):
    """The gate's own lines, run in bash against a home with or without a config file."""
    import subprocess
    smoke = (ISO / "airootfs/usr/local/bin/bombadil-smoke").read_text()
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
    smoke = (ISO / "airootfs/usr/local/bin/bombadil-smoke").read_text()
    # systemd starts a killed agentd again in the normal environment, so the smoke's own agentd
    # (another provider, a fake login) needs the unit stopped first, and the unit back after.
    restart = smoke[smoke.index("restart_agentd() {"):smoke.index("page_or_offline()")]
    assert restart.index("systemctl --user stop bombadil-agentd") < restart.index("setsid -f agentd")
    assert "systemctl --user start bombadil-agentd" in smoke and "check agentd-restored agentd_unit_again" in smoke
    # as_user is a shell function: a `bash -c` would not find it.
    assert "check services-are-units units_active" in smoke
    assert "bash -c 'for u in bombadil-agentd" not in smoke


# -- Mail (docs/MAIL.md): Thunderbird as the unseen engine, and the service in front of it --

ROOT = ISO.parent


def test_thunderbird_is_on_the_image():
    assert "thunderbird" in _packages()


def test_thunderbird_is_told_not_to_update_itself_or_report_or_ask_to_be_the_default_mail_program():
    import json
    policy = json.loads((ISO / "airootfs/etc/thunderbird/policies/policies.json").read_text())["policies"]
    assert policy == {"DisableAppUpdate": True, "DisableTelemetry": True, "DontCheckDefaultClient": True}


def test_the_mail_service_is_a_user_unit_that_starts_at_login_and_is_never_given_up_on():
    units = ISO / "airootfs/etc/systemd/user"
    unit = (units / "bombadil-mail.service").read_text()
    assert re.search(r"^ExecStart=/usr/local/bin/bombadil-mail$", unit, re.MULTILINE)
    assert re.search(r"^Restart=on-failure$", unit, re.MULTILINE)
    # Five quick failures (a locked disk at login) would otherwise leave mail stopped until the next login.
    assert re.search(r"^StartLimitIntervalSec=0$", unit, re.MULTILINE)
    assert re.search(r"^WantedBy=default\.target$", unit, re.MULTILINE)
    link = units / "default.target.wants/bombadil-mail.service"
    assert link.is_symlink() and os.readlink(link) == "../bombadil-mail.service"
    assert (link.parent / os.readlink(link)).resolve().is_file()


def test_the_build_links_mails_commands_into_usr_local_bin():
    build = (ROOT / "scripts/build-iso.sh").read_text()
    names = re.search(r"^for b in ([^;]+); do$", build, re.MULTILINE).group(1).split()
    assert {"bombadil-mail", "bombadil-mail-host"} <= set(names)
    # Each link points at a file the tree ships (the loop's other names are checked here too).
    assert [n for n in names if not (ROOT / "bin" / n).is_file()] == [], "linked, but not in bin/"
    assert 'ln -sfn "/usr/share/bombadil/bin/$b" "$profile/airootfs/usr/local/bin/$b"' in build


def test_the_mail_skill_reaches_both_clis():
    skill = (ROOT / "share/skills/bombadil-mail/SKILL.md").read_text()
    assert skill.startswith("---\nname: bombadil-mail\ndescription: ")
    for tool in ("mail_search", "mail_read", "mail_mark", "mail_draft", "mail_show"):
        assert tool in skill
    skel = ISO / "airootfs/etc/skel"
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
    smoke = (ISO / "airootfs/usr/local/bin/bombadil-smoke").read_text()
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
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
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
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
    start = lua[lua.index('hl.on("hyprland.start"'):lua.index("hl.config(")]
    assert "systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE" in start
    assert "systemctl --user restart bombadil-mail.service" in start


def test_the_smoke_test_covers_mail_and_parses():
    import subprocess
    smoke = ISO / "airootfs/usr/local/bin/bombadil-smoke"
    subprocess.run(["bash", "-n", str(smoke)], check=True)
    text = smoke.read_text()
    for name in ("mail-tools-installed", "mail-unit-enabled", "mail-unit-answers", "mail-engine-window-hidden",
                 "mail-engine-window-keeps-the-keyboard", "mail-skill-installed",
                 "mail-status", "mail-new-mail-is-a-notice", "mail-notice-on-the-line", "mail-word-opens-window",
                 "mail-window", "mail-unit-restored"):
        assert f"check {name} " in text, name
    # The fake engine is what the smoke runs on: no account exists on the ISO, and nothing may reach one.
    assert "BOMBADIL_MAIL_ENGINE=fake" in text
