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
