"""Base retired most of the older DashboardAPI on 2026-11-24 (issues #29, #30, #31).

These cover the calls that replace it, the battery percent estimate, and how
an "unknown method" reply is handled.
"""

from __future__ import annotations

import struct

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.base_power import api_v2
from custom_components.base_power.api import BasePowerApiClient
from custom_components.base_power.capacity import estimate_battery_percent

HOST = "https://dashboard.baseapis.net"
LEGACY = f"{HOST}/dashboard.DashboardAPI"
V2_BATTERY = f"{HOST}/dashboard.mobile.v2.BatteryService"


# --- protobuf builders ---------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def _int(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _msg(field: int, body: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(body)) + body


def _str(field: int, text: str) -> bytes:
    return _msg(field, text.encode())


def _double(field: int, value: float) -> bytes:
    return _varint(field << 3 | 1) + struct.pack("<d", value)


def _stamp(field: int, seconds: int) -> bytes:
    return _msg(field, _int(1, seconds))


def _frame(flag: int, payload: bytes) -> bytes:
    return bytes([flag]) + struct.pack(">I", len(payload)) + payload


def _grpc(message: bytes = b"", status: str = "0", text: str = "") -> bytes:
    trailer = f"grpc-status: {status}\r\n"
    if text:
        trailer += f"grpc-message: {text}\r\n"
    return _frame(0, message) + _frame(0x80, trailer.encode())


# --- GetDashboardRoot -----------------------------------------------------------


def _dashboard_root(numeric_id: int = 4321, solar: bool = False) -> bytes:
    service_location = _int(1, numeric_id) + (_int(6, 1) if solar else b"")
    address = _str(2, "1 Main St") + _str(9, "America/Chicago")
    battery = _int(1, 1) + _int(2, 3) + _stamp(5, 1_800_000_000)
    root = _msg(1, service_location) + _msg(5, address) + _msg(10, battery) + _int(12, 1)
    return _msg(1, root)


def test_get_dashboard_root_reads_the_nested_numeric_id():
    parsed = BasePowerApiClient._parse_get_dashboard_root(_dashboard_root())
    assert parsed == {
        "service_location_id": 4321,
        "battery_status": 3,
        "has_solar": False,
        "time_zone": "America/Chicago",
        "upcoming_service_at": 1_800_000_000,
    }


def test_get_dashboard_root_solar_flag_and_empty_reply():
    assert BasePowerApiClient._parse_get_dashboard_root(_dashboard_root(solar=True))["has_solar"]
    empty = BasePowerApiClient._parse_get_dashboard_root(b"")
    assert empty["service_location_id"] is None
    assert empty["time_zone"] is None


# --- GetServiceStatus -----------------------------------------------------------


def test_service_status_gives_voltage_outage_and_gateway():
    # Captured shape: gridVoltage 246, hasGateway, gatewayConnection,
    # stateOfEnergy 5 bars, gatewayWifi with only the ssid (state 0 = connected).
    body = _int(1, 246) + _int(3, 1) + _int(4, 1) + _int(5, 5) + _msg(6, _str(1, "HomeNet")) + _int(10, 247)
    parsed = BasePowerApiClient._parse_service_status(body)
    assert parsed["available"] is True
    assert parsed["voltage_volts"] == 246.0
    assert parsed["has_gateway"] is True
    assert parsed["gateway_connected"] is True
    assert parsed["is_active_outage"] is False
    assert parsed["energy_bars"] == 5
    assert parsed["wifi_ssid"] == "HomeNet"
    assert parsed["wifi_state"] == "connected"
    # Bars are not a percent; the percent comes from elsewhere.
    assert parsed["state_of_energy_raw"] is None


def test_service_status_outage_and_missing_voltage():
    parsed = BasePowerApiClient._parse_service_status(_int(3, 1) + _int(9, 1))
    assert parsed["is_active_outage"] is True
    # No voltage field is unknown, not 0 V.
    assert parsed["voltage_volts"] is None
    assert BasePowerApiClient._parse_service_status(b"")["available"] is False


# --- snapshot Wi-Fi and the overview backup series -----------------------------


def test_snapshot_reads_the_wifi_block():
    wifi = _int(1, 4) + _str(2, "HomeNet") + _stamp(3, 1_790_000_000)
    on_grid = _stamp(1, 1_800_000_000) + _double(3, 25.2) + _double(4, 30.0)
    parsed = api_v2.parse_battery_snapshot(_msg(1, _msg(1, wifi) + _msg(3, on_grid)))
    assert parsed["state"] == "on_grid"
    assert parsed["wifi_ssid"] == "HomeNet"
    assert parsed["wifi_state"] == "connected"
    assert parsed["backup_hours_750w"] == 30.0
    # On grid the snapshot has no battery percent.
    assert parsed["state_of_energy_percent"] is None


def _backup_sample(start: int, current: float, at_750w: float) -> bytes:
    return _msg(2, _stamp(1, start) + _double(2, current) + _double(3, at_750w))


def test_overview_keeps_the_newest_backup_sample():
    body = (
        _backup_sample(1_800_000_900, 20.2, 45.9)
        + _backup_sample(1_800_001_800, 17.3, 45.7)
        + _backup_sample(1_800_000_000, 22.6, 46.0)
    )
    parsed = api_v2.parse_daily_overview(body, 0, 2_000_000_000)
    assert parsed["backup_latest"] == {
        "timestamp": 1_800_001_800,
        "backup_hours": 17.3,
        "backup_hours_750w": 45.7,
    }
    assert api_v2.parse_daily_overview(b"", 0, 1)["backup_latest"] is None


def test_snapshot_fills_only_what_the_context_is_missing():
    snapshot = {
        **api_v2.empty_snapshot(),
        "available": True,
        "state": "outage",
        "state_of_energy_percent": 63,
        "backup_hours": 12.0,
        "backup_hours_750w": 30.0,
        "power_flow": {"from_grid_kw": 0.0, "from_storage_kw": 1.2, "from_solar_kw": 0.0,
                       "non_solar_to_home_kw": 1.2, "to_home_kw": 1.2},
        "wifi_ssid": "HomeNet",
        "wifi_state": "connected",
    }
    context = {**BasePowerApiClient._parse_service_status(b""), "wifi_ssid": "FromStatus"}
    filled = api_v2.fill_context_from_snapshot(context, snapshot)
    assert filled["available"] is True
    assert filled["power_from_storage_kw"] == 1.2
    assert filled["power_to_home_kw"] == 1.2
    assert filled["available_backup_hours"] == 12.0
    assert filled["state_of_energy_raw"] == 63
    assert filled["home_has_power"] is True
    assert filled["wifi_ssid"] == "FromStatus"
    # A failed snapshot changes nothing.
    assert api_v2.fill_context_from_snapshot(context, api_v2.empty_snapshot()) is context


# --- battery percent from the 750 W backup time ---------------------------------


@pytest.mark.parametrize(
    ("hours", "capacity", "percent"),
    [
        # Issue #29: a Base Core compared against the retired percent.
        (3.66, 39.2, 17.0),
        (23.52, 39.2, 55.0),
        (47.04, 39.2, 100.0),
        # A full GRO_SINGLE reads 30 h.
        (30.0, 25, 100.0),
        (0.0, 25, 10.0),
    ],
)
def test_battery_percent_estimate_matches_the_retired_value(hours, capacity, percent):
    assert estimate_battery_percent(hours, capacity) == pytest.approx(percent, abs=0.1)


@pytest.mark.parametrize(
    ("hours", "capacity"),
    [(None, 25), (30.0, None), (30.0, 0), (-1.0, 25), (float("nan"), 25), (True, 25)],
)
def test_battery_percent_estimate_needs_good_numbers(hours, capacity):
    assert estimate_battery_percent(hours, capacity) is None


def test_battery_percent_estimate_never_passes_full():
    # The snapshot reads about 4% high, and the capacity setting can be wrong.
    assert estimate_battery_percent(48.2, 39.2) == 100.0
    assert estimate_battery_percent(47.04, 25) == 100.0


# --- retired methods ------------------------------------------------------------


async def test_a_retired_method_warns_once_and_is_not_called_again(hass, aioclient_mock, caplog):
    aioclient_mock.post(
        f"{LEGACY}/MobileGetWifiMetrics",
        content=_grpc(status="12", text="unknown method MobileGetWifiMetrics"),
    )
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    with caplog.at_level("WARNING"):
        first = await client.get_wifi_metrics("loc-1")
        second = await client.get_wifi_metrics("loc-1")
    assert first["scan"] == {} and second["scan"] == {}
    assert aioclient_mock.call_count == 1
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "MobileGetWifiMetrics" in warnings[0].getMessage()


async def test_dashboard_root_uses_the_new_call(hass, aioclient_mock):
    aioclient_mock.post(f"{LEGACY}/GetDashboardRoot", content=_grpc(_dashboard_root(99)))
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    parsed = await client.get_dashboard_root("loc-1")
    assert parsed["service_location_id"] == 99
    assert aioclient_mock.call_count == 1


async def test_dashboard_root_falls_back_to_the_older_call(hass, aioclient_mock):
    aioclient_mock.post(f"{LEGACY}/GetDashboardRoot", content=_grpc(status="12"))
    aioclient_mock.post(f"{LEGACY}/MobileGetDashboardRoot", content=_grpc(_int(7, 55)))
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    assert (await client.get_dashboard_root("loc-1"))["service_location_id"] == 55


async def test_wifi_scan_uses_the_v2_call(hass, aioclient_mock):
    networks = _msg(1, _str(1, "HomeNet") + _int(2, 68)) + _msg(1, _str(1, "Other") + _int(2, 30))
    aioclient_mock.post(f"{V2_BATTERY}/ListWifiNetworks", content=_grpc(networks))
    client = BasePowerApiClient(async_get_clientsession(hass))
    client.set_jwt("jwt")
    wifi = await client.get_battery_wifi_networks("loc-1")
    assert wifi["scan"] == {"HomeNet": 68, "Other": 30}
    assert aioclient_mock.call_count == 1


async def test_discovery_uses_get_available_locations(hass, aioclient_mock):
    aioclient_mock.post(f"{LEGACY}/GetAvailableLocations", content=_grpc(_msg(1, _str(1, "addr-9"))))
    found = await BasePowerApiClient.async_discover_location_id(async_get_clientsession(hass), "jwt")
    assert found == "addr-9"
    assert aioclient_mock.call_count == 1
