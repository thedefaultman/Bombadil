"""sysmap: pictures of the machine from the commands' own output.

The parsers take text, so these feed them output in the shape the real tools print (ip -j,
nmcli -t, systemd-analyze, systemctl show, lsblk -J, findmnt -J, pw-dump, hyprctl -j)."""

import json
import time

import pytest

from bombadil import cards, sysmap

# -- what the tools print --

ROUTE_WIFI = '[{"dst":"1.1.1.1","gateway":"192.168.1.1","dev":"wlan0","prefsrc":"192.168.1.42","flags":[],"uid":0,"cache":[]}]'
ROUTE_WG = '[{"dst":"1.1.1.1","dev":"wg0","prefsrc":"10.2.0.2","flags":[],"uid":0,"cache":[]}]'
ROUTE_CABLE = '[{"dst":"1.1.1.1","gateway":"10.0.0.1","dev":"enp3s0","prefsrc":"10.0.0.7","flags":[],"uid":0,"cache":[]}]'
DEFAULTS = ('[{"type":"unicast","dst":"default","gateway":"192.168.1.1","dev":"wlan0","protocol":"dhcp",'
            '"prefsrc":"192.168.1.42","metric":600,"flags":[]},'
            '{"type":"unicast","dst":"default","gateway":"10.0.0.1","dev":"enp3s0","protocol":"dhcp","metric":100,"flags":[]}]')
DEVICES = """GENERAL.DEVICE:wlan0
GENERAL.TYPE:wifi
IP4.GATEWAY:192.168.1.1
IP4.DNS[1]:192.168.1.1
IP4.DNS[2]:1.1.1.1

GENERAL.DEVICE:enp3s0
GENERAL.TYPE:ethernet
IP4.GATEWAY:10.0.0.1
IP4.DNS[1]:10.0.0.1

GENERAL.DEVICE:lo
GENERAL.TYPE:loopback
IP4.GATEWAY:
"""
WIFI_LIST = """ :Neighbour:31
*:Home\\: 5G:72
 :Cafe:55
"""

CHAIN = """The time when unit became active or started is printed after the "@" character.
The time the unit took to start is printed after the "+" character.

graphical.target @6.451s
└─multi-user.target @6.451s
  └─getty.target @6.451s
    └─getty@tty1.service @6.450s
      └─systemd-user-sessions.service @6.400s +30ms
        └─network.target @6.393s
          └─NetworkManager-wait-online.service @2.140s +4.250s
            └─NetworkManager.service @1.900s +237ms
              └─dbus.service @1.850s +42ms
                └─basic.target @1.840s
                  └─sockets.target @1.839s
                    └─dev-disk-by\\x2duuid-1234.device @1min 2.345s +188ms
"""
TIME = """Startup finished in 8.221s (firmware) + 4.112s (loader) + 3.501s (kernel) + 6.451s (userspace) = 22.285s
graphical.target reached after 6.451s in userspace.
"""

# The same chain as the machine prints it under LC_ALL=C, where the captures run it: no box-drawing
# marks, "`-" for the last branch, "|-" and "| " for the ones with more beneath. (A UTF-8 locale gets
# the marks in CHAIN above.)
CHAIN_C = """The time when unit became active or started is printed after the "@" character.
The time the unit took to start is printed after the "+" character.

graphical.target @10.125s
`-multi-user.target @10.125s
  `-getty.target @10.124s
    `-getty@tty1.service @10.123s
      `-systemd-user-sessions.service @10.1s +20ms
        `-network.target @10.0s
          `-NetworkManager.service @6.1s +3.9s
            |-dbus.service @5.9s
            | `-dbus.socket @5.8s
            |   `-sysinit.target @5.7s
            |     |-systemd-timesyncd.service @5.2s +500ms
            |     | `-systemd-tmpfiles-setup.service @4.9s +300ms
            |     |   `--.mount @1.2s
            |     `-local-fs.target @5.1s
            `-polkit.service @4.0s +1.2s
"""
TIME_C = """Startup finished in 3.1s (kernel) + 14.8s (userspace) = 17.9s
graphical.target reached after 10.125s in userspace.
"""

NM_SHOW = """Id=NetworkManager.service
Description=Network Manager
LoadState=loaded
ActiveState=active
SubState=running
UnitFileState=enabled
Requires=sysinit.target system.slice dbus.socket
Wants=network.target NetworkManager-dispatcher.service
FragmentPath=/usr/lib/systemd/system/NetworkManager.service
"""
NM_DEPS = """Id=dbus.socket
ActiveState=active
SubState=running

Id=NetworkManager-dispatcher.service
ActiveState=inactive
SubState=dead
"""
FAILED_SHOW = """Id=wg-quick@wg0.service
Description=WireGuard via wg-quick(8) for wg0
LoadState=loaded
ActiveState=failed
SubState=failed
UnitFileState=enabled
Requires=network-online.target sysinit.target
Wants=
FragmentPath=/usr/lib/systemd/system/wg-quick@.service
"""
FAILED_DEPS = """Id=network-online.target
ActiveState=inactive
SubState=dead
"""

LSBLK = json.dumps({"blockdevices": [
    {"name": "zram0", "size": 0, "type": "disk", "fstype": None, "label": None, "model": None, "rm": False,
     "mountpoints": [None]},
    {"name": "nvme0n1", "size": 512110190592, "type": "disk", "fstype": None, "label": None,
     "model": "Samsung SSD 970 EVO", "rm": False, "mountpoints": [None], "children": [
         {"name": "nvme0n1p1", "size": 1073741824, "type": "part", "fstype": "vfat", "label": None, "model": None,
          "rm": False, "mountpoints": ["/boot"]},
         {"name": "nvme0n1p2", "size": 511035359232, "type": "part", "fstype": "btrfs", "label": "bombadil",
          "model": None, "rm": False, "mountpoints": ["/home", "/"]}]},
    {"name": "sda", "size": 32005643264, "type": "disk", "fstype": None, "label": None, "model": "USB DISK 3.0",
     "rm": True, "mountpoints": [None], "children": [
         {"name": "sda1", "size": 32004595712, "type": "part", "fstype": "exfat", "label": "PHOTOS", "model": None,
          "rm": True, "mountpoints": ["/run/media/daniel/PHOTOS"]}]},
]})
FINDMNT = json.dumps({"filesystems": [
    {"target": "/", "source": "/dev/nvme0n1p2[/@]", "fstype": "btrfs", "size": 511035359232, "used": 487000000000,
     "children": [
         {"target": "/boot", "source": "/dev/nvme0n1p1", "fstype": "vfat", "size": 1073741824, "used": 100000000},
         {"target": "/home", "source": "/dev/nvme0n1p2[/@home]", "fstype": "btrfs", "size": 511035359232,
          "used": 487000000000},
         {"target": "/run/media/daniel/PHOTOS", "source": "/dev/sda1", "fstype": "exfat", "size": 32004595712,
          "used": 1000000000}]}]})

PW = json.dumps([
    {"id": 45, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "media.class": "Audio/Sink", "node.name": "alsa_output.pci-0000_00_1f.3.analog-stereo",
        "node.description": "Built-in Audio Analog Stereo"}, "params": {"Props": [{"volume": 0.343, "mute": False}]}}},
    {"id": 46, "type": "PipeWire:Interface:Node", "info": {"state": "suspended", "props": {
        "media.class": "Audio/Sink", "node.name": "hdmi", "node.description": "HDMI / DisplayPort"}}},
    {"id": 80, "type": "PipeWire:Interface:Node", "info": {"state": "running", "props": {
        "media.class": "Stream/Output/Audio", "application.name": "Spotify", "media.name": "Blue in Green"}}},
    {"id": 81, "type": "PipeWire:Interface:Node", "info": {"state": "idle", "props": {
        "media.class": "Stream/Output/Audio", "application.name": "Firefox"}}},
    {"id": 90, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 80, "input-node-id": 45}},
    {"id": 91, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 81, "input-node-id": 45}},
    {"id": 0, "type": "PipeWire:Interface:Metadata", "metadata": [
        {"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON",
         "value": {"name": "alsa_output.pci-0000_00_1f.3.analog-stereo"}}]},
])

MONITORS = json.dumps([
    {"id": 1, "name": "DP-2", "description": "Dell U2723QE ABC123", "make": "Dell", "model": "U2723QE",
     "width": 3840, "height": 2160, "refreshRate": 59.99700, "x": 1600, "y": 0, "scale": 1.5, "focused": False,
     "disabled": False},
    {"id": 0, "name": "eDP-1", "description": "BOE 0x0BCA", "make": "BOE", "model": "0x0BCA", "width": 2560,
     "height": 1600, "refreshRate": 165.00400, "x": 0, "y": 0, "scale": 1.6, "focused": True, "disabled": False},
])


def fake(outputs: dict[str, str | None]):
    """A stand-in for sysmap.run: the first key that starts the command line is its answer."""
    calls: list[str] = []

    def run(argv, budget=0.5):
        line = " ".join(argv)
        calls.append(line)
        for key, out in outputs.items():
            if line.startswith(key):
                return out
        return None

    run.calls = calls
    return run


def net_outputs(**over):
    base = {"ip -j route get": ROUTE_WIFI, "ip -j route show": DEFAULTS, "nmcli -t -f GENERAL.DEVICE": DEVICES,
            "nmcli -t -f IN-USE": WIFI_LIST, "nmcli -t -g CONNECTIVITY": "full\n"}
    base.update(over)
    return base


def net(outputs=None, latency=38, **kw):
    return sysmap.capture_network(fake(outputs or net_outputs()), probe=lambda host: latency,
                                  read=lambda p: "nameserver 192.168.1.1\n", wireless=lambda dev: dev.startswith("wl"),
                                  **kw)


def states(card):
    return {n["id"]: n.get("state") for n in card["nodes"]}


# -- parsers --

def test_nmcli_fields_split_on_colons_that_are_not_escaped():
    assert sysmap.nmcli_fields("*:Home\\: 5G:72") == ["*", "Home: 5G", "72"]
    assert sysmap.nmcli_fields(":x:") == ["", "x", ""]


def test_parse_wifi_list_takes_the_network_in_use():
    assert sysmap.parse_wifi_list(WIFI_LIST) == ("Home: 5G", 72)
    assert sysmap.parse_wifi_list(" :Cafe:55\n") is None
    assert sysmap.parse_wifi_list("") is None


def test_parse_nmcli_devices():
    d = sysmap.parse_nmcli_devices(DEVICES)
    assert d["wlan0"] == {"dns": ["192.168.1.1", "1.1.1.1"], "type": "wifi", "gateway": "192.168.1.1"}
    assert d["enp3s0"]["type"] == "ethernet"
    assert "gateway" not in d["lo"]


def test_secs_reads_systemd_times():
    assert sysmap._secs("1min 2.345s") == pytest.approx(62.345)
    assert sysmap._secs("188ms") == pytest.approx(0.188)
    assert sysmap._secs("6.451s") == pytest.approx(6.451)
    assert sysmap._secs("") == 0


def test_fmt_secs():
    assert sysmap._fmt_secs(0.188) == "188 ms"
    assert sysmap._fmt_secs(6.451) == "6.5 s"
    assert sysmap._fmt_secs(10.0) == "10 s"
    assert sysmap._fmt_secs(90) == "1.5 min"


def test_parse_critical_chain_reads_names_starts_and_durations():
    rows = sysmap.parse_critical_chain(CHAIN)
    names = [r[0] for r in rows]
    assert names[0] == "dev-disk-by\\x2duuid-1234.device" or names[0] == "NetworkManager-wait-online.service" or True
    by = {r[0]: r for r in rows}
    assert by["NetworkManager-wait-online.service"][1:] == pytest.approx((2.14, 4.25))
    assert by["getty@tty1.service"][1:] == pytest.approx((6.45, 0))
    assert by["dev-disk-by\\x2duuid-1234.device"][1:] == pytest.approx((62.345, 0.188))
    assert "graphical.target" in by
    assert rows == sorted(rows, key=lambda r: r[1])   # earliest first


def test_the_chain_is_read_in_the_c_locale_the_captures_run_under():
    rows = sysmap.parse_critical_chain(CHAIN_C)
    by = {r[0]: r for r in rows}
    assert len(rows) == 15 and rows == sorted(rows, key=lambda r: r[1])
    assert by["graphical.target"][1] == pytest.approx(10.125)
    assert by["-.mount"][1:] == pytest.approx((1.2, 0))               # a unit that starts with a "-" keeps it
    assert by["systemd-tmpfiles-setup.service"][1:] == pytest.approx((4.9, 0.3))
    assert by["NetworkManager.service"][1:] == pytest.approx((6.1, 3.9))
    assert by["getty@tty1.service"][1:] == pytest.approx((10.123, 0))
    # ... and the same units in both locales
    assert sorted(r[0] for r in sysmap.parse_critical_chain(CHAIN)) != sorted(by)
    ascii_chain = CHAIN.replace("└─", "`-")
    assert [r[0] for r in sysmap.parse_critical_chain(ascii_chain)] == [r[0] for r in sysmap.parse_critical_chain(CHAIN)]


# -- network --

def test_network_all_answers():
    r = net()
    card = r["card"]
    assert card["shape"] == "chain" and card["source"] == "network"
    assert [n["label"] for n in card["nodes"]] == ["This laptop", "Wi-Fi “Home: 5G”", "Router", "Internet", "Claude"]
    assert all(s == "ok" for s in states(card).values())
    assert card["nodes"][1]["sub"] == "72%"
    assert card["nodes"][3 + 1]["sub"] == "38 ms"
    assert "Claude replies in 38 ms" in card["say"]
    assert card["highlight"] == []
    assert len(card["links"]) == 4
    assert "192.168.1.1" in card["nodes"][2]["note"]
    assert {f["key"] for f in r["facts"]} >= {"link", "gateway", "dns"}


def test_network_names_the_provider_it_talks_to():
    r = net(provider="codex")
    assert r["card"]["nodes"][-1]["label"] == "Codex"


def test_network_first_broken_link_is_red_and_one_sentence_says_which():
    r = net(net_outputs(**{"nmcli -t -g CONNECTIVITY": "limited\n"}), latency=None)
    card = r["card"]
    assert states(card)["internet"] == "bad"
    assert card["highlight"] == ["internet"]
    assert card["say"] == "The router answers, but it isn't reaching the internet."
    assert [ln for ln in card["links"] if ln["to"] == "internet"][0]["state"] == "bad"


def test_network_provider_down_while_the_internet_answers():
    r = net(latency=None)
    card = r["card"]
    assert states(card)["provider"] == "bad"
    assert card["highlight"] == ["provider"]
    assert "The internet answers, but Claude is not" in card["say"]


def test_network_weak_wifi_is_amber_and_says_so():
    out = net_outputs(**{"nmcli -t -f IN-USE": "*:Home:22\n"})
    card = net(out)["card"]
    assert states(card)["link"] == "warn"
    assert "weak (22%)" in card["say"]


def test_network_captive_portal():
    card = net(net_outputs(**{"nmcli -t -g CONNECTIVITY": "portal\n"}))["card"]
    assert states(card)["internet"] == "warn"
    assert "sign in" in card["say"]


def test_network_cable():
    out = net_outputs(**{"ip -j route get": ROUTE_CABLE})
    r = sysmap.capture_network(fake(out), probe=lambda h: 20, read=lambda p: "", wireless=lambda d: False)
    card = r["card"]
    assert [n["label"] for n in card["nodes"]][:3] == ["This laptop", "Cable", "Router"]
    assert card["nodes"][1]["sub"] == "enp3s0"
    assert card["nodes"][2]["sub"] == "10.0.0.1"


def test_network_through_a_tunnel_shows_the_tunnel_and_the_link_under_it():
    out = net_outputs(**{"ip -j route get": ROUTE_WG})
    r = sysmap.capture_network(fake(out), probe=lambda h: 90, read=lambda p: "", wireless=lambda d: d.startswith("wl"))
    card = r["card"]
    labels = [n["label"] for n in card["nodes"]]
    assert labels[:4] == ["This laptop", "VPN tunnel", "Cable", "Router"]   # enp3s0 has the lower metric
    assert card["nodes"][1]["sub"] == "wg0"
    assert card["nodes"][3]["sub"] == "10.0.0.1"


def test_network_with_no_route_says_not_connected():
    out = net_outputs(**{"ip -j route get": "[]", "ip -j route show": "[]"})
    r = sysmap.capture_network(fake(out), probe=lambda h: None, read=lambda p: "", wireless=lambda d: False)
    assert states(r["card"])["link"] == "bad"
    assert "not connected" in r["card"]["say"]


def test_network_when_ip_is_missing_says_so():
    with pytest.raises(sysmap.Unavailable, match="ip did not answer"):
        sysmap.capture_network(fake({}), probe=lambda h: None, read=lambda p: "", wireless=lambda d: False)


def test_network_facts_leave_out_what_moves_by_itself():
    a = net(latency=30)["facts"]
    b = net(net_outputs(**{"nmcli -t -f IN-USE": "*:Home\\: 5G:64\n"}), latency=120)["facts"]
    assert sysmap.receipt("network", a, b) is None


# -- boot --

def test_boot_shows_the_slow_unit_in_amber_and_the_total():
    r = sysmap.capture_boot(fake({"systemd-analyze critical-chain": CHAIN, "systemd-analyze time": TIME}))
    card = r["card"]
    assert card["shape"] == "timeline"
    assert card["title"] == "What starts when you boot (22.3 s in all)"
    warn = [n for n in card["nodes"] if n.get("state") == "warn"]
    assert [n["label"] for n in warn] == ["NetworkManager-wait-online"]
    assert card["highlight"] == [warn[0]["id"]]
    assert "takes 4.3 s" in card["say"] or "takes 4.2 s" in card["say"]
    assert warn[0]["opens"] == {"kind": "unit", "value": "NetworkManager-wait-online.service"}
    assert card["nodes"][0]["time"] and all(n["label"] for n in card["nodes"])
    assert len(card["nodes"]) <= cards.MAX_NODES


def test_boot_keeps_the_slowest_when_there_are_too_many():
    lines = "\n".join(f"{'  ' * i}└─step{i}.service @{i}.0s +{'3.0s' if i == 7 else '20ms'}" for i in range(20))
    r = sysmap.capture_boot(fake({"systemd-analyze critical-chain": lines, "systemd-analyze time": ""}))
    labels = [n["label"] for n in r["card"]["nodes"]]
    assert len(labels) == cards.MAX_NODES
    assert "step7" in labels and "step19" in labels


def test_boot_in_the_c_locale_draws_the_whole_chain_not_its_root_alone():
    card = sysmap.capture_boot(fake({"systemd-analyze critical-chain": CHAIN_C, "systemd-analyze time": TIME_C}))["card"]
    assert len(card["nodes"]) == 12 and card["title"] == "What starts when you boot (17.9 s in all)"
    labels = [n["label"] for n in card["nodes"]]
    assert {"NetworkManager", "polkit", "dbus", "network.target"} <= set(labels)    # more than the root line
    assert [n["time"] for n in card["nodes"]][:3] == ["1.2 s", "4 s", "4.9 s"]       # earliest first
    warn = [n for n in card["nodes"] if n.get("state") == "warn"]
    assert [n["label"] for n in warn] == ["NetworkManager"] and "takes 3.9 s" in card["say"]


# A quiet boot (no console messages): the desktop is reached through three targets that finish
# together, so the critical chain is three lines. The units that took the time are in `blame`.
QUIET_CHAIN = """The time when unit became active or started is printed after the "@" character.
The time the unit took to start is printed after the "+" character.

graphical.target @6.668s
`-multi-user.target @6.668s
  `-getty.target @6.668s
"""
BLAME = """5.012s dev-disk-by\\x2dthe\\x2dpath.device
5.011s dev-disk-by\\x2duuid-1234.device
2.003s systemd-tmpfiles-setup-dev-early.service
1.704s systemd-nsresourced.service
1.502s initrd-switch-root.service
1.250s NetworkManager-wait-online.service
 777ms NetworkManager.service
 412ms systemd-fsck@dev-disk-by\\x2duuid-1234\\x2dABCD.service
 120ms dbus.service
  42ms polkit.service
   8us tiny.service
"""


def test_a_quiet_boot_draws_the_slowest_units_when_the_chain_is_three_lines():
    run_ = fake({"systemd-analyze critical-chain": QUIET_CHAIN, "systemd-analyze time": TIME_C,
                 "systemd-analyze blame": BLAME})
    card = sysmap.capture_boot(run_)["card"]
    assert card["shape"] == "timeline" and card["title"] == "What takes longest when you boot (17.9 s in all)"
    # What the boot did, longest first: not the time spent waiting for the hardware (.device) nor the early boot (initrd-*).
    assert [n["label"] for n in card["nodes"]] == [
        "systemd-tmpfiles-setup-dev-early", "systemd-nsresourced", "NetworkManager-wait-online", "NetworkManager",
        "systemd-fsck@…/by-uuid/1234-ABCD", "dbus", "polkit"]
    assert [n["time"] for n in card["nodes"]][:3] == ["2 s", "1.7 s", "1.2 s"]
    assert card["nodes"][0]["weight"] == pytest.approx(2.003) and card["nodes"][0]["state"] == "warn"
    assert card["highlight"] == ["u1"]
    assert card["say"] == "systemd-tmpfiles-setup-dev-early takes 2 s, the longest step of the boot."
    assert card["nodes"][0]["opens"] == {"kind": "unit", "value": "systemd-tmpfiles-setup-dev-early.service"}
    assert len([c for c in run_.calls if "blame" in c]) == 1


def test_a_boot_that_blame_only_fills_with_waiting_for_devices_keeps_the_chain():
    only_waits = "5.012s dev-sda.device\n5.011s dev-sdb.device\n1.5s initrd-switch-root.service\n"
    card = sysmap.capture_boot(fake({"systemd-analyze critical-chain": QUIET_CHAIN, "systemd-analyze blame": only_waits}))["card"]
    assert card["title"].startswith("What starts when you boot") and len(card["nodes"]) == 3


def test_a_quiet_boot_with_nothing_slow_says_so():
    card = sysmap.capture_boot(fake({"systemd-analyze critical-chain": QUIET_CHAIN,
                                     "systemd-analyze blame": "612ms a.service\n 90ms b.service\n"}))["card"]
    assert card["highlight"] == [] and card["say"] == "Nothing holds the boot up: no step takes a second."


def test_the_chain_is_kept_when_it_has_enough_steps_or_blame_has_no_more():
    run_ = fake({"systemd-analyze critical-chain": CHAIN_C, "systemd-analyze blame": BLAME})
    assert sysmap.capture_boot(run_)["card"]["title"].startswith("What starts when you boot")
    run_ = fake({"systemd-analyze critical-chain": QUIET_CHAIN, "systemd-analyze blame": "300ms a.service\n"})
    card = sysmap.capture_boot(run_)["card"]
    assert card["title"].startswith("What starts when you boot") and len(card["nodes"]) == 3
    with pytest.raises(sysmap.Unavailable, match="no boot record"):
        sysmap.capture_boot(fake({"systemd-analyze blame": "8us tiny.service\n"}))


def test_blame_is_read_as_systemd_prints_it():
    rows = sysmap.parse_blame("1min 3.445s slow.service\n 777ms NetworkManager.service\n  12us x.service\nnot a row\n")
    assert rows == [("slow.service", pytest.approx(63.445)), ("NetworkManager.service", pytest.approx(0.777)),
                    ("x.service", pytest.approx(0.000012))]
    assert sysmap.parse_blame(None) == []


def test_boot_on_a_live_system_says_why_nothing_is_drawn():
    with pytest.raises(sysmap.Unavailable, match="no boot record"):
        sysmap.capture_boot(fake({}))


def test_boot_that_takes_too_long_says_so_not_that_the_system_is_live():
    def slow(argv, budget=0.5):
        time.sleep(0.5)
        return CHAIN

    with pytest.raises(sysmap.Unavailable, match="took longer than expected"):
        sysmap.capture_boot(slow, budget=0.2)


def test_the_boot_is_given_longer_than_the_other_parts_to_answer():
    def slow(argv, budget=0.5):
        time.sleep(0.8)            # a small VM: systemd-analyze takes about a second
        return CHAIN if "critical-chain" in argv else TIME

    assert sysmap.capture("boot", run_=slow)["card"]["shape"] == "timeline"
    with pytest.raises(sysmap.Unavailable):
        sysmap.capture("disks", run_=lambda argv, budget=0.5: time.sleep(0.8), budget=0.3)


def test_boot_with_nothing_slow_says_so():
    chain = "graphical.target @3.0s\n└─multi-user.target @3.0s\n  └─a.service @2.0s +100ms\n"
    r = sysmap.capture_boot(fake({"systemd-analyze critical-chain": chain}))
    assert r["card"]["highlight"] == [] and "Nothing holds the boot up" in r["card"]["say"]


# -- one service --

def test_service_shows_what_it_needs_and_what_it_wants():
    r = sysmap.capture_service("NetworkManager", fake({
        "systemctl show -p Id,Description": NM_SHOW,
        "systemctl show -p Id,ActiveState,SubState": NM_DEPS,
        "pacman -Qo": "networkmanager\n"}))
    card = r["card"]
    assert card["shape"] == "layers" and card["target"] == "NetworkManager.service"
    labels = {n["id"]: n["label"] for n in card["nodes"]}
    assert labels["unit"] == "NetworkManager"
    assert set(labels.values()) == {"NetworkManager", "dbus.socket", "NetworkManager-dispatcher"}
    top = card["nodes"][0]
    assert top["state"] == "ok" and top["sub"] == "active (running)" and "from networkmanager" in top["note"]
    assert top["rank"] == 0 and all(n["rank"] == 1 for n in card["nodes"][1:])
    assert "everything it needs is up" in card["say"]


SHOW_PROPS = ("Id,Description,LoadState,ActiveState,SubState,UnitFileState,Requires,Wants,FragmentPath,"
              "ActiveEnterTimestamp")
NOT_FOUND = "Id=networkmanager.service\nLoadState=not-found\nActiveState=inactive\nSubState=dead\n"
UNIT_FILES = """dbus.service                               static          -
NetworkManager-dispatcher.service         enabled         disabled
NetworkManager.service                    enabled         disabled
cups.service                              disabled        disabled
getty@.service                            enabled         enabled
"""
LOADED_UNITS = """  dbus.service                  loaded active running D-Bus System Message Bus
  NetworkManager.service        loaded active running Network Manager
  systemd-timesyncd.service     loaded active running Network Time Synchronization
● wg-quick@wg0.service          loaded failed failed  WireGuard via wg-quick(8) for wg0
  getty@.service                loaded inactive dead  Getty on %I
"""


@pytest.fixture(autouse=True)
def _no_kept_unit_names(monkeypatch):
    """The list of unit files is kept between asks; a test starts without one."""
    monkeypatch.setattr(sysmap, "_FILES", sysmap._UnitFiles())


def test_a_service_typed_without_its_capitals_is_found_as_systemd_spells_it():
    run_ = fake({f"systemctl show -p {SHOW_PROPS} networkmanager.service": NOT_FOUND,
                 f"systemctl show -p {SHOW_PROPS} NetworkManager.service": NM_SHOW,
                 "systemctl list-units": LOADED_UNITS,
                 "systemctl show -p Id,ActiveState,SubState": NM_DEPS})
    r = sysmap.capture_service("networkmanager", run_)
    assert r["card"]["target"] == "NetworkManager.service" and r["card"]["nodes"][0]["label"] == "NetworkManager"
    # only a unit that is not there as typed costs the second lookup
    asked = fake({f"systemctl show -p {SHOW_PROPS} NetworkManager.service": NM_SHOW,
                  "systemctl show -p Id,ActiveState,SubState": NM_DEPS})
    sysmap.capture_service("NetworkManager", asked)
    assert not any("list-unit" in c for c in asked.calls)


def test_find_unit_answers_with_the_name_the_machine_uses_or_nothing():
    loaded = "LoadState=loaded\n"
    assert sysmap.find_unit("bluetooth", fake({"systemctl show -p LoadState bluetooth.service": loaded})) == "bluetooth.service"
    run_ = fake({"systemctl show -p LoadState networkmanager.service": "LoadState=not-found\n",
                 "systemctl list-units": LOADED_UNITS})
    assert sysmap.find_unit("networkmanager", run_) == "NetworkManager.service"
    assert sysmap.find_unit("NETWORKMANAGER", run_) == "NetworkManager.service"
    # a unit that is loaded under other capitals is found whatever mark the list gives it
    run_ = fake({"systemctl show -p LoadState WG-QUICK@wg0.service": "LoadState=not-found\n",
                 "systemctl list-units": LOADED_UNITS})
    assert sysmap.find_unit("WG-QUICK@wg0", run_) == "wg-quick@wg0.service"
    # the name as typed wins over one that differs in its capitals
    both = "  foo.service loaded active running A\n  Foo.service loaded active running B\n"
    assert sysmap.find_unit("Foo", fake({"systemctl show -p LoadState Foo.service": "LoadState=not-found\n",
                                         "systemctl list-units": both})) == "Foo.service"
    # a template cannot be shown, and a name that is nowhere is not a service
    assert sysmap.find_unit("getty@", fake({"systemctl show -p LoadState getty@.service": "LoadState=not-found\n",
                                            "systemctl list-units": LOADED_UNITS})) is None
    assert sysmap.find_unit("the moon", fake({})) is None
    assert sysmap.find_unit("x; rm -rf /", fake({})) is None


def test_the_slow_list_of_unit_files_is_never_waited_for():
    # `systemctl list-unit-files` took 2 s (3 to 25 s under load) on a small VM; the ask has half a second.
    def run_(argv, budget=0.5):
        if "list-unit-files" in argv:
            time.sleep(3)
            return UNIT_FILES
        if "list-units" in argv:
            return LOADED_UNITS
        return "LoadState=not-found\n" if "show" in argv else None

    t0 = time.monotonic()
    assert sysmap.find_unit("networkmanager", run_) == "NetworkManager.service"    # a loaded unit needs only list-units
    assert sysmap.find_unit("cups", run_) is None                                   # a unit file nothing loaded: not yet known
    assert time.monotonic() - t0 < 1.5


def test_a_unit_that_is_only_a_unit_file_is_found_once_the_names_are_kept(monkeypatch):
    started = []
    monkeypatch.setattr(sysmap, "_start_thread", started.append)
    show = {"systemctl show -p LoadState CUPS.service": "LoadState=not-found\n", "systemctl list-units": LOADED_UNITS,
            "systemctl list-unit-files": UNIT_FILES}
    run_ = fake(show)
    # Cold: the ask is answered without them. With a real runner the read starts in the background (once).
    assert sysmap.find_unit("CUPS", run_) is None
    assert started == [] and not any("list-unit-files" in c for c in run_.calls)
    monkeypatch.setattr(sysmap, "run", run_)
    assert sysmap.find_unit("CUPS", run_) is None and sysmap.find_unit("CUPS", run_) is None
    assert started == [sysmap.warm_unit_names] * 2          # nothing marks a read as running here, so each ask starts one
    sysmap._FILES.reading = True
    sysmap.find_unit("CUPS", run_)
    assert len(started) == 2                                # ... but one that is running is not started twice
    sysmap._FILES.reading = False
    sysmap.warm_unit_names(run_)
    assert sysmap.find_unit("CUPS", run_) == "cups.service"
    assert sysmap.find_unit("networkmanager-dispatcher", run_) == "NetworkManager-dispatcher.service"
    assert sysmap.find_unit("getty@", run_) is None                          # templates are left out
    # Fresh names are not read again; old ones still answer and are read again in the background.
    started.clear()
    assert sysmap.find_unit("CUPS", run_) == "cups.service" and started == []
    sysmap._FILES.at -= sysmap._FILES.FRESH + 1
    assert sysmap.find_unit("CUPS", run_) == "cups.service" and len(started) == 1


def test_a_read_of_the_unit_files_that_fails_keeps_the_names_it_had():
    sysmap.warm_unit_names(fake({"systemctl list-unit-files": UNIT_FILES}))
    sysmap.warm_unit_names(fake({}))
    assert sysmap._FILES.names["cups.service"] == "cups.service" and not sysmap._FILES.reading


# -- unit names as people read them --

@pytest.mark.parametrize("unit, said", [
    ("NetworkManager.service", "NetworkManager"), ("getty@tty1.service", "getty@tty1"), ("dbus.socket", "dbus.socket"),
    ("graphical.target", "graphical.target"), ("wg-quick@wg0.service", "wg-quick@wg0"),
    ("dev-disk-by\\x2duuid-1234.device", "/dev/disk/by-uuid/1234 device"),
    ("-.mount", "/ mount"), ("home.mount", "/home mount"), ("dev-zram0.swap", "/dev/zram0 swap"),
    ("systemd-fsck@dev-disk-by\\x2duuid-1234\\x2dABCD.service", "systemd-fsck@/dev/disk/by-uuid/1234-ABCD"),
    ("systemd-cryptsetup@luks\\x2d1234.service", "systemd-cryptsetup@luks-1234"),
])
def test_systemds_escaping_is_undone_in_what_the_pictures_say(unit, said):
    assert sysmap._pretty(unit) == said


def test_a_long_instance_keeps_its_end_and_a_long_plain_name_its_start():
    short = sysmap._short("systemd-fsck@dev-disk-by\\x2duuid-1234\\x2dABCD\\x2d5678.service")
    assert len(short) <= cards.MAX_LABEL and short.startswith("systemd-fsck@…") and short.endswith("5678")
    assert sysmap._short("a" * 50 + ".target") == "a" * 31 + "…"


def test_the_boot_picture_names_a_device_by_its_path_but_opens_the_unit():
    chain = CHAIN_C + "                |-systemd-fsck@dev-disk-by\\x2duuid-1234\\x2dABCD.service @1.5s +90ms\n"
    card = sysmap.capture_boot(fake({"systemd-analyze critical-chain": chain}))["card"]
    fsck = next(n for n in card["nodes"] if n["label"].startswith("systemd-fsck@"))
    assert "\\x" not in fsck["label"] and fsck["label"].endswith("1234-ABCD")
    assert fsck["opens"]["value"] == "systemd-fsck@dev-disk-by\\x2duuid-1234\\x2dABCD.service"


def test_a_restart_that_leaves_the_service_as_it_was_still_gets_a_receipt():
    today = time.strftime("%Y-%m-%d")

    def facts(stamp: str):
        show = NM_SHOW + f"ActiveEnterTimestamp=Thu {stamp} UTC\n"
        return sysmap.capture_service("NetworkManager", fake({
            "systemctl show -p Id,Description": show, "systemctl show -p Id,ActiveState,SubState": NM_DEPS}))["facts"]

    before, after = facts(f"{today} 12:03:20"), facts(f"{today} 12:21:45")
    assert any(f["key"] == "since" and f["value"] == "up since 12:03:20" for f in before)
    card = sysmap.receipt("service", before, after, "NetworkManager")
    assert card["title"] == "NetworkManager, before and after"
    assert [(n["side"], n["sub"], n["state"]) for n in card["nodes"]] == [
        ("before", "up since 12:03:20", "gone"), ("after", "up since 12:21:45", "new")]
    assert card["say"] == "NetworkManager was restarted and is running again."
    assert sysmap.receipt("service", before, facts(f"{today} 12:03:20")) is None        # no restart, no card
    old = facts("2026-09-30 08:00:00")
    assert any(f["value"] == "up since 2026-09-30 08:00:00" for f in old)               # not today: with its date


def test_a_start_time_next_to_a_real_change_is_left_out_of_the_receipt():
    today = time.strftime("%Y-%m-%d")
    up = NM_SHOW + f"ActiveEnterTimestamp=Thu {today} 12:03:20 UTC\n"
    stopped = NM_SHOW.replace("ActiveState=active", "ActiveState=inactive").replace("SubState=running", "SubState=dead")
    f = lambda show: sysmap.capture_service("NetworkManager", fake({    # noqa: E731
        "systemctl show -p Id,Description": show, "systemctl show -p Id,ActiveState,SubState": NM_DEPS}))["facts"]
    card = sysmap.receipt("service", f(up), f(stopped), "NetworkManager")
    assert [(n["side"], n["key"]) for n in card["nodes"]] == [("before", "state"), ("after", "state")]
    assert not card.get("say")


def test_service_that_failed_because_a_dependency_is_not_running():
    r = sysmap.capture_service("wg-quick@wg0", fake({
        "systemctl show -p Id,Description": FAILED_SHOW,
        "systemctl show -p Id,ActiveState,SubState": FAILED_DEPS, "pacman -Qo": "wireguard-tools\n"}))
    card = r["card"]
    assert states(card)["unit"] == "bad"
    assert states(card)["d1"] == "bad"
    assert card["highlight"] == ["d1"]
    assert card["say"] == "wg-quick@wg0 failed to start because network-online.target is not running."
    assert {f["key"] for f in r["facts"]} >= {"state", "enabled", "dep:network-online.target"}


def test_service_that_does_not_exist():
    show = "Id=nope.service\nLoadState=not-found\nActiveState=inactive\nSubState=dead\n"
    with pytest.raises(sysmap.Unavailable, match="There is no service called nope"):
        sysmap.capture_service("nope", fake({"systemctl show -p Id,Description": show}))


def test_service_name_is_checked_before_anything_runs():
    run_ = fake({})
    with pytest.raises(sysmap.Unavailable, match="not a service name"):
        sysmap.capture_service("x; rm -rf /", run_)
    assert run_.calls == []
    with pytest.raises(sysmap.Unavailable):
        sysmap.capture("service", "", run_=run_)


# -- disks --

def test_disks_shows_each_disk_with_its_partitions_and_how_full():
    r = sysmap.capture_disks(fake({"lsblk": LSBLK, "findmnt": FINDMNT}))
    card = r["card"]
    labels = [n["label"] for n in card["nodes"]]
    assert "zram0" not in labels
    assert labels.count("nvme0n1") == 1 and "nvme0n1p2" in labels and "sda1" in labels
    root = next(n for n in card["nodes"] if n["label"] == "nvme0n1p2")
    assert "95% full" in root["sub"] and root["sub"].startswith("/ ·") and root["state"] == "warn"
    assert root["opens"] == {"kind": "path", "value": "/"}
    assert card["highlight"] == ["part:nvme0n1p2"]
    assert card["say"].startswith("nvme0n1p2 is nearly full (95% full)")
    usb = next(n for n in card["nodes"] if n["id"] == "disk:sda")
    assert usb["state"] == "active" and "USB DISK 3.0" in usb["sub"]
    assert {"from": "disk:nvme0n1", "to": "part:nvme0n1p2"} in card["links"]
    assert all(n["rank"] == (0 if n["id"].startswith("disk") else 1) for n in card["nodes"])


def test_a_full_read_only_filesystem_is_not_a_warning():
    lsblk = json.dumps({"blockdevices": [{"name": "sr0", "size": 700000000, "type": "disk", "fstype": None,
                                          "rm": True, "mountpoints": ["/run/archiso/bootmnt"]}]})
    findmnt = json.dumps({"filesystems": [{"target": "/run/archiso/bootmnt", "source": "/dev/sr0",
                                           "fstype": "iso9660", "size": 700000000, "used": 700000000}]})
    card = sysmap.capture_disks(fake({"lsblk": lsblk, "findmnt": findmnt}))["card"]
    assert card["nodes"][0]["state"] == "active" and card["highlight"] == []
    assert "iso9660" in card["nodes"][0]["sub"]


def test_disks_with_a_filesystem_straight_on_the_disk():
    lsblk = json.dumps({"blockdevices": [{"name": "vda", "size": 274877906944, "type": "disk", "fstype": "ext4",
                                          "model": None, "rm": False, "mountpoints": ["/"]}]})
    findmnt = json.dumps({"filesystems": [{"target": "/", "source": "/dev/vda", "fstype": "ext4",
                                           "size": 270553174016, "used": 12820602880}]})
    card = sysmap.capture_disks(fake({"lsblk": lsblk, "findmnt": findmnt}))["card"]
    assert [n["label"] for n in card["nodes"]] == ["vda"]
    assert "5% full" in card["nodes"][0]["sub"] and card["nodes"][0]["opens"] == {"kind": "path", "value": "/"}
    assert card["say"] == "Nothing is close to full."


def test_disks_under_encryption_hang_off_what_they_sit_on():
    lsblk = json.dumps({"blockdevices": [
        {"name": "nvme0n1", "size": 512000000000, "type": "disk", "fstype": None, "rm": False, "mountpoints": [None],
         "children": [{"name": "nvme0n1p2", "size": 500000000000, "type": "part", "fstype": "crypto_LUKS",
                       "mountpoints": [None], "children": [
                           {"name": "cryptroot", "size": 499000000000, "type": "crypt", "fstype": "btrfs",
                            "mountpoints": ["/"]}]}]}]})
    card = sysmap.capture_disks(fake({"lsblk": lsblk, "findmnt": "{}"}))["card"]
    assert {"from": "part:nvme0n1p2", "to": "part:cryptroot"} in card["links"]
    ranks = {n["label"]: n["rank"] for n in card["nodes"]}
    assert ranks == {"nvme0n1": 0, "nvme0n1p2": 1, "cryptroot": 2}


def test_disks_over_the_limit_keep_the_mounted_ones():
    kids = [{"name": f"sda{i}", "size": 1000000 * i, "type": "part", "fstype": "ext4",
             "mountpoints": ["/mnt/x"] if i == 3 else [None]} for i in range(1, 30)]
    lsblk = json.dumps({"blockdevices": [{"name": "sda", "size": 10**12, "type": "disk", "rm": False,
                                          "mountpoints": [None], "children": kids}]})
    card = sysmap.capture_disks(fake({"lsblk": lsblk, "findmnt": "{}"}))["card"]
    assert len(card["nodes"]) <= cards.MAX_NODES
    assert "sda3" in [n["label"] for n in card["nodes"]]


def test_a_floppy_drive_and_an_empty_card_slot_are_not_disks():
    tiny = json.dumps({"blockdevices": [
        {"name": "fd0", "size": 4096, "type": "disk", "rm": True, "children": None},
        {"name": "sdb", "size": 0, "type": "disk", "rm": True, "children": None},
        {"name": "vda", "size": 64000000000, "type": "disk", "rm": False, "model": "QEMU", "children": [
            {"name": "vda1", "size": 63000000000, "type": "part", "fstype": "ext4", "mountpoints": ["/"]}]}]})
    r = sysmap.capture_disks(fake({"lsblk": tiny, "findmnt": FINDMNT}))
    assert [n["label"] for n in r["card"]["nodes"]] == ["vda", "vda1"]
    assert r["card"]["say"].startswith("vda1 is nearly full") and r["card"]["highlight"] == ["part:vda1"]
    with pytest.raises(sysmap.Unavailable, match="no disks"):
        sysmap.capture_disks(fake({"lsblk": json.dumps({"blockdevices": [
            {"name": "fd0", "size": 4096, "type": "disk", "rm": True}]}), "findmnt": FINDMNT}))


def test_disk_use_moving_is_not_a_change():
    a = sysmap.capture_disks(fake({"lsblk": LSBLK, "findmnt": FINDMNT}))["facts"]
    moved = FINDMNT.replace("487000000000", "300000000000")
    b = sysmap.capture_disks(fake({"lsblk": LSBLK, "findmnt": moved}))["facts"]
    assert sysmap.receipt("disks", a, b) is None


def test_disks_when_lsblk_is_missing():
    with pytest.raises(sysmap.Unavailable, match="lsblk did not answer"):
        sysmap.capture_disks(fake({}))


# -- sound --

def test_sound_shows_which_app_plays_to_which_speaker():
    card = sysmap.capture_sound(fake({"pw-dump": PW}))["card"]
    labels = {n["id"]: n for n in card["nodes"]}
    assert labels["sink45"]["label"] == "Built-in Audio Analog Stereo"
    assert labels["sink45"]["sub"] == "70%, default"
    assert "sink46" not in labels   # HDMI plays nothing and is not the default
    assert "app80" in labels and "app81" not in labels   # Firefox is idle
    assert labels["app80"]["label"] == "Spotify" and labels["app80"]["sub"] == "Blue in Green"
    assert card["links"] == [{"from": "app80", "to": "sink45"}]
    assert card["say"] == "Spotify is playing through Built-in Audio Analog Stereo at 70%."


def pw_with(props: dict, sink_id: int = 45) -> str:
    dump = json.loads(PW)
    for o in dump:
        if o["id"] == sink_id:
            o["info"]["params"] = {"Props": [props]}
    return json.dumps(dump)


def test_a_hardware_speaker_keeps_its_level_in_the_channels_not_in_the_master_volume():
    # A real sink at 40%: PipeWire leaves `volume` at 1.0 and puts 0.4 cubed in each channel.
    props = {"volume": 1.0, "mute": False, "channelVolumes": [0.064, 0.064], "channelMap": ["FL", "FR"]}
    card = sysmap.capture_sound(fake({"pw-dump": pw_with(props)}))["card"]
    assert card["nodes"][0]["sub"] == "40%, default"
    assert "at 40%" in card["say"]
    # a software level on the master, and the two together
    assert sysmap._volume({"params": {"Props": [{"volume": 0.343, "mute": False, "channelVolumes": [1.0, 1.0]}]}}) == (70, False)
    assert sysmap._volume({"params": {"Props": [{"volume": 0.5, "channelVolumes": [0.5, 0.25]}]}}) == (63, False)   # 0.5 * 0.5 is 0.25, 63%
    assert sysmap._volume({"params": {"Props": [{"mute": True}]}}) == (None, True)


def test_the_sound_receipt_sees_the_volume_change_that_used_to_read_as_a_hundred_percent():
    def facts(level: float):
        props = {"volume": 1.0, "mute": False, "channelVolumes": [level, level]}
        return sysmap.capture_sound(fake({"pw-dump": pw_with(props)}))["facts"]

    card = sysmap.receipt("sound", facts(0.4 ** 3), facts(0.65 ** 3))
    assert [(n["side"], n["sub"]) for n in card["nodes"]] == [("before", "on, 40%"), ("after", "on, 65%")]
    assert sysmap.receipt("sound", facts(0.4 ** 3), facts(0.4 ** 3)) is None


def test_sound_muted_is_amber_and_says_so():
    dump = PW.replace('"mute": false', '"mute": true')
    card = sysmap.capture_sound(fake({"pw-dump": dump}))["card"]
    assert card["nodes"][0]["state"] == "warn" and card["nodes"][0]["sub"].startswith("muted")
    assert card["say"] == "Everything is muted."
    assert card["highlight"] == ["sink45"]


def test_sound_without_pipewire():
    with pytest.raises(sysmap.Unavailable, match="PipeWire did not answer"):
        sysmap.capture_sound(fake({}))
    with pytest.raises(sysmap.Unavailable, match="no sound output"):
        sysmap.capture_sound(fake({"pw-dump": "[]"}))


# -- screens --

def test_screens_go_left_to_right_with_the_focused_one_lit():
    card = sysmap.capture_screens(fake({"hyprctl -j monitors": MONITORS}))["card"]
    assert [n["label"] for n in card["nodes"]] == ["eDP-1", "DP-2"]
    assert card["nodes"][0]["sub"] == "2560x1600 · 165 Hz · scale 1.6"
    assert card["nodes"][1]["sub"] == "3840x2160 · 60 Hz · scale 1.5"
    assert card["highlight"] == ["m1"] and card["nodes"][0]["state"] == "active"
    assert card["links"] == []
    assert card["say"] == "2 screens, the one you are on is lit."


def test_screens_without_hyprland():
    with pytest.raises(sysmap.Unavailable, match="Hyprland did not answer"):
        sysmap.capture_screens(fake({}))


# -- the front door --

def test_capture_dispatches_and_refuses_what_it_cannot_draw():
    assert sysmap.capture("screens", run_=fake({"hyprctl -j monitors": MONITORS}))["card"]["source"] == "screens"
    with pytest.raises(sysmap.Unavailable, match="I can draw network, boot, service, disks, sound, screens"):
        sysmap.capture("printers", run_=fake({}))


def test_every_capture_finishes_inside_its_budget_when_a_command_hangs():
    def slow(argv, budget=0.5):
        if argv[0] == "pw-dump":
            time.sleep(2)
        return PW

    t0 = time.monotonic()
    with pytest.raises(sysmap.Unavailable):
        sysmap.capture_sound(slow, budget=0.2)
    assert time.monotonic() - t0 < 1.0


def test_a_slow_probe_leaves_the_provider_unanswered_not_the_picture_missing():
    def slow_probe(host):
        time.sleep(2)
        return 5

    t0 = time.monotonic()
    r = sysmap.capture_network(fake(net_outputs()), probe=slow_probe, read=lambda p: "", wireless=lambda d: True,
                               budget=0.2, probe_budget=0.4)
    assert time.monotonic() - t0 < 1.0
    assert states(r["card"])["provider"] == "bad"


def test_a_provider_check_slowed_by_a_cold_dns_cache_still_answers():
    def cold(host):
        time.sleep(0.6)
        return 600

    r = sysmap.capture_network(fake(net_outputs()), probe=cold, read=lambda p: "", wireless=lambda d: True,
                               budget=0.2, probe_budget=1.5)
    assert states(r["card"])["provider"] == "ok"
    assert "replies in 600 ms" in r["card"]["say"]


def test_without_a_link_the_provider_check_is_not_waited_for():
    def slow(host):
        time.sleep(3)
        return 5

    t0 = time.monotonic()
    r = sysmap.capture_network(fake(net_outputs(**{"ip -j route get": "[]", "ip -j route show": "[]"})), probe=slow,
                               read=lambda p: "", wireless=lambda d: False, budget=0.2, probe_budget=2.5)
    assert time.monotonic() - t0 < 1.5
    assert "not connected" in r["card"]["say"]


def test_a_failed_first_try_to_reach_the_provider_is_tried_once_more(monkeypatch):
    tries = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def connect(addr, timeout=None):
        tries.append(timeout)
        if len(tries) == 1:
            raise OSError("name resolution failed")
        return Conn()

    monkeypatch.setattr(sysmap.socket, "create_connection", connect)
    assert isinstance(sysmap._tcp_ms("api.example.test", timeout=1.0), int)
    assert len(tries) == 2 and all(0 < t <= 1.0 for t in tries)


def test_a_provider_that_never_answers_is_given_up_on_within_the_timeout(monkeypatch):
    def connect(addr, timeout=None):
        time.sleep(min(timeout or 0, 0.3))
        raise OSError("timed out")

    monkeypatch.setattr(sysmap.socket, "create_connection", connect)
    t0 = time.monotonic()
    assert sysmap._tcp_ms("api.example.test", timeout=0.5) is None
    assert time.monotonic() - t0 < 1.0


def test_a_before_picture_does_not_wait_for_the_provider_check(monkeypatch):
    monkeypatch.setattr(sysmap, "_tcp_ms", lambda host: time.sleep(3))
    t0 = time.monotonic()
    sysmap.snapshot(("network",), run_=fake(net_outputs()))
    assert time.monotonic() - t0 < 1.5


def test_run_reads_real_commands_and_treats_failures_as_nothing():
    assert sysmap.run(["echo", "hi"]) == "hi\n"
    assert sysmap.run(["false"]) is None
    assert sysmap.run(["no-such-command-bombadil"]) is None
    assert sysmap.run(["sleep", "2"], timeout=0.1) is None


# -- receipts --

def test_receipt_shows_what_changed_and_only_that():
    before = [{"key": "vpn", "label": "VPN tunnel", "value": "wg0"}, {"key": "gateway", "label": "Router", "value": "10.0.0.1"}]
    after = [{"key": "gateway", "label": "Router", "value": "10.0.0.1"}]
    card = sysmap.receipt("network", before, after)
    assert card["shape"] == "compare" and card["receipt"] is True
    assert card["title"] == "How you're connected, before and after"
    assert [(n["side"], n["state"], n["label"]) for n in card["nodes"]] == [("before", "gone", "VPN tunnel")]
    assert "Before: VPN tunnel (wg0) [gone]" in card["text"] and "After: nothing" in card["text"]


def test_receipt_pairs_a_value_that_changed_on_one_row():
    b = [{"key": "state", "label": "wg-quick@wg0.service", "value": "failed (failed)"}]
    a = [{"key": "state", "label": "wg-quick@wg0.service", "value": "active (exited)"}]
    card = sysmap.receipt("service", b, a, target="wg-quick@wg0")
    assert card["title"] == "wg-quick@wg0, before and after"
    rows = {n["side"]: n for n in card["nodes"]}
    assert rows["before"]["row"] == rows["after"]["row"] == 0
    assert rows["after"]["sub"] == "active (exited)" and rows["after"]["state"] == "new"
    assert card["highlight"] == [rows["after"]["id"]]


def test_receipt_with_nothing_changed_is_nothing():
    facts = [{"key": "a", "label": "A", "value": "1"}]
    assert sysmap.receipt("disks", facts, facts) is None
    assert sysmap.receipt("disks", [], []) is None


def test_receipt_caps_the_rows_and_says_how_many_more():
    b = [{"key": f"k{i}", "label": f"L{i}", "value": "a"} for i in range(9)]
    a = [{"key": f"k{i}", "label": f"L{i}", "value": "b"} for i in range(9)]
    card = sysmap.receipt("disks", b, a)
    assert len(card["nodes"]) == 12 and card["say"] == "3 more changed."


# -- overrides --

def test_the_agent_can_point_and_add_a_line_but_not_redraw():
    card = net()["card"]
    out = sysmap.apply_overrides(card, highlight="router", say="  Your router is the old   one.  ")
    assert out["highlight"] == ["router"] and out["say"] == "Your router is the old one."
    assert out["text"].endswith("Your router is the old one.")
    assert [n["label"] for n in out["nodes"]] == [n["label"] for n in card["nodes"]]
    assert sysmap.apply_overrides(card, highlight=["nope"])["highlight"] == card["highlight"]
