"""What the ISO profile ships. The VM smoke proves a booted machine; these catch a dropped line sooner."""
import configparser
import os
import re
import subprocess
from pathlib import Path

ISO = Path(__file__).resolve().parent.parent / "iso"
ROOT = ISO.parent
USER_UNITS = ISO / "airootfs/etc/systemd/user"
SMOKE = ISO / "airootfs/usr/local/bin/bombadil-smoke"


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
    binds = re.findall(r'^\s*hl\.bind\("([^"]+)", hl\.dsp\.exec_cmd\("bombadil pill"\)', lua, re.M)
    assert {"SUPER + SUPER_L", "SUPER + SUPER_R", "ALT + space"} <= set(binds)


def test_the_agent_is_told_a_replaced_kernel_needs_a_restart():
    # modprobe of a module (overlay, br_netfilter, docker's) fails after pacman -Syu replaced the running kernel.
    from bombadil import providers
    assert "If an upgrade replaced the kernel, tell the user a restart is needed" in providers.system_prompt()


def _unit(path: Path) -> configparser.ConfigParser:
    """A systemd unit or drop-in: sections of Key=Value, keys case-sensitive, `#` and `;` comment lines."""
    unit = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",))
    unit.optionxform = str
    unit.read_string(path.read_text())
    return unit


def _linked_programs() -> list[str]:
    """The programs build-iso.sh links into /usr/local/bin, each to its copy in /usr/share/bombadil/bin."""
    build = (ROOT / "scripts/build-iso.sh").read_text()
    loop = re.search(r'^for b in (.+); do\n\s*ln -sfn "/usr/share/bombadil/bin/\$b" '
                     r'"[^"]*/usr/local/bin/\$b"\ndone$', build, re.MULTILINE)
    assert loop, "build-iso.sh no longer links bin/* into /usr/local/bin the way this test reads it"
    return loop.group(1).split()


def test_the_prober_is_a_user_unit_that_only_looks_and_only_when_nothing_else_wants_the_machine():
    unit = _unit(USER_UNITS / "bombadil-probe.service")
    service = unit["Service"]
    assert unit["Unit"]["Description"].strip()
    assert service["ExecStart"] == "/usr/local/bin/bombadil-probe"
    assert (service["Type"], service["Restart"], service["RestartSec"]) == ("simple", "always", "5")
    assert (service["Nice"], service["CPUSchedulingPolicy"]) == ("15", "idle")
    assert service["IOSchedulingClass"] == "idle"
    # systemd reads StartLimitIntervalSec from [Unit]; under [Service] it is ignored, and a few quick
    # failures would leave the prober down for good.
    assert unit["Unit"]["StartLimitIntervalSec"] == "0" and "StartLimitIntervalSec" not in service


def test_the_prober_stops_with_its_process_group():
    # KillMode=process (agentd's, so its apps outlive it) would leave behind whatever the prober ran.
    service = _unit(USER_UNITS / "bombadil-probe.service")["Service"]
    assert service.get("KillMode", "control-group") == "control-group"


def test_the_prober_is_not_tied_to_agentd_because_it_has_to_outlive_a_crash_to_see_it():
    unit = _unit(USER_UNITS / "bombadil-probe.service")
    for section in unit.sections():
        for key in ("PartOf", "BindsTo", "Requires", "Requisite", "Upholds", "Conflicts", "After", "Before"):
            assert "agentd" not in unit[section].get(key, ""), f"{section} {key}"


def test_agentd_starts_the_prober_through_a_drop_in_that_only_wants_it():
    # The agentd and bar units and hyprland.lua are owned elsewhere; Wants= pulls the prober in when agentd
    # starts and ties neither unit's restarts to the other.
    drop = _unit(USER_UNITS / "bombadil-agentd.service.d" / "probe.conf")
    assert drop.sections() == ["Unit"] and dict(drop["Unit"]) == {"Wants": "bombadil-probe.service"}
    assert (USER_UNITS / "bombadil-agentd.service").exists()
    assert (USER_UNITS / "bombadil-probe.service").exists()


def test_the_prober_program_is_shipped_where_its_unit_runs_it():
    assert "bombadil-probe" in _linked_programs()
    program = ROOT / "bin" / "bombadil-probe"
    assert os.access(program, os.X_OK)             # mkarchiso copies without modes; profiledef.sh sets them
    assert program.read_text().startswith("#!/usr/bin/env python3")
    assert "from bombadil.loop.runner import main" in program.read_text()


def test_every_user_unit_runs_a_program_that_build_iso_links():
    linked = _linked_programs()
    for path in USER_UNITS.glob("*.service"):
        start = _unit(path)["Service"]["ExecStart"].split()[0]
        if start.startswith("/usr/local/bin/"):
            assert start.rsplit("/", 1)[1] in linked, path.name


def test_the_smoke_test_is_valid_bash_and_checks_the_loop_commands_where_the_loop_is_checked():
    subprocess.run(["bash", "-n", str(SMOKE)], check=True)
    text = SMOKE.read_text()
    block = text[text.index("# Bombadil's own checks (the self-improvement loop)"):
                 text.index('if [[ "$mode" == "install" ]]')]
    names = ("loop-probe-unit-active", "loop-status-answers", "loop-doctor-live", "loop-asks-tool")
    at = [block.index(f"check {n} ") for n in names]
    assert at == sorted(at) and block.index("check coredump-handler") < at[0]
    assert "as_user systemctl --user is-active --quiet bombadil-probe.service" in block
    assert "as_user timeout 60 bombadil loop status" in block
    assert re.search(r"as_user timeout \d+ bombadil doctor --live", block)    # up to a minute of looking
    assert "mcp_call asks '{}'" in block
