"""The stretch between the boot loader and the desk: a quiet console in the design system's ground.

The live system's boot entry and an installed system's GRUB drop-in carry the same kernel parameters,
and both are the output of scripts/console-palette.py. greetd keeps Hyprland's start-up text off
the console. Hyprland's own background is the opaque ground, not clear.
"""

import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path

from qml_theme import THEME

ROOT = Path(__file__).resolve().parents[1]
ISO = ROOT / "iso"
ENTRIES = ISO / "efiboot" / "loader" / "entries"
GRUB_DROPINS = ISO / "airootfs" / "etc" / "default" / "grub.d"
DROPIN = GRUB_DROPINS / "zz-bombadil-console.cfg"


def palette_script():
    spec = importlib.util.spec_from_file_location("console_palette", ROOT / "scripts" / "console-palette.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kernel_options(text):
    """The kernel parameters a boot entry or a GRUB drop-in sets, as one string."""
    m = re.search(r"^options\s+(.*)$", text, re.MULTILINE) \
        or re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="(.*)"$', text, re.MULTILINE)
    assert m, text
    return m.group(1)


def colour_params(options):
    return re.findall(r"vt\.default_(?:red|grn|blu)=\S+", options)


QUIET = ("quiet", "loglevel=3", "systemd.show_status=error", "rd.udev.log_level=3",
         "vt.global_cursor_default=0", "logo.nologo")


def test_the_palette_is_the_design_systems_with_the_ground_as_colour_zero():
    pal = palette_script()
    cols = dict(enumerate(pal.colours()))
    assert len(cols) == 16
    # colour 0 is the console's background, 7 its text: the desk's ground and primary ink
    assert cols[0][1] == THEME["bg"][1:] and cols[7][1] == THEME["fg"][1:]
    # status colours are the system's
    status = tuple(THEME[k][1:] for k in ("bad", "good", "warn", "info"))
    assert (cols[1][1], cols[2][1], cols[3][1], cols[4][1]) == status
    params = pal.parameters()
    for name in ("red", "grn", "blu"):
        values = re.search(rf"vt\.default_{name}=(\S+)", params).group(1).split(",")
        assert len(values) == 16 and all(re.fullmatch(r"0x[0-9a-f]{2}", v) for v in values)
    assert re.search(r"vt\.default_red=0x10,", params) and re.search(r"vt\.default_grn=0x12,", params) \
        and re.search(r"vt\.default_blu=0x14,", params)


def test_the_live_boot_entry_is_quiet_and_in_the_ground_colour():
    options = kernel_options((ENTRIES / "01-bombadil.conf").read_text())
    for want in QUIET:
        assert want in options.split(), want
    assert " ".join(colour_params(options)) == palette_script().parameters()


def test_the_serial_smoke_entries_keep_their_kernel_output():
    # They are read on the serial console by the VM tests: no quiet, no palette.
    for name in ("02-bombadil-serial.conf", "03-bombadil-install-test.conf"):
        options = kernel_options((ENTRIES / name).read_text()).split()
        assert "quiet" not in options and not colour_params(" ".join(options)), name


def test_an_installed_system_gets_the_same_parameters_from_a_grub_drop_in():
    options = kernel_options(DROPIN.read_text())
    assert options.startswith("$GRUB_CMDLINE_LINUX_DEFAULT ")   # adds to the line, never replaces it
    for want in QUIET:
        assert want in options.split(), want
    assert " ".join(colour_params(options)) == palette_script().parameters()
    live = kernel_options((ENTRIES / "01-bombadil.conf").read_text())
    assert [p for p in options.split()[1:]] == [p for p in live.split()[live.split().index("quiet"):]]


def test_the_grub_drop_in_adds_to_the_line_and_sorts_after_every_other_drop_in():
    # grub-mkconfig sources /etc/default/grub, then each /etc/default/grub.d/*.cfg in name order.
    names = sorted(p.name for p in GRUB_DROPINS.glob("*.cfg"))
    assert names[-1] == DROPIN.name
    for base in ('"loglevel=3 quiet"', '""'):
        out = subprocess.run(
            ["sh", "-c",
             f'GRUB_CMDLINE_LINUX_DEFAULT={base}; . {DROPIN}; printf %s "$GRUB_CMDLINE_LINUX_DEFAULT"'],
            capture_output=True, text=True, check=True).stdout
        assert out.strip().startswith(base.strip('"')) and "vt.default_red=0x10," in out
        assert out.count("quiet") == base.count("quiet") + 1


def test_the_installer_copies_the_live_etc_so_the_drop_in_arrives_with_it():
    # bombadil-install copies the image (or, with none mounted, the running system), /etc/default/grub.d
    # included, and removes only the files it names.
    script = (ISO / "airootfs" / "usr" / "local" / "bin" / "bombadil-install").read_text()
    copy = script.split("copy_system() {")[1].split("\n}\n")[0]
    after_copy = copy.split('cp -a "$image/." "$target/"')[1]
    assert 'cp -a "$image/." "$target/"' in copy and "cp -ax /. " in copy and "grub.d" not in after_copy


def test_greetd_keeps_hyprlands_start_up_text_off_the_console():
    cfg = tomllib.loads((ISO / "airootfs" / "etc" / "greetd" / "config.toml").read_text())
    assert cfg["terminal"]["vt"] == 1
    for session in ("default_session", "initial_session"):
        command = cfg[session]["command"]
        assert "start-hyprland" in command and "systemd-cat" in command, (session, command)
        assert cfg[session]["user"] == "user"


def test_hyprlands_own_background_is_the_opaque_ground():
    # 0xAARRGGBB: 0x101214 has alpha 00, which Hyprland draws as black (the desk was black on every boot).
    lua = (ISO / "airootfs" / "etc" / "skel" / ".config" / "hypr" / "hyprland.lua").read_text()
    m = re.search(r"^\s*background_color\s*=\s*0x([0-9a-fA-F]{8})\b", lua, re.MULTILINE)
    assert m, "background_color must be a 0xAARRGGBB number"
    assert m.group(1).lower() == "ff" + THEME["bg"][1:]
