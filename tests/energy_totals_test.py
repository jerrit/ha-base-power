"""Running energy totals: every hour counted once, never going down."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "custom_components.base_power"


from custom_components.base_power import totals as totals_mod  # noqa: E402

NOW = 1_800_000_000


def _hour(start, home, solar=0.0):
    return {"start": start, "home_kwh": home, "solar_kwh": solar}


def test_unknown_until_the_first_hour():
    totals = totals_mod.EnergyTotals()
    assert totals.started is False
    assert totals.add_hours([], NOW) is False
    assert totals.started is False


def test_the_late_hour_after_midnight_is_counted():
    totals = totals_mod.EnergyTotals()
    totals.add_hours([_hour(NOW - 7200, 1.0), _hour(NOW - 10800, 1.5)], NOW)
    # The 23:00 hour shows up after midnight, next to the new day's first hour.
    assert totals.add_hours([_hour(NOW - 7200, 1.0), _hour(NOW - 3600, 1.3), _hour(NOW, 0.7)], NOW)
    assert round(totals.home_kwh, 3) == 4.5


def test_repeats_are_not_counted_twice():
    totals = totals_mod.EnergyTotals()
    hours = [_hour(NOW - 3600, 1.0, 0.5), _hour(NOW, 2.0)]
    totals.add_hours(hours, NOW)
    assert totals.add_hours(hours, NOW) is False
    assert (totals.home_kwh, totals.solar_kwh) == (3.0, 0.5)


def test_a_revised_hour_adds_growth_and_never_goes_down():
    totals = totals_mod.EnergyTotals()
    totals.add_hours([_hour(NOW, 1.0)], NOW)
    totals.add_hours([_hour(NOW, 1.4)], NOW)
    assert round(totals.home_kwh, 3) == 1.4
    assert totals.add_hours([_hour(NOW, 0.9)], NOW) is False
    assert round(totals.home_kwh, 3) == 1.4


def test_old_hours_are_dropped_from_the_counted_list():
    totals = totals_mod.EnergyTotals()
    totals.add_hours([_hour(NOW - 41 * 86400, 1.0), _hour(NOW, 1.0)], NOW)
    assert list(totals.counted) == [NOW]
    assert totals.home_kwh == 2.0


def test_store_round_trip_and_bad_data():
    totals = totals_mod.EnergyTotals()
    totals.add_hours([_hour(NOW, 1.25, 0.5)], NOW)
    restored = totals_mod.EnergyTotals.from_dict(totals.as_dict())
    assert (restored.home_kwh, restored.solar_kwh, restored.counted) == (1.25, 0.5, {NOW: (1.25, 0.5)})
    assert restored.add_hours([_hour(NOW, 1.25, 0.5)], NOW) is False
    for bad in (None, "x", {"counted": [["a", 1]]}):
        assert totals_mod.EnergyTotals.from_dict(bad).started is False


# --- gap repair from Base's daily totals ----------------------------------------

DAY = 86400
# A local midnight, three days before NOW.
D0 = NOW - 3 * DAY


def _day(start, home, solar=0.0):
    return {"start": start, "home_kwh": home, "solar_kwh": solar}


def _tracking(since):
    totals = totals_mod.EnergyTotals()
    totals.add_hours([_hour(since - 3600, 1.0)], since)
    return totals


def test_a_fully_counted_day_is_left_alone():
    totals = _tracking(D0 - DAY)
    totals.add_hours([_hour(D0 + hour * 3600, 1.0) for hour in range(24)], D0 + DAY)
    before = totals.home_kwh
    days = [_day(D0, 24.0001), _day(D0 + DAY, 5.0)]
    assert totals.reconcile_days(days, NOW) is False
    assert totals.home_kwh == before


def test_hours_missed_during_downtime_are_added_once():
    totals = _tracking(D0 - DAY)
    # Home Assistant was down for most of the day: only 4 of 24 hours arrived.
    totals.add_hours([_hour(D0 + hour * 3600, 1.0) for hour in range(4)], D0 + DAY)
    before = totals.home_kwh
    days = [_day(D0, 24.0, 6.0), _day(D0 + DAY, 5.0)]
    assert totals.reconcile_days(days, NOW) is True
    assert round(totals.home_kwh - before, 3) == 20.0
    assert round(totals.solar_kwh, 3) == 6.0
    # Asking again adds nothing, and the repair survives a restart.
    assert totals.reconcile_days(days, NOW) is False
    restored = totals_mod.EnergyTotals.from_dict(totals.as_dict())
    assert restored.reconcile_days(days, NOW) is False
    assert restored.home_kwh == totals.home_kwh


def test_a_day_base_revises_upward_adds_only_the_difference():
    totals = _tracking(D0 - DAY)
    days = [_day(D0, 10.0), _day(D0 + DAY, 5.0)]
    totals.reconcile_days(days, NOW)
    before = totals.home_kwh
    assert totals.reconcile_days([_day(D0, 12.5), _day(D0 + DAY, 5.0)], NOW) is True
    assert round(totals.home_kwh - before, 3) == 2.5
    # A lower figure later never takes energy back.
    assert totals.reconcile_days([_day(D0, 3.0), _day(D0 + DAY, 5.0)], NOW) is False


def test_days_before_counting_began_are_never_repaired():
    # Counting began in the middle of D0, so D0 was only partly seen on purpose.
    totals = _tracking(D0 + 12 * 3600)
    before = totals.home_kwh
    days = [_day(D0 - DAY, 30.0), _day(D0, 30.0), _day(D0 + DAY, 30.0), _day(D0 + 2 * DAY, 1.0)]
    assert totals.reconcile_days(days, NOW) is True
    # Only D0 + DAY, the first full day after counting began, is added.
    assert round(totals.home_kwh - before, 3) == 30.0


def test_today_and_a_day_that_just_ended_wait():
    totals = _tracking(D0 - DAY)
    yesterday = NOW - DAY - 3600
    today = NOW - 3600
    # Yesterday ended one hour ago. Base posts its last hour late, so it waits.
    assert totals.reconcile_days([_day(yesterday, 20.0), _day(today, 1.0)], NOW) is False
    # Six hours after it ended, it counts.
    assert totals.reconcile_days([_day(yesterday, 20.0), _day(today, 1.0)], today + 6 * 3600) is True


def test_a_skipped_day_or_tiny_gap_is_ignored():
    totals = _tracking(D0 - DAY)
    # Base's list jumps two days: the range is not one day, so it is not trusted.
    assert totals.reconcile_days([_day(D0, 20.0), _day(D0 + 2 * DAY, 1.0)], NOW) is False
    # Rounding-sized differences are not added.
    totals.add_hours([_hour(D0 + hour * 3600, 1.0) for hour in range(24)], D0 + DAY)
    assert totals.reconcile_days([_day(D0, 24.04), _day(D0 + DAY, 1.0)], NOW) is False


def test_a_daylight_saving_day_still_counts():
    totals = _tracking(D0 - DAY)
    assert totals.reconcile_days([_day(D0, 20.0), _day(D0 + 25 * 3600, 1.0)], NOW) is True


def test_nothing_is_repaired_before_the_first_hour():
    totals = totals_mod.EnergyTotals()
    assert totals.reconcile_days([_day(D0, 20.0), _day(D0 + DAY, 1.0)], NOW) is False
    assert totals.started is False


def test_a_store_from_before_gap_repair_starts_counting_days_from_now():
    old = {"home_kwh": 54.0, "solar_kwh": 0.0, "counted": [[NOW - 3600, 0.5, 0.0]]}
    totals = totals_mod.EnergyTotals.from_dict(old)
    assert totals.since is None
    # Before the first refresh after the update, nothing is repaired.
    assert totals.reconcile_days([_day(D0, 20.0), _day(D0 + DAY, 1.0)], NOW) is False
    assert totals.add_hours([], NOW) is True
    assert totals.since == NOW
    assert totals.home_kwh == 54.0
    # Days that began before the update stay as they were.
    assert totals.reconcile_days([_day(D0, 20.0), _day(D0 + DAY, 1.0)], NOW + 10 * DAY) is False


def test_counted_hours_are_kept_long_enough_to_check_a_day():
    totals = _tracking(NOW - 39 * DAY)
    totals.add_hours([_hour(NOW - 38 * DAY, 1.0)], NOW)
    assert NOW - 38 * DAY in totals.counted
