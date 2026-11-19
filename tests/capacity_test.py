"""Battery size from Base's backup time and from GetHome's nameplate."""

from __future__ import annotations

import struct

import pytest

from custom_components.base_power import api_v2
from custom_components.base_power.capacity import CapacityEstimate, nearest_system
from custom_components.base_power.const import battery_variant_count


def _estimate(*readings):
    estimate = CapacityEstimate()
    for soc, hours in readings:
        estimate.add(soc, hours)
    return estimate


def test_measured_gro_single_comes_out_at_25_kwh():
    # Real readings from a GRO_SINGLE: 30.0 h at 100%, 29.7 h at 99%.
    estimate = _estimate((100, 30.0), (99, 29.7), (100, 30.0))
    assert estimate.usable_kwh == 22.5
    assert estimate.system() == (1, 25)
    assert estimate.system(1) == (1, 25)


@pytest.mark.parametrize(
    ("units", "nameplate"), [(1, 20), (1, 25), (1, 39.2), (2, 50), (2, 78.4)]
)
def test_every_known_size_is_recognised_at_any_charge(units, nameplate):
    usable = nameplate * 0.9
    for soc in (50, 75, 100):
        hours = usable * soc / 100 / 0.75
        assert _estimate(*[(soc, hours)] * 3).system(units) == (units, nameplate)


def test_needs_a_few_readings_at_half_charge_or_more():
    assert _estimate((100, 30.0), (100, 30.0)).usable_kwh is None
    assert _estimate((49, 14.7), (40, 12.0), (10, 3.0)).samples == 0
    for bad in ((None, 30.0), (100, None), (100, 0), (100, float("nan")), (True, 30.0), (101, 30.0)):
        assert not CapacityEstimate().add(*bad)


def test_median_ignores_a_stray_reading():
    estimate = _estimate((100, 30.0), (100, 30.0), (100, 60.0), (100, 30.0), (100, 1.0))
    assert estimate.usable_kwh == 22.5


def test_keeps_only_the_last_hour():
    estimate = _estimate(*[(100, 47.04)] * 60, *[(100, 30.0)] * 61)
    assert estimate.samples == 60
    assert estimate.system() == (1, 25)


def test_a_reading_between_sizes_is_not_used():
    assert nearest_system(30) is None  # 20% from 25, 23% from 39.2
    assert nearest_system(60) is None  # a capped dual Core reading
    assert nearest_system(43, units=1) == (1, 39.2)
    assert nearest_system(43, units=2) is None
    assert nearest_system(0) is None


def test_diagnostics_summary():
    assert CapacityEstimate().as_diagnostics() == {
        "samples": 0, "usable_kwh": None, "min_kwh": None, "max_kwh": None
    }
    summary = _estimate((100, 30.0), (99, 29.7), (100, 30.4)).as_diagnostics()
    assert summary == {"samples": 3, "usable_kwh": 22.5, "min_kwh": 22.5, "max_kwh": 22.8}


def test_count_from_battery_types():
    assert battery_variant_count([{"variant": "SINGLE"}]) == 1
    assert battery_variant_count([{"variant": "DUAL"}]) == 2
    assert battery_variant_count([{"variant": "SINGLE"}, {"variant": "WALL"}]) == 2
    assert battery_variant_count([{"variant": None}]) is None
    assert battery_variant_count([]) is None


# --- GetHome ------------------------------------------------------------------------


def _ld(field: int, body: bytes) -> bytes:
    return bytes([field << 3 | 2, len(body)]) + body


def _double(field: int, value: float) -> bytes:
    return bytes([field << 3 | 1]) + struct.pack("<d", value)


def test_get_home_reads_the_product_nameplate():
    system = _double(1, 39200.0) + _double(2, 11500.0)
    product = _ld(1, b"Base Core") + _ld(3, b"cfg-1") + _ld(9, system)
    battery = b"\x08\x04" + b"\x10\x01" + _ld(8, product)
    reply = _ld(1, b"America/Chicago") + _ld(3, battery) + b"\x20\x01"
    assert api_v2.parse_home(reply) == {
        "available": True,
        "product_name": "Base Core",
        "nameplate_capacity_kwh": 39.2,
        "nameplate_discharge_kw": 11.5,
    }


def test_get_home_without_a_product_has_no_nameplate():
    # What our own account returns: a battery block with no product.
    reply = _ld(1, b"America/Chicago") + _ld(3, b"\x08\x04\x10\x01")
    assert api_v2.parse_home(reply) == {**api_v2.empty_home(), "available": True}
    zero = _ld(3, _ld(8, _ld(9, _double(1, 0.0))))
    assert api_v2.parse_home(zero)["nameplate_capacity_kwh"] is None


def test_empty_get_home_keeps_the_last_good_reply():
    from custom_components.base_power.poll import _keep

    assert api_v2.parse_home(b"") == api_v2.empty_home()
    previous = {**api_v2.empty_home(), "available": True, "nameplate_capacity_kwh": 39.2}
    assert _keep(previous, api_v2.parse_home(b"")) is previous
