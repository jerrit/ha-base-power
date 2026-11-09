"""Running energy totals: every hour counted once, never going down."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "custom_components.base_power"


def _load_totals():
    for name, path in (("custom_components", "custom_components"), (_PKG, "custom_components/base_power")):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(_ROOT / path)]
            sys.modules[name] = module
    for name in ("const", "totals"):
        full = f"{_PKG}.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, _ROOT / "custom_components" / "base_power" / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
    return sys.modules[f"{_PKG}.totals"]


totals_mod = _load_totals()
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
    totals.add_hours([_hour(NOW - 72 * 3600, 1.0), _hour(NOW, 1.0)], NOW)
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
