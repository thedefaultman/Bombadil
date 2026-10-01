import json
import subprocess
import sys
from pathlib import Path

import pytest

from bombadil import installplan
from bombadil.installplan import PlanError, validate

CARRY = [".claude", ".claude.json", ".codex", ".config/bombadil", ".local/state/bombadil", "Apps", ".config/chromium"]


@pytest.fixture
def zones(tmp_path):
    z = tmp_path / "zoneinfo"
    (z / "leapseconds").parent.mkdir(parents=True, exist_ok=True)
    (z / "leapseconds").write_text("# Leap seconds")
    (z / "dangling").symlink_to(z / "nothing")
    for name in ("UTC", "Europe/Lisbon", "America/Vancouver"):
        (z / name).parent.mkdir(parents=True, exist_ok=True)
        (z / name).write_bytes(b"TZif2" + b"\0" * 40)
    return z


def plan(zones, **fields):
    return validate(fields, carry_list=CARRY, zoneinfo=zones)


def test_a_bare_disk_is_a_fresh_install_with_everything_carried_and_no_zone_chosen(zones):
    p = plan(zones, disk="/dev/vda")
    assert p["mode"] == "fresh" and p["carry"] == CARRY
    assert (p["timezone"], p["timezone_source"]) == ("UTC", "unset")
    assert p["encrypt"] is None and p["hostname"] is None and p["keymap"] is None


def test_the_command_line_disk_and_mode_fill_what_the_plan_leaves_out(zones):
    p = validate({}, carry_list=CARRY, zoneinfo=zones, disk="/dev/nvme0n1", mode="refresh")
    assert (p["disk"], p["mode"]) == ("/dev/nvme0n1", "refresh")
    p = validate({"disk": "/dev/vda", "mode": "fresh"}, carry_list=CARRY, zoneinfo=zones, disk="/dev/vda", mode="fresh")
    assert (p["disk"], p["mode"]) == ("/dev/vda", "fresh")


def test_a_plan_that_disagrees_with_the_command_line_is_refused_not_obeyed(zones):
    # A wipe of the plan's disk where a refresh of another was typed is the mistake this prevents.
    with pytest.raises(PlanError, match="disk: the plan says"):
        validate({"disk": "/dev/sda"}, carry_list=CARRY, zoneinfo=zones, disk="/dev/sdb")
    with pytest.raises(PlanError, match="mode: the plan says"):
        validate({"disk": "/dev/sda", "mode": "fresh"}, carry_list=CARRY, zoneinfo=zones, mode="refresh")


def test_a_time_zone_and_where_it_came_from(zones):
    p = plan(zones, disk="/dev/vda", timezone="Europe/Lisbon", timezone_source="detected")
    assert (p["timezone"], p["timezone_source"]) == ("Europe/Lisbon", "detected")
    # A zone with no word on where it came from was chosen by someone.
    assert plan(zones, disk="/dev/vda", timezone="America/Vancouver")["timezone_source"] == "chosen"
    assert plan(zones, disk="/dev/vda", timezone_source="unset")["timezone"] == "UTC"


@pytest.mark.parametrize("fields", [
    {"timezone": "Mars/Olympus"},
    {"timezone": "UTC/"},
    {"timezone": "Europe"},
    {"timezone": "leapseconds"},
    {"timezone": "../etc/passwd"},
    {"timezone": "/usr/share/zoneinfo/UTC"},
    {"timezone_source": "detected"},
    {"timezone": "UTC", "timezone_source": "guessed"},
])
def test_a_time_zone_the_system_does_not_know_is_refused(zones, fields):
    with pytest.raises(PlanError):
        plan(zones, disk="/dev/vda", **fields)


@pytest.mark.parametrize("disk", [None, "", "vda", "/dev/../etc/passwd", "/dev/vda; rm -rf /", "/dev/vda\n", "/tmp/disk",
                                  "/dev/$(id)", 3])
def test_only_a_device_under_dev_is_a_disk(zones, disk):
    with pytest.raises(PlanError):
        plan(zones, disk=disk)


def test_the_password_is_never_part_of_a_plan(zones):
    with pytest.raises(PlanError, match="file descriptor"):
        plan(zones, disk="/dev/vda", password="hunter2")


def test_unknown_fields_are_refused_so_a_typo_does_not_quietly_change_nothing(zones):
    with pytest.raises(PlanError, match="unknown plan field: encrpyt"):
        plan(zones, disk="/dev/vda", encrpyt=True)


@pytest.mark.parametrize("fields", [
    {"mode": "reinstall"}, {"encrypt": "yes"}, {"kind": "cdrom"}, {"hostname": "Has Space"}, {"hostname": "-lead"},
    {"hostname": "x" * 64}, {"keymap": "us;ls"}, {"keymap": ""},
])
def test_every_field_is_checked(zones, fields):
    with pytest.raises(PlanError):
        plan(zones, disk="/dev/vda", **fields)


def test_what_comes_along_is_a_subset_of_the_carry_list(zones):
    assert plan(zones, disk="/dev/vda", carry=["Apps"])["carry"] == ["Apps"]
    assert plan(zones, disk="/dev/vda", carry=[])["carry"] == []
    for bad in (["Documents"], ["../x"], ["/etc"], [".claude/../../etc"], ["Apps/"], "Apps", [3]):
        with pytest.raises(PlanError):
            plan(zones, disk="/dev/vda", carry=bad)


def test_the_carry_list_skips_comments_and_refuses_paths_that_leave_the_home_folder(tmp_path):
    f = tmp_path / "carry.list"
    f.write_text("# a comment\n\n.claude\n  Apps  \n")
    assert installplan.read_carry_list(f) == [".claude", "Apps"]
    f.write_text("../outside\n")
    with pytest.raises(PlanError):
        installplan.read_carry_list(f)


def test_the_shipped_carry_list_is_valid_and_has_the_sign_in():
    shipped = installplan.read_carry_list(Path(__file__).resolve().parent.parent / "install" / "carry.list")
    assert {".claude", ".claude.json", ".codex", ".config/bombadil"} <= set(shipped)
    assert len(shipped) == len(set(shipped))


def test_the_shell_assignments_are_quoted_so_an_eval_cannot_run_a_value(zones):
    p = plan(zones, disk="/dev/vda", carry=["Apps"], hostname="daniels-laptop", encrypt=False)
    out = installplan.shell_assignments(p)
    assert "P_DISK=/dev/vda" in out and "P_ENCRYPT=no" in out and "P_CARRY=(Apps)" in out
    p["carry"] = ["a b", "it's"]
    p["hostname"] = "$(touch /tmp/pwned)"
    out = installplan.shell_assignments(p)
    # What a shell reads back is exactly the data, and nothing ran.
    script = f'{out}\nprintf "%s|" "$P_HOSTNAME" "${{P_CARRY[@]}}"'
    got = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    assert got == "$(touch /tmp/pwned)|a b|it's|"


def test_run_as_a_module_it_prints_assignments_and_a_bad_plan_exits_2(tmp_path, zones):
    f = tmp_path / "plan.json"
    carry = tmp_path / "carry.list"
    carry.write_text("Apps\n")
    f.write_text(json.dumps({"disk": "/dev/vda", "timezone": "Europe/Lisbon", "timezone_source": "detected"}))
    src = Path(__file__).resolve().parent.parent / "src"
    env = {"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"}
    base = [sys.executable, "-m", "bombadil.installplan", "--carry-list", str(carry), "--zoneinfo", str(zones)]
    ok = subprocess.run([*base, "--plan", str(f)], capture_output=True, text=True, env=env)
    assert ok.returncode == 0 and "P_TIMEZONE=Europe/Lisbon" in ok.stdout and "P_TIMEZONE_SOURCE=detected" in ok.stdout
    f.write_text(json.dumps({"disk": "/dev/vda", "password": "x"}))
    bad = subprocess.run([*base, "--plan", str(f)], capture_output=True, text=True, env=env)
    assert bad.returncode == 2 and bad.stdout == "" and "install plan:" in bad.stderr
    f.write_text("{not json")
    assert subprocess.run([*base, "--plan", str(f)], capture_output=True, text=True, env=env).returncode == 2


@pytest.mark.parametrize("model,name,seed,expected", [
    ("ThinkPad T480", "Daniel", "4dd00922", "daniels-thinkpad"),
    ("ThinkPad T480", None, "4dd00922", "thinkpad"),
    ("XPS 13 9310", "Ana María", "9f3a1b", "ana-marias-xps"),
    ("Standard PC (Q35 + ICH9, 2009)", "Daniel", "4dd00922", "bombadil-4dd0"),
    ("To be filled by O.E.M.", None, "a1b2c3", "bombadil-a1b2"),
    (None, "Daniel", "4dd00922", "bombadil-4dd0"),
    ("20L5CTO1WW", "Daniel", "abcdef", "bombadil-abcd"),
    ("", None, "", "bombadil-home"),
    ("pc-i440fx-noble-v2", "Daniel", "77aa11", "bombadil-77aa"),
    ("pc-q35-9.2", None, "77aa11", "bombadil-77aa"),
])
def test_the_name_the_card_offers_is_readable_when_the_maker_gave_a_model_and_distinct_when_not(model, name, seed, expected):
    host = installplan.suggest_hostname(seed, model=model, name=name)
    assert host == expected
    assert installplan._HOST.fullmatch(host)


def _node(path, kind, **kw):
    return {"path": path, "type": kind, **kw}


def test_the_disks_a_person_can_pick_from_and_the_reason_for_each_one_that_is_missing():
    lsblk = {"blockdevices": [
        _node("/dev/nvme0n1", "disk", size=512 * 1000**3, model="SAMSUNG  MZVLB512", tran="nvme", children=[
            _node("/dev/nvme0n1p1", "part", fstype="vfat", partlabel="EFI system partition"),
            _node("/dev/nvme0n1p3", "part", fstype="BitLocker", partlabel="Basic data partition")]),
        _node("/dev/sda", "disk", size=1000 * 1000**3, model="WD Blue", tran="sata", children=[
            _node("/dev/sda2", "part", fstype="crypto_LUKS", partlabel="Bombadil")]),
        _node("/dev/sdb", "disk", size=32 * 1000**3, model="Flash", tran="usb", children=[
            _node("/dev/sdb1", "part", fstype="iso9660", mountpoints=["/run/archiso/bootmnt"])]),
        _node("/dev/sdc", "disk", size=8 * 1000**3, model="Tiny", tran="usb"),
        _node("/dev/sdd", "disk", size=256 * 1000**3, model="Busy", children=[
            _node("/dev/sdd1", "part", fstype="ext4", mountpoints=["/mnt/data"])]),
        _node("/dev/zram0", "disk", size=8 * 1000**3),
        _node("/dev/fd0", "disk", size=0),
        _node("/dev/loop0", "loop", size=1000**3),
        _node("/dev/sr0", "rom", size=1000**3),
    ]}
    by = {d["path"]: d for d in installplan.list_disks(lsblk, stick="/dev/sdb")}
    assert list(by) == ["/dev/nvme0n1", "/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
    assert by["/dev/nvme0n1"]["offered"] and by["/dev/nvme0n1"]["windows"] and by["/dev/nvme0n1"]["note"] == "Windows is on it"
    assert by["/dev/nvme0n1"]["model"] == "SAMSUNG MZVLB512"
    assert by["/dev/sda"]["offered"] and by["/dev/sda"]["bombadil"] and by["/dev/sda"]["encrypted"]
    assert by["/dev/sda"]["note"] == "Bombadil is on it, encrypted"
    assert by["/dev/sdb"]["why_not"] == "the USB stick Bombadil started from"
    assert by["/dev/sdc"]["why_not"] == "smaller than 16 GB"
    assert by["/dev/sdd"]["why_not"] == "in use"
    # An install image is refused by what is on it too, when the stick is not the one this boot came from.
    other = installplan.list_disks(lsblk, stick="")
    assert [d for d in other if d["path"] == "/dev/sdb"][0]["why_not"] == "a Bombadil install image"


def test_the_lines_for_the_shell_number_only_what_can_be_picked():
    disks = [{"path": "/dev/a", "size": 500 * 1000**3, "model": "A", "usb": False, "offered": True, "why_not": "",
              "note": "empty", "bombadil": False, "encrypted": False, "windows": False},
             {"path": "/dev/b", "size": 2000 * 1000**3, "model": "", "usb": True, "offered": False,
              "why_not": "in use", "note": "", "bombadil": False, "encrypted": False, "windows": False},
             {"path": "/dev/c", "size": 256 * 1000**3, "model": "C", "usb": False, "offered": True, "why_not": "",
              "note": "Bombadil is on it", "bombadil": True, "encrypted": False, "windows": False}]
    rows = [line.split("\t") for line in installplan.disk_lines(disks)]
    assert [r[0] for r in rows] == ["1", "-", "2"]
    assert rows[1][2:5] == ["0", "0", "0"] and rows[2][2:5] == ["1", "1", "0"]
    assert rows[1][5] == "2.0 TB disk (USB): not offered, it is in use"


def test_run_as_a_module_it_lists_the_disks_from_lsblks_json(tmp_path):
    f = tmp_path / "lsblk.json"
    f.write_text(json.dumps({"blockdevices": [_node("/dev/vda", "disk", size=64 * 1000**3, model="QEMU", tran="")]}))
    src = Path(__file__).resolve().parent.parent / "src"
    r = subprocess.run([sys.executable, "-m", "bombadil.installplan", "--disks", "--lsblk", str(f)], capture_output=True,
                       text=True, env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout.startswith("1\t/dev/vda\t1\t0\t0\t64 GB QEMU: empty")
