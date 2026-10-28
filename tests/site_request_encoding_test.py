"""Request bytes for the five site RPCs that were returning empty frames.

The integration package imports Home Assistant. These tests load the API
module directly so the request encoding can be checked without it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import struct
import sys
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "custom_components.base_power"


def _load_api():
    """Import api.py without executing the integration's Home Assistant setup."""
    if _PKG + ".api" in sys.modules:
        return sys.modules[_PKG + ".api"]
    components = types.ModuleType("custom_components")
    components.__path__ = [str(_ROOT / "custom_components")]
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / "custom_components" / "base_power")]
    package.__package__ = _PKG
    sys.modules["custom_components"] = components
    sys.modules[_PKG] = package

    def load(name: str, filename: str):
        spec = importlib.util.spec_from_file_location(
            name, _ROOT / "custom_components" / "base_power" / filename
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    load(_PKG + ".const", "const.py")
    return load(_PKG + ".api", "api.py")


_api = _load_api()
BasePowerApiClient = _api.BasePowerApiClient
build_site_payload = _api.build_site_payload
local_day_interval = _api.local_day_interval

ADDRESS = "loc-1"
ADDRESS_BYTES = bytes.fromhex("0a056c6f632d31")
# Field 1 varint 150, field 2 timestamp seconds=1, field 3 timestamp seconds=2.
NUMERIC_INTERVAL = bytes.fromhex("089601120208011a020802")
INTERVAL = (1, 2)

RECENT_OK = bytes.fromhex("0a0d" + "00" * 13)
ENERGY_OK = bytes.fromhex("0d0000803f")  # field 1 float32 = 1.0
BILLING_OK = bytes.fromhex("1064")  # field 2 varint = 100 cents
WIFI_OK = bytes.fromhex("0a030a0178")  # field 1 {ssid "x"}
CYCLES_OK = bytes.fromhex("120161")  # field 2 string "a"

RPCS = (
    ("get_recent_usage", "MobileGetRecentUsage", RECENT_OK, False),
    ("get_usage_energy", "MobileGetUsageEnergy", ENERGY_OK, True),
    ("get_billing_metadata", "MobileGetBillingMetadata", BILLING_OK, True),
    ("get_wifi_metrics", "MobileGetWifiMetrics", WIFI_OK, True),
    ("get_usage_cycles", "MobileGetUsageCycles", CYCLES_OK, True),
)


class RecordingClient(BasePowerApiClient):
    """Capture protobuf bodies without opening a socket."""

    def __init__(self, responses: list) -> None:
        super().__init__(session=None)
        self._responses = list(responses)
        self.calls: list[tuple[str, bytes, bool]] = []

    async def _call(self, method: str, payload: bytes = b"", *, optional: bool = False) -> bytes:
        self.calls.append((method, bytes(payload), optional))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _run(coro):
    return asyncio.run(coro)


def test_numeric_interval_bytes_are_stable():
    """Lock the protobuf layout: int32 id, then two timestamp messages."""
    payload, mode = build_site_payload(ADDRESS, 150, INTERVAL)
    assert mode == "numeric"
    assert payload == NUMERIC_INTERVAL


def test_address_fallback_omits_the_interval():
    """The previous request is field 1 as the address string and nothing else."""
    for numeric in (None, 0, False):
        payload, mode = build_site_payload(ADDRESS, numeric, INTERVAL)
        assert mode == "address"
        assert payload == ADDRESS_BYTES


def test_inverted_interval_sends_the_id_only():
    payload, mode = build_site_payload(ADDRESS, 150, (5, 5))
    assert mode == "numeric"
    assert payload == bytes.fromhex("089601")


def test_local_day_interval_uses_the_zone_and_does_not_crash():
    zone = ZoneInfo("America/Chicago")
    now = datetime(2026, 10, 28, 15, 30, tzinfo=zone)
    start, end = local_day_interval("America/Chicago", now)
    assert datetime.fromtimestamp(start, zone) == datetime(2026, 10, 28, tzinfo=zone)
    assert datetime.fromtimestamp(end, zone) == datetime(2026, 10, 29, tzinfo=zone)
    assert end - start == 86400

    start, end = local_day_interval("Not/AZone", datetime(2026, 1, 2, 3, tzinfo=ZoneInfo("UTC")))
    assert datetime.fromtimestamp(start, ZoneInfo("UTC")) == datetime(2026, 1, 2, tzinfo=ZoneInfo("UTC"))
    assert end - start == 86400


def test_each_rpc_sends_numeric_id_and_interval():
    async def check(method_name: str, rpc: str, body: bytes, optional: bool) -> None:
        client = RecordingClient([body])
        result = await getattr(client, method_name)(ADDRESS, 150, INTERVAL)
        assert client.calls == [(rpc, NUMERIC_INTERVAL, True)]
        assert result

    for method_name, rpc, body, optional in RPCS:
        _run(check(method_name, rpc, body, optional))


def test_each_rpc_falls_back_when_numeric_id_is_missing(caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.base_power.api")

    async def check(method_name: str, rpc: str, body: bytes, optional: bool) -> None:
        client = RecordingClient([body])
        result = await getattr(client, method_name)(ADDRESS, None, INTERVAL)
        assert client.calls == [(rpc, ADDRESS_BYTES, optional)]
        assert result

    for method_name, rpc, body, optional in RPCS:
        _run(check(method_name, rpc, body, optional))
    assert "numeric service location id unavailable; falling back to address id" in caplog.text


def test_each_rpc_falls_back_when_numeric_frame_is_empty():
    async def check(method_name: str, rpc: str, body: bytes, optional: bool) -> None:
        client = RecordingClient([b"", body])
        result = await getattr(client, method_name)(ADDRESS, 150, INTERVAL)
        assert [call[0] for call in client.calls] == [rpc, rpc]
        assert client.calls[0][1] == NUMERIC_INTERVAL
        assert client.calls[0][2] is True
        assert client.calls[1] == (rpc, ADDRESS_BYTES, optional)
        assert result

    for method_name, rpc, body, optional in RPCS:
        _run(check(method_name, rpc, body, optional))


def test_numeric_transport_error_falls_back_instead_of_raising():
    async def check() -> None:
        client = RecordingClient([ConnectionError("down"), CYCLES_OK])
        result = await client.get_usage_cycles(ADDRESS, 150, INTERVAL)
        assert result["asset_id"] == "a"
        assert client.calls[1][1] == ADDRESS_BYTES

    _run(check())


def test_usage_energy_reads_doubles_and_keeps_zero():
    payload = b"".join(
        bytes([((index + 1) << 3) | 1]) + struct.pack("<d", value)
        for index, value in enumerate((1.5, 0.0, 2.25))
    )
    parsed = BasePowerApiClient._parse_usage_energy(payload)
    assert parsed == {
        "grid_to_home_kwh": 1.5,
        "solar_to_home_kwh": 0.0,
        "battery_to_home_kwh": 2.25,
    }

    only_nan = bytes([0x09]) + struct.pack("<d", float("nan"))
    assert BasePowerApiClient._parse_usage_energy(only_nan) == {
        "grid_to_home_kwh": None,
        "solar_to_home_kwh": None,
        "battery_to_home_kwh": None,
    }
    assert BasePowerApiClient._parse_usage_energy(b"")["grid_to_home_kwh"] is None


def test_zero_grid_energy_is_data_and_does_not_fall_back():
    """A real zero must not be replaced by the address-string response."""
    zero = bytes.fromhex("0d00000000")  # field 1 float32 = 0

    async def check() -> None:
        client = RecordingClient([zero, ENERGY_OK])
        parsed = await client.get_usage_energy(ADDRESS, 150, INTERVAL)
        assert len(client.calls) == 1
        assert parsed["grid_to_home_kwh"] == 0.0
        assert parsed["solar_to_home_kwh"] is None

    _run(check())


def test_debug_log_names_the_id_interval_and_payload(caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.base_power.api")

    async def check() -> None:
        client = RecordingClient([ENERGY_OK])
        await client.get_usage_energy(ADDRESS, 49652, (1_700_000_000, 1_700_086_400))

    _run(check())
    text = caplog.text
    assert "MobileGetUsageEnergy request using=numeric" in text
    assert "numeric_service_location_id=49652" in text
    assert "interval_start=1700000000" in text
    assert "interval_end=1700086400" in text
    payload, mode = build_site_payload(ADDRESS, 49652, (1_700_000_000, 1_700_086_400))
    assert mode == "numeric"
    assert payload.hex() in text
