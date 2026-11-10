"""Base Power gRPC-Web API client."""

from __future__ import annotations

import logging
import math
import struct
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from .const import (
    API_CONTENT_TYPE,
    API_HOST,
    API_SERVICE,
    GRID_COLLAPSED_VOLTS,
    GRID_PRESENT_VOLTS,
    REQUEST_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
# gRPC status for a missing or expired token
GRPC_UNAUTHENTICATED = "16"
# MobileGetServiceContext gatewayWifi.state. Proto3 leaves out 0.
GATEWAY_WIFI_STATES = {0: "connected", 1: "connecting", 2: "not_connected"}


def _build_grpc_frame(payload: bytes) -> bytes:
    """Wrap protobuf message in gRPC-Web frame."""
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def _read_grpc_web(response: bytes) -> tuple[bytes, str, str]:
    """Split a gRPC-Web body into the protobuf message and trailer status.

    Each frame is a flag byte, a 4-byte length, and that many payload bytes.
    Flag 0x80 is the trailer, an HTTP header block. Live responses put
    ``grpc-status`` there instead of in the response headers.
    """
    message = b""
    status = ""
    grpc_message = ""
    offset = 0
    while offset + 5 <= len(response):
        flag = response[offset]
        length = struct.unpack(">I", response[offset + 1 : offset + 5])[0]
        offset += 5
        if offset + length > len(response):
            break
        frame = response[offset : offset + length]
        offset += length
        if flag & 0x80:
            text = frame.decode("utf-8", "replace")
            for line in text.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()
                if key == "grpc-status":
                    status = value
                elif key == "grpc-message":
                    grpc_message = unquote(value)
        elif not message:
            message = frame
    return message, status, grpc_message


def _merge_grpc_status(
    header_status: str, header_message: str, body: bytes
) -> tuple[bytes, str, str]:
    """Prefer a non-zero status from the header or the trailer frame."""
    message, trailer_status, trailer_message = _read_grpc_web(body)
    status = (header_status or "").strip()
    detail = (header_message or "").strip()
    trailer_status = trailer_status.strip()
    trailer_message = trailer_message.strip()
    if trailer_status and trailer_status != "0":
        return message, trailer_status, trailer_message or detail or "unknown"
    if not status:
        status = trailer_status
        detail = detail or trailer_message
    if status and status != "0" and not detail:
        detail = "unknown"
    return message, status, detail


def daily_grid_total_kwh(coordinator_data: dict[str, Any] | None) -> float | None:
    """Today's grid kWh from usage energy.

    The grid-status call is a voltage series and does not carry this total.
    None stays unknown on the sensor.
    """
    if not coordinator_data:
        return None
    energy = coordinator_data.get("energy") or {}
    value = energy.get("grid_to_home_kwh")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def grid_current_amps(power_from_grid_kw: float | None, voltage_volts: float | None) -> float | None:
    """Grid current from power and voltage.

    Amps are ``kW * 1000 / volts``. A negative ``power_from_grid_kw`` is power
    leaving the house toward the grid, so the current stays negative. Zero
    power is a real zero. Missing values, a non-positive voltage, or a
    non-finite number stay unknown.
    """
    if isinstance(power_from_grid_kw, bool) or isinstance(voltage_volts, bool):
        return None
    if not isinstance(power_from_grid_kw, (int, float)) or not isinstance(voltage_volts, (int, float)):
        return None
    if not math.isfinite(power_from_grid_kw) or not math.isfinite(voltage_volts):
        return None
    if voltage_volts <= 0:
        return None
    return round(power_from_grid_kw * 1000 / voltage_volts, 3)


def _encode_service_location_request(service_location_id: str) -> bytes:
    """Encode a protobuf request with the external address id as field 1 (string)."""
    encoded = service_location_id.encode("utf-8")
    return b"\x0a" + _encode_varint(len(encoded)) + encoded


def _encode_varint(value: int) -> bytes:
    """Encode an unsigned protobuf varint."""
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def _encode_int_service_location(service_location_id: int) -> bytes:
    """Encode field 1 as the numeric service location id.

    Grid status, service context, and usage energy take this int32.
    Recent usage, billing, cycles, and Wi-Fi keep the address string.
    """
    return b"\x08" + _encode_varint(service_location_id)


def _numeric_location_id(value: int | None) -> int | None:
    """Return a positive service location id, or None when it is not usable."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _zone(time_zone: str | None) -> ZoneInfo | datetime.tzinfo:
    """Resolve a Home Assistant time zone, falling back to UTC."""
    if not time_zone:
        return UTC
    try:
        return ZoneInfo(time_zone)
    except ZoneInfoNotFoundError:
        _LOGGER.debug("Unknown time zone %s; using UTC for the usage interval", time_zone)
        return UTC


def local_day_interval(
    time_zone: str | None, now: datetime | None = None
) -> tuple[int, int]:
    """Return the local calendar day's [start, end) as Unix seconds.

    The usage APIs return a rolling day and ignore a requested window. Daily
    sensors keep the samples that fall in this local day.
    """
    zone = _zone(time_zone)
    if now is None:
        current = datetime.now(zone)
    elif now.tzinfo is None:
        current = now.replace(tzinfo=zone)
    else:
        current = now.astimezone(zone)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def _sum_local_day(
    samples: list[float], stamps: list[int | None], start: int, end: int
) -> float | None:
    """Sum samples whose timestamps fall in ``[start, end)``.

    A length mismatch or a non-finite sample is malformed and returns None.
    Zero is a real reading when at least one sample is inside the window.
    """
    if not samples or len(samples) != len(stamps):
        return None
    total = 0.0
    seen = False
    for sample, stamp in zip(samples, stamps, strict=True):
        if stamp is None or not math.isfinite(sample):
            return None
        if start <= stamp < end:
            total += float(sample)
            seen = True
    if not seen:
        return None
    return round(total, 3)


def summarize_recent_usage(
    points: list[dict[str, Any]],
    time_zone: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Current interval, and today's 15-minute kWh totals.

    ``kwh`` on each point is already kW / 4. The daily total adds only points
    at or after local midnight, so the trailing 24-hour series is not summed
    whole.
    """
    summary: dict[str, Any] = {
        "current_kwh": 0.0,
        "current_power_watts": 0,
        "daily_peak_kwh": 0.0,
        "daily_low_kwh": 0.0,
        "daily_total_kwh": 0.0,
        "daily_avg_kwh": None,
        "intervals_today": 0,
    }
    if not points:
        return summary
    latest = points[-1]
    summary["current_kwh"] = latest.get("kwh") or 0.0
    summary["current_power_watts"] = latest.get("watts") or 0
    start, end = local_day_interval(time_zone, now)
    today = [
        point["kwh"]
        for point in points
        if isinstance(point.get("timestamp"), int)
        and start <= point["timestamp"] < end
        and isinstance(point.get("kwh"), (int, float))
        and math.isfinite(point["kwh"])
    ]
    if not today:
        return summary
    summary["daily_peak_kwh"] = max(today)
    summary["daily_low_kwh"] = min(today)
    summary["daily_total_kwh"] = round(sum(today), 3)
    summary["intervals_today"] = len(today)
    if summary["intervals_today"] > 0:
        summary["daily_avg_kwh"] = round(
            summary["daily_total_kwh"] / summary["intervals_today"], 3
        )
    return summary


def _timestamp_seconds(message: bytes) -> int | None:
    """Read google.protobuf.Timestamp.seconds. None when the message is not one."""
    seconds: int | None = None
    for field_num, kind, value in _iter_fields(message):
        if field_num == 1 and kind == "varint":
            seconds = value
    return seconds


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a protobuf varint, return (value, new_offset)."""
    val = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        val |= (b & 0x7F) << shift
        shift += 7
        offset += 1
        if not (b & 0x80):
            break
    return val, offset


def _skip_field(data: bytes, offset: int, wire_type: int) -> int:
    """Skip a protobuf field based on wire type."""
    if wire_type == 0:  # Varint
        while offset < len(data) and (data[offset] & 0x80):
            offset += 1
        offset += 1
    elif wire_type == 2:  # Length-delimited
        length, offset = _decode_varint(data, offset)
        offset += length
    elif wire_type == 5:  # 32-bit
        offset += 4
    elif wire_type == 1:  # 64-bit
        offset += 8
    else:
        offset += 1
    return offset


def _iter_fields(data: bytes):
    """Yield (field_number, kind, value) for a protobuf message."""
    offset = 0
    while offset < len(data):
        tag_val, new_offset = _decode_varint(data, offset)
        if new_offset == offset:
            break
        offset = new_offset
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        if wire_type == 0:
            val, offset = _decode_varint(data, offset)
            yield field_num, "varint", val
        elif wire_type == 2:
            length, offset = _decode_varint(data, offset)
            if offset + length > len(data):
                break
            yield field_num, "bytes", data[offset : offset + length]
            offset += length
        elif wire_type == 5:
            if offset + 4 > len(data):
                break
            yield field_num, "float", struct.unpack("<f", data[offset : offset + 4])[0]
            offset += 4
        elif wire_type == 1:
            if offset + 8 > len(data):
                break
            yield field_num, "double", struct.unpack("<d", data[offset : offset + 8])[0]
            offset += 8
        else:
            offset = _skip_field(data, offset, wire_type)


def _packed_floats(blob: bytes) -> list[float]:
    """Decode a packed repeated float32."""
    if not blob or len(blob) % 4:
        return []
    return [
        struct.unpack("<f", blob[index : index + 4])[0]
        for index in range(0, len(blob), 4)
    ]


def _parse_first_location_id(data: bytes) -> str | None:
    """First address id in MobileGetAvailableLocations.

    locations (1, repeated) → addressId (1, string).
    """
    for field_num, kind, location in _iter_fields(data):
        if field_num != 1 or kind != "bytes":
            continue
        for sub_num, sub_kind, value in _iter_fields(location):
            if sub_num == 1 and sub_kind == "bytes":
                return _text(value)
        return None
    return None


def _text(value: bytes) -> str | None:
    """Decode a protobuf string. None when it is empty or not UTF-8."""
    try:
        return value.decode("utf-8") or None
    except UnicodeDecodeError:
        return None


class BasePowerApiClient:
    """Client for the Base Power gRPC-Web API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: Callable[..., Awaitable[str]] | None = None,
    ) -> None:
        """Initialize the API client.

        ``token_provider(force=False)`` returns a valid JWT. With one set,
        every call asks for the token, so a long refresh cannot outlive the
        60-second JWT. Without one, ``set_jwt`` supplies the token.
        """
        self._session = session
        self._token_provider = token_provider
        self._jwt: str | None = None

    def set_jwt(self, jwt: str) -> None:
        """Set the current JWT for API calls."""
        self._jwt = jwt

    @staticmethod
    async def async_discover_locations(
        session: aiohttp.ClientSession, jwt: str
    ) -> list[dict[str, str]]:
        """List the account's sites for setup.

        Tries the newer LocationsService/ListLocations first, then the older
        MobileGetAvailableLocations. Returns [] when neither finds a site.
        """
        from . import api_v2  # api_v2 imports this module, so import late

        client = BasePowerApiClient(session)
        client.set_jwt(jwt)
        try:
            data, ok = await client._invoke(
                "ListLocations",
                optional=True,
                service=f"{api_v2.V2_PACKAGE}.LocationsService",
            )
            locations = api_v2.parse_locations(data) if ok else []
        except Exception:
            _LOGGER.debug("ListLocations failed", exc_info=True)
            locations = []
        if locations:
            return locations
        location_id = await BasePowerApiClient.async_discover_location_id(session, jwt)
        return [{"address_id": location_id, "label": location_id}] if location_id else []

    @staticmethod
    async def async_discover_location_id(
        session: aiohttp.ClientSession, jwt: str
    ) -> str | None:
        """Call MobileGetAvailableLocations to auto-discover service location ID."""
        client = BasePowerApiClient(session)
        client.set_jwt(jwt)
        try:
            data, ok = await client._invoke("MobileGetAvailableLocations", optional=True)
        except Exception:
            _LOGGER.debug("Location discovery request failed", exc_info=True)
            return None
        return _parse_first_location_id(data) if ok else None

    async def _call(
        self,
        method: str,
        payload: bytes = b"",
        *,
        optional: bool = False,
        service: str = API_SERVICE,
    ) -> bytes:
        """Make a gRPC-Web API call and return the protobuf message."""
        data, _ok = await self._invoke(method, payload, optional=optional, service=service)
        return data

    async def _invoke(
        self,
        method: str,
        payload: bytes = b"",
        *,
        optional: bool = False,
        service: str = API_SERVICE,
    ) -> tuple[bytes, bool]:
        """Call one RPC.

        The bool is False when the transport works but gRPC status is not 0.
        An empty status-0 frame is a successful call with no message.
        Optional calls log that status at debug so a missing contract does not
        fill the error log.
        """
        url = f"{API_HOST}/{service}/{method}"
        body = _build_grpc_frame(payload)
        log = _LOGGER.debug if optional else _LOGGER.error

        # Two tries at most. The second only runs when Base says the token
        # is missing or expired, and it asks for a new one first.
        for attempt in (1, 2):
            if self._token_provider is not None:
                self._jwt = await self._token_provider(force=attempt == 2)
            headers = {
                "Content-Type": API_CONTENT_TYPE,
                "authorization": self._jwt,
                "x-grpc-web": "1",
            }
            async with self._session.post(
                url, headers=headers, data=body, timeout=_TIMEOUT
            ) as resp:
                http_status = resp.status
                response_data = await resp.read()
                data, grpc_status, grpc_message = _merge_grpc_status(
                    resp.headers.get("grpc-status", ""),
                    resp.headers.get("grpc-message", ""),
                    response_data,
                )
            expired = grpc_status == GRPC_UNAUTHENTICATED or http_status == 401
            if expired and attempt == 1 and self._token_provider is not None:
                _LOGGER.debug("%s: token rejected, retrying with a new one", method)
                continue
            break

        if grpc_status and grpc_status != "0":
            log(
                "gRPC error calling %s: status=%s message=%s",
                method,
                grpc_status,
                grpc_message or "unknown",
            )
            return b"", False
        if http_status != 200:
            log("HTTP error calling %s: status=%s", method, http_status)
            return b"", False
        if not data:
            _LOGGER.debug("%s returned an empty payload", method)
        return data, True

    async def get_dashboard_root(self, service_location_id: str) -> dict[str, Any]:
        """Get dashboard root data, including the numeric service location id."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetDashboardRoot", payload)
        return self._parse_dashboard_root(data)

    async def get_recent_usage(self, service_location_id: str) -> list[dict[str, Any]]:
        """Get recent 15-min interval usage. The address string is the site id."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetRecentUsage", payload)
        return self._parse_recent_usage(data)

    async def get_grid_status(self, service_location_id: int) -> dict[str, Any]:
        """Get grid voltage samples for a numeric service location id."""
        payload = _encode_int_service_location(service_location_id)
        data = await self._call("MobileGetGridStatus", payload, optional=True)
        return self._parse_grid_status(data)

    async def get_service_context(self, service_location_id: int) -> dict[str, Any]:
        """Get live outage, state of charge, backup hours, and power flow."""
        payload = _encode_int_service_location(service_location_id)
        data = await self._call("MobileGetServiceContext", payload, optional=True)
        return self._parse_service_context(data)

    async def get_usage_energy(
        self,
        service_location_id: str,
        numeric_service_location_id: int | None = None,
        time_zone: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Get today's grid and solar kWh.

        Only this RPC takes the numeric service location id. The server
        ignores interval fields, so the body is field 1 alone. A missing id
        or a failed call uses the address string. An empty success does not
        retry: that address call is empty too.
        """
        numeric_id = _numeric_location_id(numeric_service_location_id)
        if numeric_id is not None:
            payload = _encode_int_service_location(numeric_id)
            _LOGGER.debug(
                "MobileGetUsageEnergy request using=numeric "
                "numeric_service_location_id=%s payload=%s",
                numeric_id,
                payload.hex(),
            )
            try:
                raw, ok = await self._invoke(
                    "MobileGetUsageEnergy", payload, optional=True
                )
            except Exception as err:
                _LOGGER.debug(
                    "MobileGetUsageEnergy numeric request failed (%s); "
                    "falling back to address id",
                    err,
                )
            else:
                if ok:
                    return self._parse_usage_energy(raw, time_zone=time_zone, now=now)
                _LOGGER.debug(
                    "MobileGetUsageEnergy numeric request failed; "
                    "falling back to address id"
                )
        else:
            _LOGGER.debug(
                "MobileGetUsageEnergy numeric service location id unavailable; "
                "falling back to address id"
            )
        payload = _encode_service_location_request(service_location_id)
        raw = await self._call("MobileGetUsageEnergy", payload, optional=True)
        return self._parse_usage_energy(raw, time_zone=time_zone, now=now)

    async def get_billing_metadata(self, service_location_id: str) -> dict[str, Any]:
        """Get billing info. The address string is the site id."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetBillingMetadata", payload, optional=True)
        return self._parse_billing_metadata(data)

    async def get_wifi_metrics(self, service_location_id: str) -> dict[str, Any]:
        """Get battery WiFi metrics. The address string is the site id."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetWifiMetrics", payload, optional=True)
        return self._parse_wifi_metrics(data)

    async def get_usage_cycles(self, service_location_id: str) -> dict[str, Any]:
        """Get usage cycles and the account id. The address string is the site id."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetUsageCycles", payload, optional=True)
        return self._parse_usage_cycles(data)

    async def get_battery_snapshot(self, address_id: str) -> dict[str, Any]:
        """Battery mode (on grid, outage, overcurrent), power flow, and backup hours."""
        from . import api_v2

        data = await self._call(
            "GetSnapshot",
            api_v2.encode_address_request(address_id),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.BatteryService",
        )
        return api_v2.parse_battery_snapshot(data)

    async def get_daily_energy(
        self, address_id: str, start: int, end: int
    ) -> list[dict[str, Any]]:
        """Daily home and solar kWh for [start, end). Base keeps about a month."""
        from . import api_v2

        data = await self._call(
            "GetDailyEnergy",
            api_v2.encode_daily_energy_request(address_id, start, end),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.UsageService",
        )
        return api_v2.parse_daily_energy(data)

    async def get_daily_overview(
        self, address_id: str, time_zone: str | None = None
    ) -> dict[str, Any]:
        """Rolling 24-hour energy mix, grid support events, and today's cost."""
        from . import api_v2

        start, end = local_day_interval(time_zone)
        data = await self._call(
            "GetDailyOverview",
            api_v2.encode_address_request(address_id),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.UsageService",
        )
        return api_v2.parse_daily_overview(data, start, end)

    async def get_recent_grid_voltage(self, address_id: str) -> dict[str, Any]:
        """Grid voltage from the v2 API. Fallback when MobileGetGridStatus is empty."""
        from . import api_v2

        data = await self._call(
            "GetRecentGridVoltage",
            api_v2.encode_address_request(address_id),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.UsageService",
        )
        return api_v2.parse_recent_grid_voltage(data)

    async def get_battery_account(self, address_id: str) -> dict[str, Any]:
        """Battery plan name and hardware SKU from the v2 battery account."""
        from . import api_v2

        data = await self._call(
            "GetAccount",
            api_v2.encode_billing_cycle_request(address_id, api_v2.ACCOUNT_TYPE_BATTERY),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.BillingService",
        )
        return api_v2.parse_battery_account(data)

    async def get_energy_account(self, address_id: str) -> dict[str, Any]:
        """Bill due date and balance for the energy account, from ListAccounts."""
        from . import api_v2

        data = await self._call(
            "ListAccounts",
            api_v2.encode_address_request(address_id),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.BillingService",
        )
        return api_v2.parse_energy_account(data)

    async def get_location(self, address_id: str) -> dict[str, Any]:
        """Energy service, solar, and battery models for the site."""
        from . import api_v2

        data = await self._call(
            "GetLocation",
            api_v2.encode_address_request(address_id),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.LocationsService",
        )
        return api_v2.parse_location(data)

    async def get_current_billing_cycle(self, address_id: str) -> dict[str, Any]:
        """Bill so far and projected bill for the energy account."""
        from . import api_v2

        data = await self._call(
            "GetCurrentBillingCycle",
            api_v2.encode_billing_cycle_request(address_id),
            optional=True,
            service=f"{api_v2.V2_PACKAGE}.BillingService",
        )
        return api_v2.parse_current_billing_cycle(data)

    @staticmethod
    def _parse_dashboard_root(data: bytes) -> dict[str, Any]:
        """Parse MobileGetDashboardRoot.

        address (2) → timezoneIdentifier (9). battery (3) → status (2),
        hasSolar (3), upcomingServiceDate (5). serviceLocationId (7) is the
        numeric id that the grid and service-context calls need.
        """
        result: dict[str, Any] = {
            "service_location_id": None,
            "battery_status": 0,
            "has_solar": False,
            "time_zone": None,
            "upcoming_service_at": None,
        }
        for field_num, kind, value in _iter_fields(data):
            if field_num == 7 and kind == "varint":
                result["service_location_id"] = value
            elif field_num == 2 and kind == "bytes":
                for sub_num, sub_kind, sub_value in _iter_fields(value):
                    if sub_num == 9 and sub_kind == "bytes":
                        result["time_zone"] = _text(sub_value)
            elif field_num == 3 and kind == "bytes":
                for sub_num, sub_kind, sub_value in _iter_fields(value):
                    if sub_num == 2 and sub_kind == "varint":
                        result["battery_status"] = sub_value
                    elif sub_num == 3 and sub_kind == "varint":
                        result["has_solar"] = bool(sub_value)
                    elif sub_num == 5 and sub_kind == "bytes":
                        result["upcoming_service_at"] = _timestamp_seconds(sub_value)
        _LOGGER.debug(
            "DashboardRoot parsed: status=%s has_solar=%s has_id=%s",
            result["battery_status"],
            result["has_solar"],
            result["service_location_id"] is not None,
        )
        return result

    @staticmethod
    def _parse_recent_usage(data: bytes) -> list[dict[str, Any]]:
        """Parse MobileGetRecentUsage.

        power_level_data (1, repeated) → time (1, Timestamp),
        power_to_home_kw (2, float). The float is average kW over the 15
        minutes, not kWh. Energy for the interval is kW / 4. Proto3 leaves
        out a 0.0 float, so a point without field 2 is a real 0 kW.
        """
        points: list[dict[str, Any]] = []
        for field_num, kind, value in _iter_fields(data):
            if field_num != 1 or kind != "bytes":
                continue
            stamp: int | None = None
            kw = 0.0
            for sub_num, sub_kind, sub_value in _iter_fields(value):
                if sub_num == 1 and sub_kind == "bytes":
                    stamp = _timestamp_seconds(sub_value)
                elif sub_num == 2 and sub_kind == "float":
                    kw = sub_value
            if stamp is None or not math.isfinite(kw):
                continue
            points.append(
                {
                    "timestamp": stamp,
                    "kwh": round(kw / 4, 3),
                    "watts": round(kw * 1000),
                }
            )
        return points

    @staticmethod
    def _parse_wifi_metrics(data: bytes) -> dict[str, Any]:
        """Parse MobileGetWifiMetrics.

        wifi_metrics (1, repeated) → ssid (1), signal_strength (2).
        last_updated (2, Timestamp). The reply does not say which network
        the battery is on. The service context does, and the coordinator
        uses that to pick the entry.

        Result keys:
          scan         {ssid: signal percent} for every network listed
          last_updated Unix seconds of the reading, or None
          ssid, signal, connected: set later by apply_wifi_preference
        """
        result: dict[str, Any] = {
            "scan": {},
            "ssid": None,
            "signal": None,
            "connected": False,
            "last_updated": None,
        }
        for field_num, kind, value in _iter_fields(data):
            if field_num == 1 and kind == "bytes":
                ssid: str | None = None
                signal: int | None = None
                for sub_num, sub_kind, sub_value in _iter_fields(value):
                    if sub_num == 1 and sub_kind == "bytes":
                        ssid = _text(sub_value)
                    elif sub_num == 2 and sub_kind == "varint":
                        signal = sub_value
                if ssid:
                    result["scan"][ssid] = signal
            elif field_num == 2 and kind == "bytes":
                result["last_updated"] = _timestamp_seconds(value)
        _LOGGER.debug("WifiMetrics: %d networks", len(result["scan"]))
        return result

    @staticmethod
    def _parse_usage_cycles(
        data: bytes, now: datetime | None = None
    ) -> dict[str, Any]:
        """Parse the account id and the latest billing cycle that has already started.

        Field 1 repeats a cycle whose field 2 and field 3 are start and end
        timestamps. The open cycle's end is the server clock at request time,
        so it is already in the past by the time the reply is parsed. The
        current cycle is the latest start at or before ``now``; its end is
        ignored. Field 2 at the top level is accountId. Lengths use
        varints, so an entry longer than 127 bytes still parses.
        ``cycle_start`` is that Unix second, or None.
        """
        result: dict[str, Any] = {"account_id": None, "cycle_start": None}
        if not data:
            return result

        cycles: list[tuple[int, int | None]] = []
        for field_num, kind, value in _iter_fields(data):
            if field_num == 2 and kind == "bytes" and result["account_id"] is None:
                try:
                    text = value.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                if text and text.isprintable():
                    result["account_id"] = text
            elif field_num == 1 and kind == "bytes":
                start: int | None = None
                end: int | None = None
                for sub_field, sub_kind, sub_value in _iter_fields(value):
                    if sub_kind != "bytes" or sub_field not in (2, 3):
                        continue
                    stamp = _timestamp_seconds(sub_value)
                    if sub_field == 2:
                        start = stamp
                    else:
                        end = stamp
                if start is not None:
                    cycles.append((start, end))

        moment = int((now or datetime.now(UTC)).timestamp())
        current = max((start for start, _end in cycles if start <= moment), default=None)
        result["cycle_start"] = current
        if current is not None:
            _LOGGER.debug("UsageCycles current cycle_start=%s", current)
        return result

    @staticmethod
    def _empty_grid_voltage() -> dict[str, Any]:
        """Grid voltage before a successful MobileGetGridStatus parse."""
        return {
            "available": False,
            "has_gateway": None,
            "voltage_volts": None,
            "voltage_samples": 0,
        }

    @staticmethod
    def _empty_service_context() -> dict[str, Any]:
        """Service context before a successful MobileGetServiceContext parse."""
        return {
            "available": False,
            "has_gateway": None,
            "gateway_connected": None,
            "is_active_outage": None,
            "home_has_power": None,
            "state_of_energy_raw": None,
            "available_backup_hours": None,
            "available_backup_hours_750w": None,
            "wifi_ssid": None,
            "wifi_state": None,
            "power_from_grid_kw": None,
            "power_from_storage_kw": None,
            "power_from_solar_kw": None,
            "power_to_home_kw": None,
        }

    @staticmethod
    def _parse_grid_status(data: bytes) -> dict[str, Any]:
        """Parse MobileGetGridStatus.

        The response is a voltage series, not an outage boolean. Field 1 is a
        map of timestamp string to volts. Field 2 is hasGateway. Field 3 is a
        repeated float that some responses use instead of the map.
        """
        result = BasePowerApiClient._empty_grid_voltage()
        if not data:
            return result

        result["available"] = True
        samples: list[tuple[str, float]] = []
        loose: list[float] = []
        for field_num, kind, value in _iter_fields(data):
            if field_num == 1 and kind == "bytes":
                key = ""
                volts: float | None = None
                for sub_field, sub_kind, sub_value in _iter_fields(value):
                    if sub_field == 1 and sub_kind == "bytes":
                        key = sub_value.decode("utf-8", "replace")
                    elif sub_field == 2 and sub_kind == "float":
                        volts = sub_value
                if volts is not None:
                    samples.append((key, volts))
            elif field_num == 2 and kind == "varint":
                result["has_gateway"] = bool(value)
            elif field_num == 3 and kind == "float":
                loose.append(value)
            elif field_num == 3 and kind == "bytes":
                loose.extend(_packed_floats(value))

        if samples:
            result["voltage_volts"] = max(samples, key=lambda item: item[0])[1]
            result["voltage_samples"] = len(samples)
        elif loose:
            result["voltage_volts"] = loose[-1]
            result["voltage_samples"] = len(loose)
        _LOGGER.debug(
            "GridStatus: samples=%s voltage=%s has_gateway=%s",
            result["voltage_samples"],
            result["voltage_volts"],
            result["has_gateway"],
        )
        return result

    @staticmethod
    def _parse_service_context(data: bytes) -> dict[str, Any]:
        """Parse MobileGetServiceContext.

        stateOfEnergyRaw is the battery percent. availableBackup is hours at
        the current load, availableBackupAt750W at a steady 750 W.
        gatewayWifi is the network the battery is on. A missing bool on a
        present message is false.
        """
        result = BasePowerApiClient._empty_service_context()
        if not data:
            return result

        result["available"] = True
        power_flow: bytes | None = None
        for field_num, kind, value in _iter_fields(data):
            if field_num == 1 and kind == "varint":
                result["has_gateway"] = bool(value)
            elif field_num == 2 and kind == "varint":
                result["gateway_connected"] = bool(value)
            elif field_num == 5 and kind == "varint":
                result["is_active_outage"] = bool(value)
            elif field_num == 7 and kind == "varint":
                result["state_of_energy_raw"] = value
            elif field_num == 8 and kind in ("float", "double"):
                result["available_backup_hours"] = value
            elif field_num == 9 and kind == "varint":
                result["home_has_power"] = bool(value)
            elif field_num == 10 and kind == "bytes":
                power_flow = value
            elif field_num == 11 and kind in ("float", "double"):
                result["available_backup_hours_750w"] = value
            elif field_num == 4 and kind == "bytes":
                state = 0
                for sub_num, sub_kind, sub_value in _iter_fields(value):
                    if sub_num == 1 and sub_kind == "bytes":
                        result["wifi_ssid"] = _text(sub_value)
                    elif sub_num == 2 and sub_kind == "varint":
                        state = sub_value
                result["wifi_state"] = GATEWAY_WIFI_STATES.get(state)

        for key in ("has_gateway", "gateway_connected", "is_active_outage", "home_has_power"):
            if result[key] is None:
                result[key] = False

        if power_flow is not None:
            flows = {1: 0.0, 2: 0.0, 3: 0.0, 5: 0.0}
            for field_num, kind, value in _iter_fields(power_flow):
                if kind in ("float", "double") and field_num in flows:
                    flows[field_num] = value
            result["power_from_grid_kw"] = flows[1]
            result["power_from_storage_kw"] = flows[2]
            result["power_from_solar_kw"] = flows[3]
            result["power_to_home_kw"] = flows[5]

        _LOGGER.debug(
            "ServiceContext: outage=%s soc_raw=%s backup=%s to_home=%s",
            result["is_active_outage"],
            result["state_of_energy_raw"],
            result["available_backup_hours"],
            result["power_to_home_kw"],
        )
        return result

    @staticmethod
    def combine_grid_telemetry(
        context: dict[str, Any], voltage: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge service context and grid voltage into the coordinator grid dict."""
        soc = context.get("state_of_energy_raw")
        backup = context.get("available_backup_hours")
        backup_750w = context.get("available_backup_hours_750w")
        return {
            "available": bool(context.get("available") or voltage.get("available")),
            "has_gateway": (
                voltage.get("has_gateway")
                if voltage.get("has_gateway") is not None
                else context.get("has_gateway")
            ),
            "gateway_connected": context.get("gateway_connected"),
            "is_active_outage": (
                context.get("is_active_outage") if context.get("available") else None
            ),
            "home_has_power": (
                context.get("home_has_power") if context.get("available") else None
            ),
            "voltage_volts": voltage.get("voltage_volts"),
            "voltage_samples": voltage.get("voltage_samples") or 0,
            "battery_soc_percent": None if soc is None else float(soc),
            "backup_hours": None if backup is None else round(float(backup), 2),
            "backup_hours_750w": None if backup_750w is None else round(float(backup_750w), 2),
            "wifi_ssid": context.get("wifi_ssid"),
            "wifi_state": context.get("wifi_state"),
            "power_from_grid_kw": context.get("power_from_grid_kw"),
            "power_from_storage_kw": context.get("power_from_storage_kw"),
            "power_from_solar_kw": context.get("power_from_solar_kw"),
            "power_to_home_kw": context.get("power_to_home_kw"),
            "current_power_amps": grid_current_amps(
                context.get("power_from_grid_kw"), voltage.get("voltage_volts")
            ),
            "hourly_usage": [],
            "grid_is_up": BasePowerApiClient._resolve_grid_is_up(context, voltage),
        }

    @staticmethod
    def _resolve_grid_is_up(
        context: dict[str, Any], voltage: dict[str, Any]
    ) -> bool | None:
        """True when voltage is present and Base has not flagged an outage."""
        if context.get("available") and context.get("is_active_outage"):
            return False
        volts = voltage.get("voltage_volts")
        if volts is None:
            return None
        if volts >= GRID_PRESENT_VOLTS:
            return True
        if volts < GRID_COLLAPSED_VOLTS:
            return False
        return None

    @staticmethod
    def _parse_usage_energy(
        data: bytes, time_zone: str | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """Parse MobileGetUsageEnergy.

        The body is one rolling day of hourly samples, not three totals.
        Field 2 is packed float32 home/grid kWh. Field 4 is packed float32
        solar kWh. Field 5 repeats the hour start as a Timestamp. Samples at
        or after local midnight, and before the next one, are summed. Battery
        energy is not in this message. A missing or mismatched series stays
        None. A summed zero is kept.
        """
        result: dict[str, Any] = {
            "grid_to_home_kwh": None,
            "solar_to_home_kwh": None,
            "battery_to_home_kwh": None,
        }
        if not data:
            return result

        grid: list[float] = []
        solar: list[float] | None = None
        stamps: list[int | None] = []
        solar_seen = False
        for field_num, kind, value in _iter_fields(data):
            if field_num == 2 and kind == "bytes":
                grid.extend(_packed_floats(value))
            elif field_num == 2 and kind == "float":
                grid.append(value)
            elif field_num == 4 and kind == "bytes":
                solar_seen = True
                if solar is None:
                    solar = []
                solar.extend(_packed_floats(value))
            elif field_num == 4 and kind == "float":
                solar_seen = True
                if solar is None:
                    solar = []
                solar.append(value)
            elif field_num == 5 and kind == "bytes":
                stamps.append(_timestamp_seconds(value))

        start, end = local_day_interval(time_zone, now)
        result["grid_to_home_kwh"] = _sum_local_day(grid, stamps, start, end)
        if solar_seen:
            result["solar_to_home_kwh"] = _sum_local_day(solar or [], stamps, start, end)
        _LOGGER.debug(
            "UsageEnergy parsed: %s hours=%s day_start=%s day_end=%s",
            result,
            len(stamps),
            start,
            end,
        )
        return result

    @staticmethod
    def _parse_billing_metadata(data: bytes) -> dict[str, Any]:
        """Parse MobileGetBillingMetadata.

        rep (1) → activeSpendCents (2), lastBillAmountCents (3). For example
        0a0a080210a5930118b0880212020802 is $188.53 spent so far and a last
        bill of $338.40. The message has no due date. That comes from the
        newer ListAccounts call.
        """
        result: dict[str, Any] = {"amount_cents": None, "last_bill_cents": None}
        for field_num, kind, value in _iter_fields(data):
            if field_num != 1 or kind != "bytes":
                continue
            for sub_num, sub_kind, sub_value in _iter_fields(value):
                if sub_kind != "varint":
                    continue
                if sub_num == 2:
                    result["amount_cents"] = sub_value
                elif sub_num == 3:
                    result["last_bill_cents"] = sub_value
        return result
