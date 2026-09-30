import json

import pytest

from bombadil import hypr


@pytest.fixture(autouse=True)
def _isolated(home):
    """Nothing here may reach a real compositor (HYPRLAND_INSTANCE_SIGNATURE is cleared) or write
    the real runtime dir, where the slots handed out are kept."""


def _monitor(w=1600, h=900, reserved=(0, 0, 0, 64), scale=1, x=0, y=0, transform=0):
    return {"name": "DP-1", "focused": True, "width": w, "height": h, "scale": scale, "x": x, "y": y,
            "transform": transform, "reserved": list(reserved)}


def test_app_slots_start_centered_above_the_bar_and_step_down_and_right():
    assert hypr.app_slots(_monitor(1920, 1080)) == [(690, 178), (738, 226), (786, 274)]


def test_the_step_shows_the_heading_of_the_card_it_covers():
    # A card's title sits about 30 px under its top edge; the next card's edge must pass below it.
    steps = {b[1] - a[1] for slots in (hypr.app_slots(_monitor(1920, 1080)), hypr.app_slots(_monitor()))
             for a, b in zip(slots, slots[1:])}
    assert min(steps) >= 44


def test_a_short_screen_starts_the_cascade_above_center_before_the_steps_shrink():
    slots = hypr.app_slots(_monitor())   # 1600x900: 88 px of room, a full cascade needs 96
    assert slots == [(522, 80), (570, 128), (618, 176)]
    assert slots[-1][1] + 660 == 900 - 64


def test_the_steps_shrink_so_the_last_card_stays_above_the_bar():
    slots = hypr.app_slots(_monitor(1280, 800))
    assert slots[0] == (332, 0)
    assert slots[-1][1] + 660 <= 800 - 64
    assert slots[0] != slots[1] != slots[2]


def test_a_card_as_tall_as_the_room_has_one_slot_at_the_top():
    slots = hypr.app_slots(_monitor(1366, 700))
    assert slots == [(413, 0)] * 3


def test_app_slots_follow_scale_and_rotation():
    assert hypr.app_slots(_monitor(3200, 1800, scale=2)) == hypr.app_slots(_monitor(1600, 900))
    assert hypr.app_slots(_monitor(900, 1600, transform=1, reserved=(0, 0, 0, 64))) == hypr.app_slots(_monitor(1600, 900))


class Ipc(hypr.Hyprland):
    """Hyprland's IPC, answering from lists."""
    available = True

    def __init__(self, clients=(), monitors=None, reply="ok"):
        self._clients = list(clients)
        self._monitors = monitors if monitors is not None else [_monitor(1920, 1080)]
        self.reply = reply
        self.sent = []

    def request(self, command):
        self.sent.append(command)
        if command == "j/monitors":
            return json.dumps(self._monitors)
        if command == "j/clients":
            return json.dumps(self._clients)
        return self.reply


def _card(slot, name="passwords", floating=True):
    return {"class": f"bombadil-app-{name}", "floating": floating, "at": list(slot)}


def _rule(h):
    return [c for c in h.sent if c.startswith("eval ")]


def test_the_first_app_opens_centered_through_a_rule_named_for_it():
    h = Ipc()
    assert h.place_app("passwords") == "passwords opens at 690,178"
    assert _rule(h) == ['eval hl.window_rule({ name = "bombadil-app-passwords", '
                        'match = { class = "^(bombadil-app-passwords)$" }, move = { 690, 178 } })']


def test_the_next_app_takes_the_next_free_slot():
    h = Ipc(clients=[_card((690, 178))])
    assert h.place_app("memory-viewer") == "memory-viewer opens at 738,226"
    h = Ipc(clients=[_card((690, 178)), _card((738, 226), "memory-viewer")])
    assert h.place_app("smoke-test") == "smoke-test opens at 786,274"


def test_a_slot_freed_by_closing_an_app_is_used_again():
    h = Ipc(clients=[_card((738, 226), "memory-viewer")])
    assert h.place_app("notes") == "notes opens at 690,178"


def test_with_every_slot_taken_it_cycles_by_count():
    slots = hypr.app_slots(_monitor(1920, 1080))
    h = Ipc(clients=[_card(s, f"a{i}") for i, s in enumerate(slots)] + [_card((1, 1), "a9")])
    assert h.place_app("new") == f"new opens at {slots[4 % 3][0]},{slots[4 % 3][1]}"


def test_other_windows_and_tiled_apps_do_not_take_a_slot():
    h = Ipc(clients=[{"class": "foot", "floating": True, "at": [690, 178]}, _card((690, 178), floating=False)])
    assert h.place_app("passwords") == "passwords opens at 690,178"


def test_slots_are_in_the_focused_monitors_own_coordinates():
    second = _monitor(1920, 1080, x=1920)
    first = {**_monitor(1920, 1080), "focused": False}
    h = Ipc(clients=[_card((1920 + 690, 178))], monitors=[first, second])
    assert h.place_app("notes") == "notes opens at 738,226"


def test_it_never_raises_and_says_why_it_did_not_pick():
    assert hypr.Hyprland().place_app("passwords") == ""   # no Hyprland here
    assert "no spot picked" in Ipc(reply="error: bad").place_app("passwords")

    class Down(Ipc):
        def request(self, command):
            raise RuntimeError("Hyprland is not running")
    assert "no spot picked" in Down().place_app("passwords")
    assert "no spot picked" in Ipc(monitors=[]).place_app("passwords")


@pytest.mark.parametrize("name", ['a"b', "a b", "", "-x", "a\n"])
def test_a_name_that_is_not_an_app_name_never_reaches_lua(name):
    h = Ipc()
    assert h.place_app(name) == "" and h.sent == []


def test_two_apps_opened_a_moment_apart_do_not_share_a_slot():
    # A window takes seconds to map, so the second open sees no window of the first.
    h = Ipc()
    assert h.place_app("passwords") == "passwords opens at 690,178"
    assert h.place_app("memory-monitor") == "memory-monitor opens at 738,226"
    assert h.place_app("notes") == "notes opens at 786,274"


def test_the_same_app_asking_again_keeps_its_own_slot():
    h = Ipc()
    assert h.place_app("passwords") == h.place_app("passwords") == "passwords opens at 690,178"


def test_a_slot_handed_out_is_the_windows_once_it_maps_and_free_again_when_it_never_does(monkeypatch):
    import time
    from types import SimpleNamespace
    now = [1000.0]
    monkeypatch.setattr(hypr, "time", SimpleNamespace(time=lambda: now[0], sleep=time.sleep))
    h = Ipc()
    h.place_app("passwords")
    # Its window mapped where it was told: one slot taken, not counted twice.
    h._clients = [_card((690, 178))]
    assert h.place_app("memory-monitor") == "memory-monitor opens at 738,226"
    # memory-monitor never mapped: ten seconds later its slot is free again.
    now[0] += hypr.PENDING_SECS + 1
    assert h.place_app("notes") == "notes opens at 738,226"


def test_a_spot_that_was_not_set_is_not_held():
    h = Ipc(reply="error: bad")
    assert "no spot picked" in h.place_app("passwords")
    h.reply = "ok"
    assert h.place_app("memory-monitor") == "memory-monitor opens at 690,178"


def test_a_broken_placements_file_is_ignored(home):
    (home / "run").mkdir(parents=True, exist_ok=True)
    (home / "run" / "app-placements.json").write_text('[{"name": 3}, "x", {"name": "a", "t": "now", "at": []}]')
    assert Ipc().place_app("passwords") == "passwords opens at 690,178"
