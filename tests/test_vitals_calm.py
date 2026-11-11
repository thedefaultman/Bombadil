"""A Machine the person keeps on the desk: up at rest in a calm shape, cheap to keep, and the card it always was
the moment something is wrong or someone asks. The readings are handed in, as in test_vitals.py."""

import json

import pytest
from test_vitals import GB, reading, rig, run

from bombadil import vitals
from bombadil.vitals import (
    ASK_FOR,
    CALM_EVERY,
    FAST_EVERY,
    MIN_GAP,
    NEAR_EVERY,
    Reading,
    calm_rate,
    calm_rate_text,
)

CALM_ROWS = [("memory", "meter"), ("disk", "meter"), ("cpu", "meter"), ("net", "plain")]


def kept(**now):
    """A kept Machine that has taken its two first samples, and the message the second gave. A card at rest waits
    for the second, a second after the first: the processor's share and the rates need two reads, so a card drawn
    after one would say 0% and dashes."""
    v, fake, clock = rig()
    v.keep(True)
    first = run(v, fake, clock, 1, step=FAST_EVERY, **now)[0]
    assert first["present"] is False and first["rows"] == []           # nothing half-known is drawn
    msg = run(v, fake, clock, 1, step=CALM_EVERY, **now)[0]
    return v, fake, clock, msg


def test_a_machine_that_is_not_kept_has_no_card_at_rest():
    v, fake, clock = rig()
    first, *rest = run(v, fake, clock, 3, step=CALM_EVERY)     # the first message always goes out: "no card"
    assert first["present"] is False and first["calm"] is False and rest == [None, None]


def test_a_kept_machine_has_a_card_at_rest_that_says_the_machine_is_fine():
    v, fake, clock, m = kept(memory=0.40, disk=0.50, heat=62.4, cpu=0.12, net=(1.2e6, 40e3))
    assert m == {
        "type": "machine", "present": True, "asked": False, "calm": True, "why": "The machine is fine",
        "strip": {"text": "heat 60°", "dot": ""},     # the reading nearest its line, in the card's own figures
        "rows": [
            {"key": "memory", "kind": "meter", "title": "Memory", "meter": 0.4, "meterText": "6.4 of 16 GB",
             "tone": "you", "opens": ""},
            {"key": "disk", "kind": "meter", "title": "Disk", "meter": 0.5, "meterText": "115 of 230 GB",
             "tone": "you", "opens": "disk"},
            {"key": "cpu", "kind": "meter", "title": "Processor", "meter": 0.2, "meterText": "60°",
             "tone": "you", "opens": ""},
            {"key": "net", "kind": "plain", "title": "Network", "sub": "↓ 1 MB/s   ↑ 40 kB/s", "tone": "you",
             "opens": ""}]}
    json.dumps(m)


def test_memory_is_one_meter_at_rest_never_the_stack_of_who_holds_it():
    # Who holds the memory needs the walk of the memory groups, and a card at rest has nothing to say about who.
    v, fake, clock, m = kept(memory=0.40, machine_=2 * GB, held=3 * GB)
    memory = next(r for r in m["rows"] if r["key"] == "memory")
    assert memory["kind"] == "meter" and "parts" not in memory
    assert [(r["key"], r["kind"]) for r in m["rows"]] == CALM_ROWS


def test_a_kept_machine_is_sampled_as_cheaply_as_any_calm_machine():
    v, fake, clock = rig()
    v.keep(True)
    first = run(v, fake, clock, 1, step=FAST_EVERY, memory=0.40)[0]
    # A processor share and a rate need two reads, so the second comes a second after the first, once, and the card
    # waits for it: it is never drawn saying 0% and dashes.
    assert v.next_delay() == FAST_EVERY and first["present"] is False
    assert run(v, fake, clock, 1, step=CALM_EVERY, memory=0.40)[0]["calm"] is True
    assert v.next_delay() == CALM_EVERY
    run(v, fake, clock, 5, step=CALM_EVERY, memory=0.40)
    # The first sample reads everything, so a client connecting sees nothing half-known; the rest do not walk.
    assert fake.fast == [True] + [False] * 6


def test_a_kept_machine_draws_nothing_until_the_second_sample_has_given_it_its_rates():
    v, fake, clock = rig()
    v.keep(True)
    fake.reading = reading(memory=0.40, cpu=0.12, net=(1.2e6, 40e3))
    first = v.tick()                                      # what a bar that connects at once is told
    assert first["present"] is False and first["rows"] == [] and v.message()["present"] is False
    clock.t += FAST_EVERY
    second = v.tick()
    assert second["present"] is True and second["calm"] is True
    assert next(r for r in second["rows"] if r["key"] == "net")["sub"] == "↓ 1 MB/s   ↑ 40 kB/s"
    assert next(r for r in second["rows"] if r["key"] == "cpu")["meter"] == 0.2
    assert v.message() == second                          # ...and so is the bar that connects now


def test_a_card_that_is_asked_for_or_has_a_line_crossed_does_not_wait_for_a_second_sample():
    v, fake, clock = rig()
    v.keep(True)
    v.ask()
    assert v.tick()["present"] is True                    # asked: the live card, as it always was
    v2, fake2, clock2 = rig()
    v2.keep(True)
    msgs = run(v2, fake2, clock2, 3, memory=0.95)         # a line crossing is a card in its own right
    assert msgs[-1]["present"] is True and msgs[-1]["calm"] is False


def test_a_machine_that_is_not_kept_is_not_sampled_twice_at_the_start():
    v, fake, clock = rig()
    run(v, fake, clock, 1, step=CALM_EVERY, memory=0.40)
    assert v.next_delay() == CALM_EVERY


def test_the_second_sample_after_a_reset_comes_a_second_later_again_for_a_kept_machine():
    v, fake, clock, _ = kept(memory=0.40)
    run(v, fake, clock, 1, step=FAST_EVERY, memory=0.40)
    v.reset()                          # the last bar left: the readings go, the person's choice does not
    run(v, fake, clock, 1, step=CALM_EVERY, memory=0.40)
    assert v.next_delay() == FAST_EVERY


def test_figures_are_rounded_so_a_message_goes_out_only_when_a_shown_figure_moves():
    v, fake, clock, first = kept(memory=0.40, disk=0.50, heat=62.4, cpu=0.12, net=(1.2e6, 40e3))
    # Wobbles that stay inside a figure's step say nothing at all.
    wobble = [dict(memory=0.41, disk=0.51, heat=61.9, cpu=0.14, net=(1.3e6, 42e3)),
              dict(memory=0.38, disk=0.49, heat=58.0, cpu=0.08, net=(1.1e6, 38e3)),
              dict(memory=0.42, disk=0.52, heat=60.2, cpu=0.10, net=(1.4e6, 44e3))]
    for now in wobble:
        assert run(v, fake, clock, 1, step=CALM_EVERY, **now) == [None]
    # A figure that moves a step says the card again, once.
    moved = run(v, fake, clock, 2, step=CALM_EVERY, memory=0.44, disk=0.50, heat=62.4, cpu=0.12, net=(1.2e6, 40e3))
    assert moved[0] is not None and moved[0]["rows"][0]["meter"] == 0.45 and moved[1] is None


def test_a_wobble_inside_each_figures_step_says_nothing():
    import random
    rnd = random.Random(7)
    v, fake, clock, _ = kept(memory=0.40, disk=0.50, heat=60.0, cpu=0.10, net=(1.2e6, 40e3))
    sent = 0
    for _ in range(200):
        fake.reading = reading(memory=0.40 + rnd.uniform(-0.02, 0.02), disk=0.50 + rnd.uniform(-0.02, 0.02),
                               heat=60.0 + rnd.uniform(-2.0, 2.0), cpu=0.10 + rnd.uniform(-0.04, 0.04),
                               net=(1.2e6 + rnd.uniform(-1e5, 1e5), 40e3 + rnd.uniform(-2e3, 2e3)))
        sent += v.tick() is not None
        clock.t += CALM_EVERY
    assert sent == 0


@pytest.mark.parametrize("rate, said", [
    (0, "0 B/s"), (999, "0 B/s"), (1000, "0 B/s"), (9_999, "0 B/s"), (10_000, "10 kB/s"), (14_900, "10 kB/s"),
    (43_000, "40 kB/s"), (996_000, "1 MB/s"), (1.2e6, "1 MB/s"), (8.7e6, "9 MB/s"), (9.6e6, "10 MB/s"),
    (999_999_999, "1 GB/s"), (-5, "0 B/s")])
def test_a_calm_rate_has_one_figure_and_nothing_under_ten_kilobytes(rate, said):
    assert calm_rate_text(rate) == said


@pytest.mark.parametrize("x, step, said", [
    (0.47, 0.05, 0.45), (0.5, 0.05, 0.5), (0.026, 0.05, 0.05), (1.2, 0.05, 1.0), (-1, 0.05, 0.0),
    (0.12, 0.10, 0.1), (0.96, 0.10, 1.0)])
def test_a_calm_fraction_is_held_to_the_track_and_rounded_to_its_step(x, step, said):
    assert vitals._calm(x, step) == said


def test_a_line_crossed_turns_the_calm_card_into_the_card_it_always_was_and_back():
    v, fake, clock, calm = kept(memory=0.40, machine_=2 * GB, held=3 * GB)
    assert calm["calm"] is True
    up = [m for m in run(v, fake, clock, 3, memory=0.93, machine_=2 * GB, held=3 * GB) if m][-1]
    assert up["calm"] is False and up["asked"] is False and up["why"].startswith("Memory is nearly full")
    assert up["strip"] == {"text": "memory 93%", "dot": "amber"}
    memory = up["rows"][0]
    assert memory["kind"] == "stack" and memory["tone"] == "amber" and memory["parts"]
    assert v.next_delay() == FAST_EVERY
    # Back under its lower mark for long enough: calm again, in a message of its own.
    back = None
    for _ in range(40):
        fake.reading = reading(memory=0.40)
        got = v.tick()
        clock.t += 1
        back = got or back
    assert back is not None and back["calm"] is True and back["present"] is True and back["asked"] is False


def test_a_spike_does_not_flicker_a_kept_card_but_an_asked_one_never_says_fine_over_a_line():
    v, fake, clock, _ = kept(memory=0.40)
    # One sample over its line: it has not held for its three, so a card that is only kept stays calm (its meter
    # says what it reads); sampling goes to every second meanwhile.
    spike = run(v, fake, clock, 1, step=1.0, memory=0.95)[0]
    assert spike["calm"] is True and spike["why"] == "The machine is fine" and spike["rows"][0]["meter"] == 0.95
    assert v.next_delay() == NEAR_EVERY
    back = run(v, fake, clock, 1, step=1.0, memory=0.40)[0]
    assert back["calm"] is True and back["rows"][0]["meter"] == 0.4 and back["rows"][0]["meterText"] == "6.4 of 16 GB"
    # An asked one is the card of that moment: it does not say the machine is fine over a line.
    v.ask()
    asked = run(v, fake, clock, 1, step=1.0, memory=0.95)[0]
    assert asked["calm"] is False and asked["asked"] is True and asked["why"] == "Memory is nearly full"


def test_asking_a_kept_machine_is_a_live_card_for_a_while_and_then_calm_again():
    v, fake, clock, _ = kept(memory=0.40)
    v.ask()
    asked = run(v, fake, clock, 1, step=1.0, memory=0.40)[0]
    assert asked["asked"] is True and asked["calm"] is False and asked["why"] == "The machine is fine"
    assert asked["rows"][0]["kind"] == "stack"
    assert v.next_delay() == FAST_EVERY
    clock.t += ASK_FOR + 1
    after = run(v, fake, clock, 1, step=1.0, memory=0.40)[0]
    assert after["calm"] is True and after["asked"] is False and v.next_delay() == CALM_EVERY


def test_a_reading_near_its_line_is_sampled_a_little_sooner_even_when_kept():
    v, fake, clock, _ = kept(memory=0.40)
    run(v, fake, clock, 1, step=CALM_EVERY, memory=0.87)       # within five points of 90%
    assert v.next_delay() == NEAR_EVERY


def test_the_chip_of_a_calm_card_does_not_change_on_a_wobble():
    v, fake, clock, m = kept(memory=0.40, heat=62.4)
    assert m["strip"] == {"text": "heat 60°", "dot": ""}
    for heat in (61.0, 58.0, 62.4, 60.3):
        assert run(v, fake, clock, 1, step=CALM_EVERY, memory=0.40, heat=heat) == [None]
    assert run(v, fake, clock, 1, step=CALM_EVERY, memory=0.40, heat=66.0)[0]["strip"]["text"] == "heat 65°"


def test_no_heat_sensor_still_gives_a_calm_card():
    v, fake, clock, m = kept(memory=0.40, heat=None)
    cpu = next(r for r in m["rows"] if r["key"] == "cpu")
    assert m["calm"] is True and cpu["meterText"] == "" and m["strip"]["text"].startswith(("memory", "disk", "sessions"))


def test_a_rate_that_is_not_known_yet_is_a_dash_not_a_zero():
    v, fake, clock = rig()
    v.keep(True)
    fake.reading = Reading(cpu=0.1, memory=reading().memory, disk=reading().disk, heat=50.0, net=None)
    v.tick()                                              # a machine with no interface to read, so never a rate
    clock.t += FAST_EVERY
    m = v.tick()
    assert next(r for r in m["rows"] if r["key"] == "net")["sub"] == "↓ –   ↑ –"


def test_a_machine_that_cannot_be_read_still_has_a_card_with_what_can():
    v, fake, clock = rig()
    v.keep(True)
    fake.reading = Reading(cpu=None, memory=None, disk=None, heat=None, net=None)
    v.tick()
    clock.t += FAST_EVERY
    m = v.tick()
    assert m["present"] is True and m["calm"] is True and m["strip"]["text"] == "machine"
    assert [r["key"] for r in m["rows"]] == ["cpu", "net"]


def test_stopping_keeping_takes_the_card_away_and_reset_does_not_forget_it_was_kept():
    v, fake, clock, m = kept(memory=0.40)
    v.reset()                          # no shell is listening: the readings go, the person's choice does not
    assert v.message()["present"] is False
    again = run(v, fake, clock, 2, step=FAST_EVERY, memory=0.40)[-1]      # the second sample draws it again
    assert again is not None and again["calm"] is True
    v.keep(False)
    gone = run(v, fake, clock, 1, step=CALM_EVERY, memory=0.40)[0]
    assert gone["present"] is False and gone["rows"] == []


def test_keeping_is_said_by_the_next_message_even_with_nothing_else_changed():
    v, fake, clock = rig()
    nothing = run(v, fake, clock, 2, step=CALM_EVERY)
    assert nothing[0]["present"] is False and nothing[1] is None
    v.keep(True)
    assert run(v, fake, clock, 1, step=CALM_EVERY)[0]["calm"] is True
    assert v.message()["calm"] is True                      # and so is a client that connects now


# -- held figures: a reading on the edge between two steps does not change the card at every sample --

@pytest.mark.parametrize("x, shown, said", [
    (0.47, None, 0.45),       # nothing shown yet: the nearest step
    (0.47, 0.50, 0.50),       # 0.03 from the step it shows: held, though 0.45 is nearer
    (0.53, 0.50, 0.50),       # ...whichever side it is on
    (0.46, 0.50, 0.45),       # 0.04 from it, more than three quarters of a step: clearly another, so it moves
    (0.54, 0.50, 0.55),
    (0.99, 1.0, 1.0), (1.2, 1.0, 1.0), (-0.3, 0.0, 0.0)])
def test_a_calm_fraction_holds_the_figure_it_shows_until_the_reading_is_clearly_another(x, shown, said):
    assert vitals._calm(x, 0.05, shown) == said


@pytest.mark.parametrize("c, shown, said", [
    (47.4, None, 45), (47.6, None, 50), (47.6, 45, 45), (48.8, 45, 50), (41.0, 45, 40), (43.0, 45, 45)])
def test_calm_heat_holds_the_figure_it_shows_too(c, shown, said):
    assert vitals._calm_deg(c, shown) == said


@pytest.mark.parametrize("rate, shown, said", [
    (3_000, None, 0), (3_000, 0.0, 0),
    (10_500, 0.0, 10_000),           # leaves quiet at ten kilobytes
    (8_000, 10_000, 10_000),         # ...and does not go back to it for the first dip below
    (6_000, 10_000, 0),              # until it is clearly quiet (under 7.5 kB/s)
    (45_000, 40_000, 40_000),        # a rate within a half of what it shows is the same rate
    (59_000, 40_000, 40_000), (61_000, 40_000, 60_000),
    (26_000, 40_000, 30_000), (2.7e6, 1e6, 3e6)])
def test_a_calm_rate_holds_the_figure_it_shows_until_the_reading_is_clearly_another(rate, shown, said):
    assert calm_rate(rate, shown) == said


def _messages(v, fake, clock, samples):
    """Feed a list of readings a calm step apart; how many of them said the card again."""
    sent = 0
    for now in samples:
        fake.reading = reading(**now)
        sent += v.tick() is not None
        clock.t += CALM_EVERY
    return sent


def test_an_idle_machines_ordinary_wobble_says_almost_nothing_even_across_every_edge():
    import random
    rnd = random.Random(3)
    v, fake, clock, _ = kept(memory=0.475, disk=0.60, heat=47.5, cpu=0.05, net=(2e3, 1e3))
    run(v, fake, clock, 1, step=FAST_EVERY, memory=0.475, disk=0.60, heat=47.5, cpu=0.05, net=(2e3, 1e3))
    samples = [dict(memory=0.475 + rnd.uniform(-0.003, 0.003),     # on the edge between 45% and 50%
                    disk=0.60 + rnd.uniform(-0.002, 0.002),
                    heat=47.5 + rnd.uniform(-1.0, 1.0),            # on the edge between 45 and 50 degrees
                    cpu=rnd.uniform(0.01, 0.08),                   # an idle desktop, either side of 5%
                    net=(rnd.uniform(0, 5e3), rnd.uniform(0, 3e3)))
               for _ in range(720)]                                # an hour at a sample every five seconds
    sent = _messages(v, fake, clock, samples)
    # Without holding, this machine says the card at about every other sample (the edges are 354 of 720 each).
    assert sent <= 5, sent


def test_a_machine_that_really_moves_still_says_so():
    v, fake, clock, _ = kept(memory=0.40, disk=0.50, heat=50.0, cpu=0.10, net=(1.2e6, 40e3))
    # A download starts, the processor works, the machine warms and memory fills: each is said as it moves.
    got = [run(v, fake, clock, 1, step=CALM_EVERY, **now)[0]
           for now in (dict(memory=0.40, disk=0.50, heat=50.0, cpu=0.10, net=(9e6, 40e3)),
                       dict(memory=0.40, disk=0.50, heat=50.0, cpu=0.60, net=(9e6, 40e3)),
                       dict(memory=0.40, disk=0.50, heat=62.0, cpu=0.60, net=(9e6, 40e3)),
                       dict(memory=0.70, disk=0.50, heat=62.0, cpu=0.60, net=(9e6, 40e3)))]
    assert all(m is not None for m in got)
    assert "9 MB/s" in got[0]["rows"][3]["sub"] and got[1]["rows"][2]["meter"] == 0.6
    assert got[2]["rows"][2]["meterText"] == "60°" and got[3]["rows"][0]["meter"] == 0.7


def test_the_chip_is_picked_from_the_figures_the_card_shows_so_two_close_readings_do_not_trade_places():
    # Memory and disk are 0.50 and 0.51: the same 50% on the card, so the nearer to its line is a tie, whichever
    # reading is a hair higher, and the chip does not swap between them.
    v, fake, clock, m = kept(memory=0.50, disk=0.51)
    assert m["strip"] == {"text": "memory 50%", "dot": ""}
    for now in (dict(memory=0.51, disk=0.50), dict(memory=0.50, disk=0.51), dict(memory=0.505, disk=0.504)):
        assert run(v, fake, clock, 1, step=CALM_EVERY, **now) == [None]


def test_the_sizes_of_a_calm_card_are_whole_numbers_from_ten_gigabytes():
    # The raised card keeps a decimal below 100 GB ("12.8 of 16 GB"); a card at rest has no use for the decimal.
    v, fake, clock, m = kept(memory=0.80, disk=0.05)
    assert [r["meterText"] for r in m["rows"][:2]] == ["13 of 16 GB", "12 of 230 GB"]
    v, fake, clock, m = kept(memory=0.40, disk=0.50)
    assert [r["meterText"] for r in m["rows"][:2]] == ["6.4 of 16 GB", "115 of 230 GB"]


# -- a wake is not a sample --

def test_a_wake_a_moment_after_a_sample_reads_nothing_more_and_still_says_what_changed():
    v, fake, clock, _ = kept(memory=0.40)
    v.tick()                                            # the second sample, on its own time...
    reads = len(fake.fast)
    v.keep(False)                                       # ...and the desk changed a moment later, with a wake
    clock.t += MIN_GAP / 2
    gone = v.tick(woken=True)
    assert len(fake.fast) == reads                       # no read: a rate over this little time is noise
    assert gone is not None and gone["present"] is False  # ...and the card is still said as it now is
    v.keep(True)
    assert v.tick(woken=True)["calm"] is True and len(fake.fast) == reads


def test_a_wake_after_the_gap_reads_again_and_a_tick_that_was_not_woken_always_does():
    v, fake, clock, _ = kept(memory=0.40)
    reads = len(fake.fast)
    clock.t += MIN_GAP
    v.tick(woken=True)
    assert len(fake.fast) == reads + 1
    v.tick()                                            # on its own time, whatever the clock says
    assert len(fake.fast) == reads + 2


def test_a_burst_of_wakes_is_not_three_samples_in_a_row_over_a_line():
    v, fake, clock, _ = kept(memory=0.40)
    run(v, fake, clock, 1, step=FAST_EVERY, memory=0.40)
    fake.reading = reading(memory=0.95)
    for _ in range(5):                                  # five connections within a moment of each other
        clock.t += 0.05
        assert (v.tick(woken=True) or {"calm": True})["calm"] is True
    assert v.message()["calm"] is True and v.message()["why"] == "The machine is fine"
