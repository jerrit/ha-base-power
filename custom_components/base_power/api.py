"""Base Power gRPC-Web API client."""

from __future__ import annotations

import logging
import math
import struct
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from .const import (
    API_HOST,
    API_SERVICE,
    API_CONTENT_TYPE,
    GRID_COLLAPSED_VOLTS,
    GRID_PRESENT_VOLTS,
)

_LOGGER = logging.getLogger(__name__)


def _build_grpc_frame(payload: bytes) -> bytes:
    """Wrap protobuf message in gRPC-Web frame."""
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def _parse_grpc_frame(response: bytes) -> bytes:
    """Extract protobuf data from gRPC-Web response frame."""
    if len(response) < 5:
        return b""
    flag = response[0]
    if flag == 0x80:  # Trailer only, no data
        return b""
    length = struct.unpack(">I", response[1:5])[0]
    if length == 0:
        return b""
    return response[5 : 5 + length]


def _encode_service_location_request(service_location_id: str) -> bytes:
    """Encode a protobuf request with the external address id as field 1 (string)."""
    encoded = service_location_id.encode("utf-8")
    return b"\x0a" + bytes([len(encoded)]) + encoded


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

    Grid status, service context, and usage take this int32. The external
    address id is a different string and those methods ignore it.
    """
    return b"\x08" + _encode_varint(service_location_id)


def _encode_timestamp_field(field_number: int, seconds: int) -> bytes:
    """Encode a google.protobuf.Timestamp (seconds only) as a length-delimited field."""
    message = b"\x08" + _encode_varint(int(seconds))
    tag = (field_number << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(message)) + message


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

    Usage energy sums the window it is given. The sensors are daily, so the
    window is local midnight through the next local midnight.
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


def build_site_payload(
    address_id: str,
    numeric_service_location_id: int | None,
    interval: tuple[int, int] | None = None,
) -> tuple[bytes, str]:
    """Encode a site-scoped DashboardAPI request.

    Returns ``(payload, "numeric"|"address")``.

    Field 1 is the numeric service location id, the same int32 grid status
    uses. Field 2 and field 3 are the interval start and end as
    ``google.protobuf.Timestamp`` seconds. Usage calls ignore the external
    address string; an empty usage or energy frame with only that string is
    the failure these methods were seeing.

    When the numeric id is not known yet, the payload stays the address
    string in field 1 and the interval is left off. That is the previous
    request, which still returns data for some accounts.
    """
    if (
        isinstance(numeric_service_location_id, int)
        and not isinstance(numeric_service_location_id, bool)
        and numeric_service_location_id > 0
    ):
        payload = _encode_int_service_location(numeric_service_location_id)
        if (
            interval is not None
            and interval[1] > interval[0] >= 0
        ):
            payload += _encode_timestamp_field(2, interval[0])
            payload += _encode_timestamp_field(3, interval[1])
        return payload, "numeric"
    return _encode_service_location_request(address_id), "address"


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
    """Parse MobileGetAvailableLocations response to extract the first location ID.

    Response: field 1 = sub-message { field 1 = location_id (string) }
    """
    offset = 0
    while offset < len(data):
        tag_val, new_offset = _decode_varint(data, offset)
        if new_offset == offset:
            break
        offset = new_offset
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        if wire_type == 2:
            length, offset = _decode_varint(data, offset)
            sub = data[offset : offset + length]
            offset += length
            if field_num == 1:
                # First location sub-message — extract field 1 (location ID string)
                sub_offset = 0
                while sub_offset < len(sub):
                    sub_tag, new_sub = _decode_varint(sub, sub_offset)
                    if new_sub == sub_offset:
                        break
                    sub_offset = new_sub
                    sub_field = sub_tag >> 3
                    sub_wire = sub_tag & 0x07
                    if sub_wire == 2:
                        sub_len, sub_offset = _decode_varint(sub, sub_offset)
                        value = sub[sub_offset : sub_offset + sub_len]
                        sub_offset += sub_len
                        if sub_field == 1:
                            try:
                                return value.decode("utf-8")
                            except Exception:
                                return None
                    else:
                        sub_offset = _skip_field(sub, sub_offset, sub_wire)
                return None
        else:
            offset = _skip_field(data, offset, wire_type)
    return None


def _has_any_value(parsed: dict[str, Any]) -> bool:
    """True when a parser found at least one reading."""
    return any(value is not None for value in parsed.values())


def _parse_billing_fields(data: bytes, result: dict[str, Any], *, nested: bool = False) -> None:
    """Read amount_cents and due_date from a billing message."""
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
            if field_num == 2 and result.get("amount_cents") is None:
                result["amount_cents"] = val
        elif wire_type == 2:
            length, offset = _decode_varint(data, offset)
            if offset + length > len(data):
                break
            payload = data[offset : offset + length]
            offset += length
            if field_num == 3 and payload and result.get("due_date") is None:
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                if text and text.isprintable():
                    result["due_date"] = text
                    continue
            # Live captures nest amount_cents inside field 1.
            if field_num == 1 and payload and not nested and result.get("amount_cents") is None:
                _parse_billing_fields(payload, result, nested=True)
        else:
            offset = _skip_field(data, offset, wire_type)


class BasePowerApiClient:
    """Client for the Base Power gRPC-Web API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self._session = session
        self._jwt: str | None = None

    def set_jwt(self, jwt: str) -> None:
        """Set the current JWT for API calls."""
        self._jwt = jwt

    @staticmethod
    async def async_discover_location_id(
        session: aiohttp.ClientSession, jwt: str
    ) -> str | None:
        """Call MobileGetAvailableLocations to auto-discover service location ID."""
        url = f"{API_HOST}/{API_SERVICE}/MobileGetAvailableLocations"
        headers = {
            "Content-Type": API_CONTENT_TYPE,
            "authorization": jwt,
            "x-grpc-web": "1",
        }
        try:
            async with session.post(
                url, headers=headers, data=_build_grpc_frame(b"")
            ) as resp:
                grpc_status = resp.headers.get("grpc-status", "")
                if grpc_status and grpc_status != "0":
                    _LOGGER.debug(
                        "MobileGetAvailableLocations grpc-status=%s", grpc_status
                    )
                    return None
                raw = await resp.read()
                data = _parse_grpc_frame(raw)
                return _parse_first_location_id(data)
        except Exception:
            _LOGGER.debug("Location discovery request failed", exc_info=True)
            return None

    async def _call(
        self, method: str, payload: bytes = b"", *, optional: bool = False
    ) -> bytes:
        """Make a gRPC-Web API call.

        Optional calls log a missing contract or empty frame at debug. Those
        responses are normal for some accounts and must not fill the error log.
        """
        url = f"{API_HOST}/{API_SERVICE}/{method}"
        headers = {
            "Content-Type": API_CONTENT_TYPE,
            "authorization": self._jwt,
            "x-grpc-web": "1",
        }
        body = _build_grpc_frame(payload)

        async with self._session.post(url, headers=headers, data=body) as resp:
            response_data = await resp.read()
            grpc_status = resp.headers.get("grpc-status", "")
            if grpc_status and grpc_status != "0":
                grpc_message = resp.headers.get("grpc-message", "unknown")
                log = _LOGGER.debug if optional else _LOGGER.error
                log(
                    "gRPC error calling %s: status=%s message=%s",
                    method,
                    grpc_status,
                    grpc_message,
                )
                return b""
            data = _parse_grpc_frame(response_data)
            if not data:
                _LOGGER.debug("%s returned an empty payload", method)
            return data

    async def get_dashboard_root(self, service_location_id: str) -> dict[str, Any]:
        """Get dashboard root data, including the numeric service location id."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetDashboardRoot", payload)
        return self._parse_dashboard_root(data)

    async def get_recent_usage(
        self,
        service_location_id: str,
        numeric_service_location_id: int | None = None,
        interval: tuple[int, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent 15-min interval usage data."""
        return await self._fetch_parsed(
            "MobileGetRecentUsage",
            service_location_id,
            numeric_service_location_id,
            interval,
            self._parse_recent_usage,
            bool,
            optional=False,
        )

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
        interval: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Get energy breakdown (grid-to-home, solar-to-home kWh)."""
        return await self._fetch_parsed(
            "MobileGetUsageEnergy",
            service_location_id,
            numeric_service_location_id,
            interval,
            self._parse_usage_energy,
            _has_any_value,
            optional=True,
        )

    async def get_billing_metadata(
        self,
        service_location_id: str,
        numeric_service_location_id: int | None = None,
        interval: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Get billing info (amount, due date)."""
        return await self._fetch_parsed(
            "MobileGetBillingMetadata",
            service_location_id,
            numeric_service_location_id,
            interval,
            self._parse_billing_metadata,
            _has_any_value,
            optional=True,
        )

    async def get_wifi_metrics(
        self,
        service_location_id: str,
        numeric_service_location_id: int | None = None,
        interval: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Get battery WiFi connectivity metrics."""
        return await self._fetch_parsed(
            "MobileGetWifiMetrics",
            service_location_id,
            numeric_service_location_id,
            interval,
            self._parse_wifi_metrics,
            lambda parsed: bool(parsed.get("scan")),
            optional=True,
        )

    async def get_usage_cycles(
        self,
        service_location_id: str,
        numeric_service_location_id: int | None = None,
        interval: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Get usage cycle dates and asset ID."""
        return await self._fetch_parsed(
            "MobileGetUsageCycles",
            service_location_id,
            numeric_service_location_id,
            interval,
            self._parse_usage_cycles,
            _has_any_value,
            optional=True,
        )

    async def _fetch_parsed(
        self,
        method: str,
        address_id: str,
        numeric_service_location_id: int | None,
        interval: tuple[int, int] | None,
        parse,
        present,
        *,
        optional: bool,
    ):
        """Call a site RPC with the numeric id, then the address string.

        A missing numeric id, an empty frame, or a frame the parser cannot
        read falls back to the address-string request. The fallback is the
        previous encoding, so a poll cannot crash or replace a working
        reading with an empty one when the numeric id is not available yet.
        """
        if (
            isinstance(numeric_service_location_id, int)
            and not isinstance(numeric_service_location_id, bool)
            and numeric_service_location_id > 0
        ):
            payload, mode = build_site_payload(
                address_id, numeric_service_location_id, interval
            )
            self._log_site_request(
                method, mode, numeric_service_location_id, interval, payload
            )
            try:
                raw = await self._call(method, payload, optional=True)
            except Exception as err:
                _LOGGER.debug(
                    "%s numeric request failed (%s); falling back to address id",
                    method,
                    err,
                )
                raw = b""
            parsed = parse(raw)
            if present(parsed):
                return parsed
            start = interval[0] if interval else None
            end = interval[1] if interval else None
            _LOGGER.debug(
                "%s empty with numeric service location id %s "
                "interval_start=%s interval_end=%s; falling back to address id",
                method,
                numeric_service_location_id,
                start,
                end,
            )
        else:
            _LOGGER.debug(
                "%s numeric service location id unavailable; falling back to address id",
                method,
            )
        payload, mode = build_site_payload(address_id, None, None)
        self._log_site_request(method, mode, None, None, payload)
        raw = await self._call(method, payload, optional=optional)
        return parse(raw)

    @staticmethod
    def _log_site_request(
        method: str,
        mode: str,
        numeric_service_location_id: int | None,
        interval: tuple[int, int] | None,
        payload: bytes,
    ) -> None:
        """Log the site id and interval actually written into the request."""
        _LOGGER.debug(
            "%s request using=%s numeric_service_location_id=%s "
            "interval_start=%s interval_end=%s payload=%s",
            method,
            mode,
            numeric_service_location_id,
            interval[0] if interval else None,
            interval[1] if interval else None,
            payload.hex(),
        )

    @staticmethod
    def _parse_dashboard_root(data: bytes) -> dict[str, Any]:
        """Parse MobileGetDashboardRoot response."""
        result: dict[str, Any] = {
            "service_location_id": None,
            "battery_status": 0,
            "battery_count": 0,
            "has_solar": False,
        }
        if not data:
            return result

        _LOGGER.debug("DashboardRoot raw hex: %s", data.hex())

        offset = 0
        field3_count = 0
        first_sub_fields: dict[int, int] = {}

        while offset < len(data):
            tag_val, new_offset = _decode_varint(data, offset)
            if new_offset == offset:
                break
            offset = new_offset
            field_num = tag_val >> 3
            wire_type = tag_val & 0x07

            if field_num == 7 and wire_type == 0:
                # Numeric service location id. Grid and service-context calls
                # need this int. It is not a backup duration.
                val, offset = _decode_varint(data, offset)
                result["service_location_id"] = val
            elif field_num == 3 and wire_type == 2:
                # Status sub-message — may appear once per battery unit
                field3_count += 1
                msg_len, offset = _decode_varint(data, offset)
                end = offset + msg_len
                sub_fields: dict[int, int] = {}
                while offset < end:
                    inner_tag_val, inner_new_offset = _decode_varint(data, offset)
                    if inner_new_offset == offset:
                        break
                    offset = inner_new_offset
                    inner_field = inner_tag_val >> 3
                    inner_wire = inner_tag_val & 0x07
                    if inner_wire == 0:
                        inner_val, offset = _decode_varint(data, offset)
                        sub_fields[inner_field] = inner_val
                    else:
                        offset = _skip_field(data, offset, inner_wire)
                _LOGGER.debug(
                    "DashboardRoot field3[%d] sub-fields: %s", field3_count, sub_fields
                )
                if field3_count == 1:
                    first_sub_fields = sub_fields
                    result["battery_status"] = sub_fields.get(2, 0)
            elif wire_type == 2:
                length, offset = _decode_varint(data, offset)
                _LOGGER.debug(
                    "DashboardRoot unknown field %d (len-delimited, %d bytes) at offset %d",
                    field_num, length, offset,
                )
                offset += length
            elif wire_type == 0:
                val, offset = _decode_varint(data, offset)
                _LOGGER.debug(
                    "DashboardRoot unknown field %d (varint=%d)", field_num, val
                )
            else:
                offset = _skip_field(data, offset, wire_type)

        # Derive battery_count:
        # Theory A: sub-field 1 of the first field-3 sub-message is the unit count
        # Theory B: field-3 repeats once per battery unit
        # Use whichever is larger; both give 1 for a 1-unit system.
        count_from_subfield = first_sub_fields.get(1, 0)
        result["battery_count"] = max(field3_count, count_from_subfield)
        _LOGGER.debug(
            "DashboardRoot battery_count: field3_count=%d subfield1=%d → %d",
            field3_count, count_from_subfield, result["battery_count"],
        )

        if result["battery_count"] == 0:
            result["battery_count"] = 1

        _LOGGER.debug("DashboardRoot parsed: %s", result)
        return result

    @staticmethod
    def _parse_recent_usage(data: bytes) -> list[dict[str, Any]]:
        """Parse MobileGetRecentUsage response.

        Each entry is 15 bytes:
          0A 0D (field 1, len 13)
          0A 06 08 XX XX XX XX XX (Timestamp varint)
          15 XX XX XX XX (float32, little-endian)
        """
        points: list[dict[str, Any]] = []
        if not data:
            return points

        offset = 0
        while offset < len(data) - 14:
            if data[offset] != 0x0A or data[offset + 1] != 0x0D:
                break

            # Decode timestamp varint at offset+5
            ts = 0
            shift = 0
            for i in range(5):
                b = data[offset + 5 + i]
                ts |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break

            # Decode float32 at offset+11 (little-endian)
            kwh = struct.unpack("<f", data[offset + 11 : offset + 15])[0]

            points.append(
                {
                    "timestamp": ts,
                    "kwh": round(kwh, 3),
                    "watts": round(kwh * 4000),
                }
            )
            offset += 15

        return points

    @staticmethod
    def _parse_wifi_metrics(data: bytes) -> dict[str, Any]:
        """Parse MobileGetWifiMetrics response.

        The API returns a WiFi scan list (field 1, repeated) — signal strengths
        for all visible networks. There is no "connected" indicator in the response.
        We return the full scan dict so the coordinator can look up a user-configured
        SSID, or fall back to the strongest-signal entry.

        Result keys:
          scan    — {ssid: signal_percent} for every visible network
          ssid    — None (set by coordinator after SSID preference lookup)
          signal  — None (set by coordinator)
          connected — False (set by coordinator)
        """
        result: dict[str, Any] = {"scan": {}, "ssid": None, "signal": None, "connected": False}
        if not data or len(data) < 4:
            return result

        offset = 0
        while offset < len(data):
            tag_val, new_offset = _decode_varint(data, offset)
            if new_offset == offset:
                break
            offset = new_offset
            field_num = tag_val >> 3
            wire_type = tag_val & 0x07

            if wire_type == 2:
                length, offset = _decode_varint(data, offset)
                sub = data[offset : offset + length]
                offset += length
                if field_num == 1:
                    # Scan entry: field 1 = ssid (string), field 2 = signal (varint)
                    ssid: str | None = None
                    signal: int | None = None
                    off = 0
                    while off < len(sub):
                        stag, soff = _decode_varint(sub, off)
                        if soff == off:
                            break
                        off = soff
                        sfn = stag >> 3
                        swt = stag & 0x07
                        if swt == 2:
                            slen, off = _decode_varint(sub, off)
                            val = sub[off : off + slen]
                            off += slen
                            if sfn == 1:
                                try:
                                    ssid = val.decode("utf-8")
                                except Exception:
                                    pass
                        elif swt == 0:
                            ival, off = _decode_varint(sub, off)
                            if sfn == 2:
                                signal = ival
                        else:
                            off = _skip_field(sub, off, swt)
                    if ssid:
                        result["scan"][ssid] = signal
            elif wire_type == 0:
                _, offset = _decode_varint(data, offset)
            else:
                offset = _skip_field(data, offset, wire_type)

        _LOGGER.debug("WifiMetrics scan: %s", result["scan"])
        return result

    @staticmethod
    def _parse_usage_cycles(data: bytes) -> dict[str, Any]:
        """Parse MobileGetUsageCycles response for asset_id."""
        result: dict[str, Any] = {"asset_id": None}
        if not data:
            return result

        offset = 0
        while offset < len(data):
            if data[offset] == 0x12:  # field 2 (asset_id string)
                str_len = data[offset + 1]
                result["asset_id"] = data[offset + 2 : offset + 2 + str_len].decode(
                    "utf-8"
                )
                break
            elif data[offset] == 0x0A:  # field 1 (cycle entry), skip it
                entry_len = data[offset + 1]
                offset += 2 + entry_len
            else:
                offset += 1

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
        the current load. A missing bool on a present message is false.
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
            "power_from_grid_kw": context.get("power_from_grid_kw"),
            "power_from_storage_kw": context.get("power_from_storage_kw"),
            "power_from_solar_kw": context.get("power_from_solar_kw"),
            "power_to_home_kw": context.get("power_to_home_kw"),
            "current_power_amps": None,
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
    def _parse_usage_energy(data: bytes) -> dict[str, Any]:
        """Parse MobileGetUsageEnergy response.

        Expected fields (float32 or double):
          Field 1: grid_to_home_kwh
          Field 2: solar_to_home_kwh
          Field 3: battery_to_home_kwh

        A missing field stays None. Zero is a real reading. NaN is not.
        """
        result: dict[str, Any] = {
            "grid_to_home_kwh": None,
            "solar_to_home_kwh": None,
            "battery_to_home_kwh": None,
        }
        if not data:
            return result

        _LOGGER.debug("UsageEnergy raw hex: %s", data.hex())
        keys = {
            1: "grid_to_home_kwh",
            2: "solar_to_home_kwh",
            3: "battery_to_home_kwh",
        }
        for field_num, kind, value in _iter_fields(data):
            key = keys.get(field_num)
            if key is None or kind not in ("float", "double"):
                continue
            if not math.isfinite(value):
                continue
            result[key] = round(value, 3)

        _LOGGER.debug("UsageEnergy parsed: %s", result)
        return result

    @staticmethod
    def _parse_billing_metadata(data: bytes) -> dict[str, Any]:
        """Parse MobileGetBillingMetadata response.

        Some accounts return amount_cents as a top-level field 2 varint.
        Others wrap it in a field 1 sub-message, for example
        0a0a080210a5930118b0880212020802 → field 2 = 18853 ($188.53).
        """
        result: dict[str, Any] = {
            "amount_cents": None,
            "due_date": None,
        }
        if not data:
            return result

        _LOGGER.debug("BillingMetadata raw hex: %s", data.hex())
        _parse_billing_fields(data, result)
        _LOGGER.debug("BillingMetadata parsed: %s", result)
        return result
