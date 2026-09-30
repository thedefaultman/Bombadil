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
    for name in ("UTC", "Europe/Lisbon", "America/Vancouver"):
        (z / name).parent.mkdir(parents=True, exist_ok=True)
        (z / name).write_text("tz")
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
    p = validate({"disk": "/dev/vda", "mode": "fresh"}, carry_list=CARRY, zoneinfo=zones, disk="/dev/sdb", mode="refresh")
    assert (p["disk"], p["mode"]) == ("/dev/vda", "fresh")


def test_a_time_zone_and_where_it_came_from(zones):
    p = plan(zones, disk="/dev/vda", timezone="Europe/Lisbon", timezone_source="detected")
    assert (p["timezone"], p["timezone_source"]) == ("Europe/Lisbon", "detected")
    # A zone with no word on where it came from was chosen by someone.
    assert plan(zones, disk="/dev/vda", timezone="America/Vancouver")["timezone_source"] == "chosen"
    assert plan(zones, disk="/dev/vda", timezone_source="unset")["timezone"] == "UTC"


@pytest.mark.parametrize("fields", [
    {"timezone": "Mars/Olympus"},
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
])
def test_the_name_the_card_offers_is_readable_when_the_maker_gave_a_model_and_distinct_when_not(model, name, seed, expected):
    host = installplan.suggest_hostname(seed, model=model, name=name)
    assert host == expected
    assert installplan._HOST.fullmatch(host)
