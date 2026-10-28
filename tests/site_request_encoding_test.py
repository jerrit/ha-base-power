"""Request bytes and parsers checked against a live Base Power account.

Fixtures are synthetic. They follow the observed field layout and do not
contain a real address, account id, or captured payload.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import struct
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


def _load_api():
    if _PKG + ".api" in sys.modules:
        return sys.modules[_PKG + ".api"]
    components = types.ModuleType("custom_components")
    components.__path__ = [str(_ROOT / "custom_components")]
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_ROOT / "custom_components" / "base_power")]
    package.__package__ = _PKG
    sys.modules["custom_components"] = components
    sys.modules[_PKG] = package
    _load(_PKG + ".const", "const.py")
    return _load(_PKG + ".api", "api.py")


_api = _load_api()
_auth = _load(_PKG + ".auth", "auth.py")
BasePowerApiClient = _api.BasePowerApiClient
local_day_interval = _api.local_day_interval
summarize_recent_usage = _api.summarize_recent_usage

ADDRESS = "loc-1"
ADDRESS_BYTES = bytes.fromhex("0a056c6f632d31")
NUMERIC_BYTES = bytes.fromhex("089601")  # field 1 varint 150, nothing else
ZONE = "America/Chicago"
# 2026-10-28 12:00 in Chicago is after local midnight (05:00 UTC).
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo(ZONE))
HOUR_BEFORE = int(datetime(2026, 10, 28, 4, 0, tzinfo=UTC).timestamp())
HOUR_MIDNIGHT = int(datetime(2026, 10, 28, 5, 0, tzinfo=UTC).timestamp())
HOUR_LATER = int(datetime(2026, 10, 28, 6, 0, tzinfo=UTC).timestamp())


class RecordingClient(BasePowerApiClient):
    """Capture protobuf bodies without opening a socket."""

    def __init__(self, responses: list) -> None:
        super().__init__(session=None)
        self._responses = list(responses)
        self.calls: list[tuple[str, bytes, bool]] = []

    async def _invoke(self, method: str, payload: bytes = b"", *, optional: bool = False):
        self.calls.append((method, bytes(payload), optional))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            return item
        return item, True


def _run(coro):
    return asyncio.run(coro)


def _len_field(field_number: int, payload: bytes) -> bytes:
    tag = (field_number << 3) | 2
    return _api._encode_varint(tag) + _api._encode_varint(len(payload)) + payload


def _timestamp(field_number: int, seconds: int) -> bytes:
    return _len_field(field_number, b"\x08" + _api._encode_varint(seconds))


def _packed(field_number: int, values: list[float]) -> bytes:
    return _len_field(field_number, b"".join(struct.pack("<f", value) for value in values))


def _recent_point(timestamp: int, kw: float) -> bytes:
    encoded = _api._encode_varint(timestamp)
    assert len(encoded) == 5
    body = b"\x0a\x06\x08" + encoded + b"\x15" + struct.pack("<f", kw)
    assert len(body) == 13
    return b"\x0a\x0d" + body


def _energy_body(grid: list[float], solar: list[float], stamps: list[int]) -> bytes:
    parts = [b"\x18\x01"]  # field 3 varint, ignored
    parts.append(_len_field(1, b"2026-01-01 00:00:00"))
    parts.append(_packed(2, grid))
    parts.append(_packed(4, solar))
    parts.extend(_timestamp(5, stamp) for stamp in stamps)
    return b"".join(parts)


def test_usage_energy_sends_only_the_numeric_id():
    body = _energy_body([1.5, 2.5], [0.0, 0.0], [HOUR_MIDNIGHT, HOUR_LATER])

    async def check() -> None:
        client = RecordingClient([body])
        parsed = await client.get_usage_energy(ADDRESS, 150, ZONE, NOW)
        assert client.calls == [("MobileGetUsageEnergy", NUMERIC_BYTES, True)]
        assert parsed["grid_to_home_kwh"] == 4.0
        assert parsed["solar_to_home_kwh"] == 0.0
        assert parsed["battery_to_home_kwh"] is None

    _run(check())


def test_other_rpcs_send_the_address_string_once():
    recent = _recent_point(HOUR_LATER, 1.0)
    cases = (
        ("get_recent_usage", "MobileGetRecentUsage", recent, False),
        ("get_billing_metadata", "MobileGetBillingMetadata", b"\x10\x64", True),
        ("get_wifi_metrics", "MobileGetWifiMetrics", bytes.fromhex("0a030a0178"), True),
        ("get_usage_cycles", "MobileGetUsageCycles", b"\x12\x07asset-1", True),
    )

    async def check(method_name: str, rpc: str, body: bytes, optional: bool) -> None:
        client = RecordingClient([body])
        await getattr(client, method_name)(ADDRESS)
        assert client.calls == [(rpc, ADDRESS_BYTES, optional)]

    for method_name, rpc, body, optional in cases:
        _run(check(method_name, rpc, body, optional))


def test_usage_energy_falls_back_when_numeric_id_is_missing(caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.base_power.api")
    body = _energy_body([1.0], [0.0], [HOUR_LATER])

    async def check(numeric) -> None:
        client = RecordingClient([body])
        parsed = await client.get_usage_energy(ADDRESS, numeric, ZONE, NOW)
        assert client.calls == [("MobileGetUsageEnergy", ADDRESS_BYTES, True)]
        assert parsed["grid_to_home_kwh"] == 1.0

    for numeric in (None, 0, False):
        _run(check(numeric))
    assert "numeric service location id unavailable; falling back to address id" in caplog.text


def test_usage_energy_falls_back_when_the_numeric_call_fails():
    body = _energy_body([2.0], [0.0], [HOUR_LATER])

    async def failed_status() -> None:
        client = RecordingClient([(b"", False), body])
        parsed = await client.get_usage_energy(ADDRESS, 150, ZONE, NOW)
        assert [call[1] for call in client.calls] == [NUMERIC_BYTES, ADDRESS_BYTES]
        assert parsed["grid_to_home_kwh"] == 2.0

    async def raised() -> None:
        client = RecordingClient([ConnectionError("down"), body])
        parsed = await client.get_usage_energy(ADDRESS, 150, ZONE, NOW)
        assert client.calls[1][1] == ADDRESS_BYTES
        assert parsed["grid_to_home_kwh"] == 2.0

    _run(failed_status())
    _run(raised())


def test_empty_numeric_success_does_not_retry():
    async def check() -> None:
        client = RecordingClient([(b"", True)])
        parsed = await client.get_usage_energy(ADDRESS, 150, ZONE, NOW)
        assert len(client.calls) == 1
        assert parsed == {
            "grid_to_home_kwh": None,
            "solar_to_home_kwh": None,
            "battery_to_home_kwh": None,
        }

    _run(check())


def test_usage_energy_sums_only_the_local_day_and_keeps_zero():
    body = _energy_body(
        [9.0, 1.5, 0.0],
        [4.0, 0.0, 0.0],
        [HOUR_BEFORE, HOUR_MIDNIGHT, HOUR_LATER],
    )
    parsed = BasePowerApiClient._parse_usage_energy(body, ZONE, NOW)
    assert parsed["grid_to_home_kwh"] == 1.5
    assert parsed["solar_to_home_kwh"] == 0.0
    assert parsed["battery_to_home_kwh"] is None

    async def no_fallback() -> None:
        client = RecordingClient([body])
        await client.get_usage_energy(ADDRESS, 150, ZONE, NOW)
        assert len(client.calls) == 1

    _run(no_fallback())


def test_usage_energy_malformed_stays_none():
    mismatched = _packed(2, [1.0]) + _timestamp(5, HOUR_LATER) + _timestamp(5, HOUR_MIDNIGHT)
    parsed = BasePowerApiClient._parse_usage_energy(mismatched, ZONE, NOW)
    assert parsed == {
        "grid_to_home_kwh": None,
        "solar_to_home_kwh": None,
        "battery_to_home_kwh": None,
    }

    nan = _packed(2, [float("nan")]) + _timestamp(5, HOUR_LATER)
    assert BasePowerApiClient._parse_usage_energy(nan, ZONE, NOW)["grid_to_home_kwh"] is None
    assert BasePowerApiClient._parse_usage_energy(b"", ZONE, NOW)["grid_to_home_kwh"] is None


def test_billing_empty_submessages_stay_none():
    parsed = BasePowerApiClient._parse_billing_metadata(bytes.fromhex("0a001200"))
    assert parsed == {"amount_cents": None, "due_date": None}


def test_usage_cycles_parse_a_long_entry_and_the_current_start():
    past = (HOUR_BEFORE - 86400, HOUR_BEFORE)
    current = (HOUR_MIDNIGHT, HOUR_MIDNIGHT + 86400)
    padding = _len_field(10, b"\x00" * 120)
    entry = _timestamp(2, current[0]) + _timestamp(3, current[1]) + padding
    assert len(entry) >= 128
    older = _timestamp(2, past[0]) + _timestamp(3, past[1])
    payload = _len_field(1, older) + _len_field(1, entry) + b"\x12\x07asset-1"
    parsed = BasePowerApiClient._parse_usage_cycles(payload, NOW)
    assert parsed["asset_id"] == "asset-1"
    assert parsed["cycle_start"] == HOUR_MIDNIGHT

    asset_only = BasePowerApiClient._parse_usage_cycles(b"\x12\x07asset-1", NOW)
    assert asset_only["asset_id"] == "asset-1"
    assert asset_only["cycle_start"] is None


def test_recent_usage_converts_average_kw_and_totals_today_only():
    points = BasePowerApiClient._parse_recent_usage(
        _recent_point(HOUR_BEFORE, 100.0)
        + _recent_point(HOUR_MIDNIGHT, 4.0)
        + _recent_point(HOUR_LATER, 8.0)
    )
    assert [(point["kwh"], point["watts"]) for point in points] == [
        (25.0, 100000),
        (1.0, 4000),
        (2.0, 8000),
    ]
    summary = summarize_recent_usage(points, ZONE, NOW)
    assert summary["current_kwh"] == 2.0
    assert summary["current_power_watts"] == 8000
    assert summary["daily_total_kwh"] == 3.0
    assert summary["daily_peak_kwh"] == 2.0
    assert summary["daily_low_kwh"] == 1.0
    assert summary["intervals_today"] == 2
    assert summary["daily_avg_kwh"] == 1.5

    # The old reading treated kW as kWh and multiplied by 4000 for watts.
    assert points[-1]["watts"] != round(8.0 * 4000)
    assert summary["daily_total_kwh"] != round(100.0 + 4.0 + 8.0, 3)


def test_debug_log_shows_the_numeric_payload_without_an_interval(caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.base_power.api")
    body = _energy_body([1.0], [0.0], [HOUR_LATER])

    async def check() -> None:
        client = RecordingClient([body])
        await client.get_usage_energy(ADDRESS, 49652, ZONE, NOW)

    _run(check())
    text = caplog.text
    assert "MobileGetUsageEnergy request using=numeric" in text
    assert "numeric_service_location_id=49652" in text
    assert "interval_start=" not in text
    payload = _api._encode_int_service_location(49652)
    assert payload == bytes.fromhex(text.split("payload=")[1].split()[0])
    assert payload == b"\x08" + _api._encode_varint(49652)


def test_open_cycle_ending_at_the_request_time_is_current():
    """The open cycle's end is the server clock, so it is not after now."""
    moment = int(NOW.timestamp())
    earlier = HOUR_BEFORE
    future = moment + 3600
    payload = b"".join(
        (
            _len_field(1, _timestamp(2, earlier) + _timestamp(3, earlier + 60)),
            _len_field(1, _timestamp(2, HOUR_MIDNIGHT) + _timestamp(3, moment)),
            _len_field(1, _timestamp(2, future) + _timestamp(3, future + 60)),
        )
    )
    parsed = BasePowerApiClient._parse_usage_cycles(payload, NOW)
    assert parsed["cycle_start"] == HOUR_MIDNIGHT


def test_daily_average_interval_is_today_total_over_count():
    share = 15.422 / 44
    points = [
        {"timestamp": HOUR_LATER + index, "kwh": share, "watts": 1}
        for index in range(44)
    ]
    summary = summarize_recent_usage(points, ZONE, NOW)
    assert summary["intervals_today"] == 44
    assert summary["daily_total_kwh"] == 15.422
    assert summary["daily_avg_kwh"] == round(15.422 / 44, 3)
    assert abs(summary["daily_avg_kwh"] - 0.351) < 0.001

    empty = summarize_recent_usage([], ZONE, NOW)
    assert empty["intervals_today"] == 0
    assert empty["daily_avg_kwh"] is None

    yesterday = summarize_recent_usage(
        [{"timestamp": HOUR_BEFORE, "kwh": 4.0, "watts": 1000}], ZONE, NOW
    )
    assert yesterday["intervals_today"] == 0
    assert yesterday["daily_avg_kwh"] is None


def test_grid_current_amps_from_power_and_voltage():
    assert _api.grid_current_amps(1.3, 245) == round(1.3 * 1000 / 245, 3)
    assert _api.grid_current_amps(0, 245) == 0.0
    # Negative grid power is export. The sign is kept.
    assert _api.grid_current_amps(-1.3, 245) == round(-1.3 * 1000 / 245, 3)
    assert _api.grid_current_amps(None, 245) is None
    assert _api.grid_current_amps(1.3, None) is None
    assert _api.grid_current_amps(1.3, 0) is None
    assert _api.grid_current_amps(1.3, -10) is None
    assert _api.grid_current_amps(float("nan"), 245) is None

    combined = BasePowerApiClient.combine_grid_telemetry(
        {"available": True, "power_from_grid_kw": 1.3},
        {"available": True, "voltage_volts": 245.0},
    )
    assert combined["current_power_amps"] == round(1.3 * 1000 / 245, 3)
    missing = BasePowerApiClient.combine_grid_telemetry(
        {"available": True},
        {"available": True, "voltage_volts": 245.0},
    )
    assert missing["current_power_amps"] is None


def test_daily_grid_total_uses_today_grid_kwh():
    assert _api.daily_grid_total_kwh(None) is None
    assert _api.daily_grid_total_kwh({}) is None
    assert _api.daily_grid_total_kwh({"energy": {}}) is None
    assert _api.daily_grid_total_kwh({"energy": {"grid_to_home_kwh": None}}) is None
    assert _api.daily_grid_total_kwh({"energy": {"grid_to_home_kwh": 0.0}}) == 0.0
    assert _api.daily_grid_total_kwh({"energy": {"grid_to_home_kwh": 12.5}}) == 12.5
    assert _api.daily_grid_total_kwh({"grid": {"daily_total_kwh_grid": 99}}) is None


def _grpc_frame(flag: int, payload: bytes) -> bytes:
    return bytes([flag]) + struct.pack(">I", len(payload)) + payload


def test_invoke_reads_header_status_trailer_status_and_success():
    message = b"\x08\x01"
    trailer_error = b"grpc-status: 5\r\ngrpc-message: address%20not%20found\r\n"
    trailer_ok = b"grpc-status: 0\r\n"

    class _Response:
        def __init__(self, body: bytes, headers: dict[str, str]) -> None:
            self.headers = headers
            self._body = body

        async def read(self) -> bytes:
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> bool:
            return False

    class _Session:
        def __init__(self, response: _Response) -> None:
            self._response = response

        def post(self, *_args, **_kwargs) -> _Response:
            return self._response

    async def call(body: bytes, headers: dict[str, str]):
        client = BasePowerApiClient(_Session(_Response(body, headers)))
        return await client._invoke("MobileGetUsageEnergy", b"", optional=True)

    data, ok = _run(
        call(
            _grpc_frame(0, message),
            {"grpc-status": "5", "grpc-message": "from-header"},
        )
    )
    assert ok is False
    assert data == b""

    data, ok = _run(
        call(_grpc_frame(0, message) + _grpc_frame(0x80, trailer_error), {})
    )
    assert ok is False
    assert data == b""

    data, ok = _run(
        call(
            _grpc_frame(0, message) + _grpc_frame(0x80, trailer_ok),
            {},
        )
    )
    assert ok is True
    assert data == message


def test_auth_logs_mask_the_session_id():
    assert _auth._mask_secret(None) == "none"
    assert _auth._mask_secret("sess_example_value") == "...alue"
    source = (_ROOT / "custom_components" / "base_power" / "auth.py").read_text()
    assert "_LOGGER.warning" not in source
    assert "client_token[:20]" not in source
    assert "_mask_secret(session_id)" in source
    assert "code=%s" not in source
    assert "jwt=%s" not in source
