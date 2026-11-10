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


def test_unknown_until_the_charge_moves(coordinator):
    assert _polls(coordinator, [80.0, 80.0, 80.0]) == [None, None, None]


def test_a_rise_holds_through_the_flat_polls_that_follow(coordinator):
    # 80 -> 81, then an hour of 81. The old logic went unknown after one poll.
    states = _polls(coordinator, [80.0, 81.0] + [81.0] * 60)
    assert states[0] is None
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


def test_a_missed_reading_is_unknown_and_does_not_invent_a_change(coordinator):
    _polls(coordinator, [50.0, 51.0])
    assert coordinator._charging_state(None, 0.0) is None
    # 51 -> (missing) -> 60 is not compared across the gap, but the hold remains.
    assert coordinator._charging_state(60.0, 0.0) is True


@pytest.mark.parametrize("storage", [None, True, float("nan"), "1.0"])
def test_odd_storage_values_are_ignored(coordinator, storage):
    _polls(coordinator, [50.0, 51.0])
    assert coordinator._charging_state(51.0, storage) is True
