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


# -- Connections (docs/CONNECT.md): the service with its own user, the browser service, notifications without mako --

NOTIFICATION_DAEMONS = {"mako", "makoctl", "dunst", "swaync", "fnott", "xfce4-notifyd", "notification-daemon",
                        "mate-notification-daemon", "notify-osd"}
SYSTEM_UNITS = ISO / "airootfs/etc/systemd/system"
USER_UNITS = ISO / "airootfs/etc/systemd/user"


def _unit(path: Path) -> dict[str, dict[str, list[str]]]:
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


def _enabled_by(link: Path, unit: str) -> bool:
    return link.is_symlink() and os.readlink(link) == f"../{unit}" and (link.parent / os.readlink(link)).resolve().is_file()


def test_the_image_has_what_connections_run_on_and_no_notification_daemon():
    packages = _packages()
    # wl-clipboard is for "Copy and open" on a card from a notification, libnotify for notify-send.
    assert {"python-mcp", "python-httpx", "wl-clipboard", "libnotify"} <= packages
    assert packages & NOTIFICATION_DAEMONS == set()


def test_nothing_on_the_image_starts_or_configures_a_notification_daemon():
    # The shell's NotificationServer owns org.freedesktop.Notifications; a second owner would take web pages'
    # notifications from the line above the pill (mako, which this replaced, would have won the race at login).
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
    started = re.findall(r'^\s*hl\.exec_cmd\("([^"]+)"', lua, re.MULTILINE)
    assert "bombadil-shell" in started
    assert [c for c in started if c.split()[0] in NOTIFICATION_DAEMONS] == []
    assert not (ISO / "airootfs/etc/skel/.config/mako").exists()
    shipped = [p for tree in ("etc/skel/.config", "etc/systemd", "etc/xdg", "etc/greetd") for p in
               (ISO / "airootfs" / tree).rglob("*") if p.is_file() and not p.is_symlink()]
    assert shipped
    named = [p.relative_to(ISO).as_posix() for p in shipped
             if re.search(r"\b(%s)\b" % "|".join(NOTIFICATION_DAEMONS),
                          "\n".join(line for line in p.read_text().splitlines()
                                    if not line.lstrip().startswith(("#", "--"))))]
    assert named == []
    # Nor a D-Bus service file that makes the bus start one when something asks for the name.
    assert [p for p in ISO.rglob("*") if "org.freedesktop.Notifications" in p.name] == []


def test_the_connection_service_is_a_system_unit_of_its_own_user_with_a_closed_state_directory(monkeypatch):
    import shlex

    from bombadil import paths
    unit = _unit(SYSTEM_UNITS / "bombadil-connect.service")
    service = unit["Service"]

    def one(key):
        assert len(service[key]) == 1, key
        return service[key][0]

    assert one("User") == "bombadil-connect"
    assert one("ExecStart") == "/usr/local/bin/bombadil-connect"
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
    assert _enabled_by(SYSTEM_UNITS / "multi-user.target.wants/bombadil-connect.service", "bombadil-connect.service")


def test_the_connection_service_user_is_a_system_account_that_cannot_log_in_and_has_no_home(tmp_path):
    import shlex
    import shutil
    import subprocess
    conf = ISO / "airootfs/usr/lib/sysusers.d/bombadil-connect.conf"
    lines = conf.read_text().splitlines()
    assert lines[0].startswith("#"), "a comment says what the account is for"
    entries = [shlex.split(line) for line in lines if line.strip() and not line.startswith("#")]
    assert len(entries) == 1
    kind, name, uid, comment, home, shell = entries[0]
    # `u` makes a system account with a group of its own and no login; `-` for the id takes one below 1000, so it is
    # never in the range of people the service lets in (1000 to 59999); `-` for home and shell is "/" and nologin.
    assert (kind, name, uid, home, shell) == ("u", "bombadil-connect", "-", "-", "-") and comment
    assert name == _unit(SYSTEM_UNITS / "bombadil-connect.service")["Service"]["User"][0]
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
    unit = _unit(USER_UNITS / "bombadil-browserd.service")
    assert unit["Service"]["ExecStart"] == ["/usr/local/bin/bombadil-browserd"]
    assert unit["Service"]["Restart"] == ["on-failure"]
    assert unit["Unit"]["After"] == ["graphical-session.target"] and unit["Unit"]["StartLimitIntervalSec"] == ["0"]
    assert unit["Install"]["WantedBy"] == ["default.target"]
    assert _enabled_by(USER_UNITS / "default.target.wants/bombadil-browserd.service", "bombadil-browserd.service")
    # It starts before there is a screen and needs none: nothing in the session hands it a display.
    lua = (ISO / "airootfs/etc/skel/.config/hypr/hyprland.lua").read_text()
    assert "bombadil-browserd" not in lua


def test_the_build_links_the_connection_services_commands_into_usr_local_bin():
    build = (ROOT / "scripts/build-iso.sh").read_text()
    names = re.search(r"^for b in ([^;]+); do$", build, re.MULTILINE).group(1).split()
    assert {"bombadil-connect", "bombadil-browserd"} <= set(names)
    # What each unit starts is the link the build makes, to a command the tree ships and can run.
    for unit in (SYSTEM_UNITS / "bombadil-connect.service", USER_UNITS / "bombadil-browserd.service"):
        assert _unit(unit)["Service"]["ExecStart"] == [f"/usr/local/bin/{unit.stem}"], unit
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
    skel = ISO / "airootfs/etc/skel"
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
    smoke = (ISO / "airootfs/usr/local/bin/bombadil-smoke").read_text()
    assert ("check connect-skill-installed as_user test -f /home/user/.claude/skills/bombadil-connect/SKILL.md "
            "-a -f /home/user/.agents/skills/bombadil-connect/SKILL.md") in smoke


def test_the_smoke_test_covers_connections_and_notifications():
    smoke = (ISO / "airootfs/usr/local/bin/bombadil-smoke").read_text()
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
    unit = _unit(SYSTEM_UNITS / "bombadil-connect.service")["Service"]
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
