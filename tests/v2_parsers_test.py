"""dashboard.mobile.v2 parsers: battery snapshot, daily overview, billing cycle.

Messages are built here with the field numbers from Base's gRPC reflection.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "custom_components.base_power"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, _ROOT / "custom_components" / "base_power" / filename
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_v2():
    if _PKG + ".api_v2" in sys.modules:
        return sys.modules[_PKG + ".api_v2"]
    if _PKG + ".api" not in sys.modules:
        components = sys.modules.get("custom_components")
        if components is None:
            components = types.ModuleType("custom_components")
            components.__path__ = [str(_ROOT / "custom_components")]
            sys.modules["custom_components"] = components
        if _PKG not in sys.modules:
            package = types.ModuleType(_PKG)
            package.__path__ = [str(_ROOT / "custom_components" / "base_power")]
            sys.modules[_PKG] = package
        _load(_PKG + ".const", "const.py")
        _load(_PKG + ".api", "api.py")
    return _load(_PKG + ".api_v2", "api_v2.py")


v2 = _load_v2()


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def msg(field: int, body: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(body)) + body


def dbl(field: int, value: float) -> bytes:
    return _varint(field << 3 | 1) + struct.pack("<d", value)


def num(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def ts(field: int, seconds: int) -> bytes:
    return msg(field, num(1, seconds))


def snapshot(state_field: int, body: bytes) -> bytes:
    return msg(1, msg(1, msg(2, b"wifi")) + msg(state_field, body))


# --- battery snapshot ---------------------------------------------------------


def test_on_grid_snapshot_reads_flow_and_backup_and_fills_zeros():
    flow = dbl(1, 0.9) + dbl(4, 0.9) + dbl(5, 0.9)
    parsed = v2.parse_battery_snapshot(
        snapshot(3, ts(1, 1_800_000_000) + msg(2, flow) + dbl(3, 25.61) + dbl(4, 30.0))
    )
    assert parsed["available"] is True
    assert parsed["state"] == "on_grid"
    assert parsed["observed_at"] == 1_800_000_000
    assert parsed["backup_hours"] == 25.61
    assert parsed["backup_hours_750w"] == 30.0
    assert parsed["power_flow"]["from_grid_kw"] == 0.9
    assert parsed["power_flow"]["from_storage_kw"] == 0.0
    assert parsed["state_of_energy_percent"] is None
    assert parsed["overcurrent_limit_kw"] is None


def test_outage_snapshot_reads_battery_percent():
    flow = dbl(2, 1.4) + dbl(5, 1.4)
    parsed = v2.parse_battery_snapshot(
        snapshot(4, ts(1, 1) + num(2, 87) + msg(3, flow) + dbl(4, 12.5) + dbl(5, 20.0))
    )
    assert parsed["state"] == "outage"
    assert parsed["state_of_energy_percent"] == 87
    assert parsed["power_flow"]["from_storage_kw"] == 1.4
    assert parsed["backup_hours"] == 12.5
    assert parsed["backup_hours_750w"] == 20.0


def test_overcurrent_states_read_the_limit_at_their_own_field():
    running = v2.parse_battery_snapshot(snapshot(6, num(2, 50) + dbl(6, 9.6)))
    standby = v2.parse_battery_snapshot(snapshot(7, num(2, 40) + dbl(5, 9.6)))
    assert running["state"] == "overcurrent"
    assert running["overcurrent_limit_kw"] == 9.6
    assert standby["state"] == "overcurrent_standby"
    assert standby["overcurrent_limit_kw"] == 9.6
    assert standby["power_flow"] is None


def test_no_home_power_and_unavailable_telemetry():
    no_power = v2.parse_battery_snapshot(snapshot(5, num(2, 0)))
    assert no_power["state"] == "no_home_power"
    assert no_power["state_of_energy_percent"] == 0
    unavailable = v2.parse_battery_snapshot(snapshot(2, b""))
    assert unavailable["state"] == "telemetry_unavailable"
    assert unavailable["backup_hours"] is None


def test_empty_snapshot_is_unavailable():
    for body in (b"", msg(1, msg(1, msg(2, b"wifi")))):
        parsed = v2.parse_battery_snapshot(body)
        assert parsed["available"] is False
        assert parsed["state"] is None


def test_off_grid_states():
    assert "on_grid" not in v2.OFF_GRID_STATES
    assert "telemetry_unavailable" not in v2.OFF_GRID_STATES
    assert v2.OFF_GRID_STATES == {"outage", "no_home_power", "overcurrent", "overcurrent_standby"}


# --- daily overview -----------------------------------------------------------

DAY_START = 1_800_000_000
DAY_END = DAY_START + 86_400


def test_overview_mix_fills_missing_sources_with_zero():
    parsed = v2.parse_daily_overview(msg(7, dbl(1, 32.052)), DAY_START, DAY_END)
    assert parsed["available"] is True
    assert parsed["energy_mix_24h"] == {
        "grid_to_home_kwh": 32.052,
        "solar_to_home_kwh": 0.0,
        "battery_to_home_kwh": 0.0,
    }


def test_overview_without_mix_leaves_it_unknown():
    parsed = v2.parse_daily_overview(msg(1, ts(1, DAY_START)), DAY_START, DAY_END)
    assert parsed["available"] is True
    assert parsed["energy_mix_24h"] is None
    assert v2.parse_daily_overview(b"", DAY_START, DAY_END)["available"] is False


def test_overview_cost_today_sums_only_the_local_day():
    hour = lambda start, cents: msg(5, ts(1, start) + dbl(2, cents))  # noqa: E731
    body = hour(DAY_START - 3600, 50.0) + hour(DAY_START, 12.5) + hour(DAY_START + 3600, 7.25)
    body += msg(6, b"USD")
    parsed = v2.parse_daily_overview(body, DAY_START, DAY_END)
    assert parsed["cost_today_cents"] == 19.75
    assert parsed["cost_currency"] == "USD"


def test_overview_without_cost_samples_today_is_unknown():
    body = msg(5, ts(1, DAY_START - 3600) + dbl(2, 50.0))
    assert v2.parse_daily_overview(body, DAY_START, DAY_END)["cost_today_cents"] is None


def test_grid_support_intervals_and_active_window():
    body = msg(3, ts(1, 100) + ts(2, 200)) + msg(3, ts(1, 500))
    parsed = v2.parse_daily_overview(body, DAY_START, DAY_END)
    assert parsed["grid_support"] == [{"start": 100, "end": 200}, {"start": 500, "end": None}]
    intervals = parsed["grid_support"]
    assert v2.grid_support_active(intervals, 150) is True
    assert v2.grid_support_active(intervals, 200) is False
    assert v2.grid_support_active(intervals, 300) is False
    assert v2.grid_support_active(intervals, 600) is True
    assert v2.grid_support_active([], 600) is False


# --- billing cycle -------------------------------------------------------------


def test_billing_cycle_amounts_and_negative_credit():
    cycle = (
        msg(1, b"USD") + ts(2, 1000) + ts(3, 1000) + ts(4, 2000)
        + num(5, 15_000) + num(6, -1_250) + num(7, 13_750) + num(8, 21_000)
    )
    parsed = v2.parse_current_billing_cycle(msg(1, cycle))
    assert parsed == {
        "currency": "USD",
        "service_period_start": 1000,
        "data_through": 2000,
        "gross_charges_cents": 15_000,
        "solar_credit_cents": -1_250,
        "net_accrued_cents": 13_750,
        "projected_charges_cents": 21_000,
    }


def test_billing_cycle_zero_amounts_are_zero_and_empty_reply_is_unknown():
    parsed = v2.parse_current_billing_cycle(msg(1, msg(1, b"USD")))
    assert parsed["net_accrued_cents"] == 0
    assert parsed["solar_credit_cents"] == 0
    assert v2.parse_current_billing_cycle(b"")["net_accrued_cents"] is None


def test_request_encoding():
    assert v2.encode_address_request("ABC").hex() == "0a03414243"
    # AccountReference {1: "ABC", 2: ACCOUNT_TYPE_ENERGY}
    assert v2.encode_billing_cycle_request("ABC").hex() == "0a070a034142431001"


def test_overview_home_power_matches_recent_usage_shape():
    body = msg(1, ts(1, DAY_START + 900) + dbl(2, 0.9565)) + msg(1, ts(1, DAY_START) + dbl(2, 0.8081))
    parsed = v2.parse_daily_overview(body, DAY_START, DAY_END)
    assert parsed["home_power"] == [
        {"timestamp": DAY_START, "kwh": 0.202, "watts": 808},
        {"timestamp": DAY_START + 900, "kwh": 0.239, "watts": 956},
    ]


def test_overview_energy_today_sums_hourly_samples_in_the_local_day():
    hour = lambda start, home, solar=None: msg(  # noqa: E731
        4, ts(1, start) + dbl(2, home) + (dbl(3, solar) if solar is not None else b"")
    )
    body = hour(DAY_START - 3600, 9.0) + hour(DAY_START, 0.9109) + hour(DAY_START + 3600, 0.7781, 0.25)
    parsed = v2.parse_daily_overview(body, DAY_START, DAY_END)
    assert parsed["energy_today"] == {
        "grid_to_home_kwh": 1.689,
        "solar_to_home_kwh": 0.25,
        "battery_to_home_kwh": None,
    }


def test_overview_without_hourly_samples_today_has_no_energy_today():
    body = msg(4, ts(1, DAY_START - 3600) + dbl(2, 9.0))
    assert v2.parse_daily_overview(body, DAY_START, DAY_END)["energy_today"] is None


def test_recent_grid_voltage_uses_the_latest_sample():
    body = msg(1, ts(1, 200) + dbl(2, 244.8)) + msg(1, ts(1, 100) + dbl(2, 240.0))
    parsed = v2.parse_recent_grid_voltage(body)
    assert parsed == {"available": True, "has_gateway": None, "voltage_volts": 244.8, "voltage_samples": 2}
    assert v2.parse_recent_grid_voltage(b"")["voltage_volts"] is None
    # A sample at 0 V is a real reading (proto3 leaves the field out).
    assert v2.parse_recent_grid_voltage(msg(1, ts(1, 300)))["voltage_volts"] == 0.0
