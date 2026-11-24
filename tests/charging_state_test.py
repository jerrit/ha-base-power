"""Battery Charging: Base reports whole percents, so direction has to be held."""

from __future__ import annotations

import pytest

from custom_components.base_power.const import CHARGING_HOLD_SECONDS
from custom_components.base_power.coordinator import BasePowerCoordinator


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def coordinator():
    instance = BasePowerCoordinator.__new__(BasePowerCoordinator)
    instance._prev_soc = None
    instance._monotonic = Clock()
    return instance


def _polls(coordinator, percents, storage_kw=0.0, step=60.0):
    states = []
    for percent in percents:
        coordinator._monotonic.now += step
        states.append(coordinator._charging_state(percent, storage_kw))
    return states


def test_unknown_until_the_charge_moves_without_a_power_reading(coordinator):
    assert _polls(coordinator, [80.0, 80.0, 80.0], storage_kw=None) == [None, None, None]


def test_an_idle_battery_reads_off_not_unknown(coordinator):
    # Issue #26: a battery sitting at 95% on grid read unknown for days.
    assert _polls(coordinator, [95.0, 95.0, 95.0]) == [False, False, False]
    # A rise still turns it on.
    assert _polls(coordinator, [96.0]) == [True]


def test_a_rise_holds_through_the_flat_polls_that_follow(coordinator):
    # 80 -> 81, then an hour of 81. The old logic went unknown after one poll.
    states = _polls(coordinator, [80.0, 81.0] + [81.0] * 60)
    assert states[0] is False
    assert all(state is True for state in states[1:])


def test_a_fall_means_not_charging_and_stays_that_way(coordinator):
    states = _polls(coordinator, [80.0, 79.0] + [79.0] * 300)
    assert all(state is False for state in states[1:])


def test_a_full_battery_is_not_charging(coordinator):
    assert _polls(coordinator, [100.0]) == [False]
    # The usual 99 <-> 100 flicker never reads as charging or unknown.
    assert _polls(coordinator, [99.0, 99.0, 100.0, 100.0]) == [False, False, False, False]


def test_a_rise_is_dropped_after_the_hold_without_another_rise(coordinator):
    _polls(coordinator, [50.0, 51.0])
    assert coordinator._charging_state(51.0, 0.0) is True
    coordinator._monotonic.now += CHARGING_HOLD_SECONDS + 1
    assert coordinator._charging_state(51.0, 0.0) is False
    # The next rise turns it back on.
    coordinator._monotonic.now += 60
    assert coordinator._charging_state(52.0, 0.0) is True


def test_battery_powering_the_home_is_not_charging(coordinator):
    _polls(coordinator, [50.0, 51.0])
    assert coordinator._charging_state(51.0, 1.5) is False
    # Noise below the grid-support threshold does not count as discharging.
    _polls(coordinator, [52.0])
    assert coordinator._charging_state(52.0, 0.1) is True


def test_a_missed_reading_does_not_invent_a_change(coordinator):
    _polls(coordinator, [50.0, 51.0])
    assert coordinator._charging_state(None, None) is None
    # Without a percent, a power reading with no charge still means off.
    assert coordinator._charging_state(None, 0.0) is False
    # 51 -> (missing) -> 60 is not compared across the gap, but the hold remains.
    assert coordinator._charging_state(60.0, 0.0) is True


@pytest.mark.parametrize("storage", [None, True, float("nan"), "1.0"])
def test_odd_storage_values_are_ignored(coordinator, storage):
    _polls(coordinator, [50.0, 51.0])
    assert coordinator._charging_state(51.0, storage) is True


def test_one_rise_holds_for_an_hour_not_three(coordinator):
    # A battery idling at its reserve that ticks up once does not stay "charging".
    from custom_components.base_power.const import CHARGING_FIRST_HOLD_SECONDS

    _polls(coordinator, [20.0, 21.0])
    coordinator._monotonic.now += CHARGING_FIRST_HOLD_SECONDS - 60
    assert coordinator._charging_state(21.0, 0.0) is True
    coordinator._monotonic.now += 120
    assert coordinator._charging_state(21.0, 0.0) is False


def test_a_steady_slow_charge_stretches_the_hold(coordinator):
    # One percent every 80 minutes: the hold follows the pace, so it stays on.
    _polls(coordinator, [50.0, 51.0])
    coordinator._monotonic.now += 80 * 60
    assert coordinator._charging_state(52.0, 0.0) is True
    states = _polls(coordinator, [52.0] * 79)
    assert all(state is True for state in states)
    coordinator._monotonic.now += CHARGING_HOLD_SECONDS
    assert coordinator._charging_state(52.0, 0.0) is False


def test_rounding_flicker_at_the_reserve_is_not_charging(coordinator):
    states = _polls(coordinator, [21.0, 20.0, 21.0, 20.0, 21.0, 21.0])
    assert all(state is False for state in states[1:])


def test_a_rise_past_the_level_it_fell_from_is_charging(coordinator):
    _polls(coordinator, [21.0, 20.0, 21.0])
    assert _polls(coordinator, [22.0]) == [True]


def _flow(coordinator, readings, step=60.0):
    states = []
    for percent, storage in readings:
        coordinator._monotonic.now += step
        states.append(coordinator._charging_state(percent, storage))
    return states


def test_a_top_up_from_99_is_charging_while_power_goes_in(coordinator):
    # Seen on a GRO_SINGLE: it drifts to 99%, charges at 0.2-0.5 kW back to
    # 100%, then idles. The percent alone reads that as rounding.
    states = _flow(
        coordinator,
        [(100.0, 0.0), (99.0, 0.0), (99.0, -0.5), (99.0, -0.5), (100.0, -0.2), (100.0, 0.0)],
    )
    assert states == [False, False, True, True, True, False]


def test_power_going_in_is_charging_before_the_percent_has_moved(coordinator):
    assert _flow(coordinator, [(60.0, -1.0)]) == [True]


def test_charging_ends_when_the_power_stops(coordinator):
    # A rise would hold for an hour, but live power says the charge stopped.
    states = _flow(coordinator, [(50.0, -1.0), (51.0, -1.0), (51.0, 0.0), (51.0, 0.0)])
    assert states == [True, True, False, False]


def test_a_missed_power_reading_keeps_the_percent_logic(coordinator):
    # None means no reading, not "stopped", so the rise still holds.
    states = _flow(coordinator, [(50.0, -1.0), (51.0, -1.0), (51.0, None)])
    assert states == [True, True, True]


def test_a_tenth_of_a_kilowatt_in_is_noise(coordinator):
    assert _flow(coordinator, [(100.0, 0.0), (99.0, -0.1), (99.0, -0.1)]) == [False, False, False]


def test_power_going_in_counts_without_a_percent(coordinator):
    assert coordinator._charging_state(None, -0.5) is True
    assert coordinator._charging_state(None, 0.0) is False
    assert coordinator._charging_state(None, None) is None
