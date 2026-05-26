"""Base Power gRPC-Web API client."""

from __future__ import annotations

import struct
import logging
from typing import Any

import aiohttp

from .const import API_HOST, API_SERVICE, API_CONTENT_TYPE

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
    """Encode a protobuf request with service_location_id as field 1 (string)."""
    encoded = service_location_id.encode("utf-8")
    return b"\x0a" + bytes([len(encoded)]) + encoded


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


def _parse_grid_data_point(sub: bytes) -> tuple[int, float | None, float | None]:
    """Parse an 18-byte grid data sub-message: {timestamp, power, soc}."""
    ts = 0
    power_val: float | None = None
    soc_val: float | None = None
    offset = 0
    while offset < len(sub):
        tag_val, new_offset = _decode_varint(sub, offset)
        if new_offset == offset:
            break
        offset = new_offset
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        if wire_type == 2:
            length, offset = _decode_varint(sub, offset)
            inner = sub[offset:offset + length]
            offset += length
            if field_num == 1:
                # Timestamp sub-message: {field 1: varint}
                _, ts_offset = _decode_varint(inner, 0)  # skip tag
                ts, _ = _decode_varint(inner, 1)  # read varint after tag byte 0x08
                if inner[0] == 0x08:
                    ts, _ = _decode_varint(inner, 1)
        elif wire_type == 5:
            if offset + 4 <= len(sub):
                val = struct.unpack("<f", sub[offset:offset + 4])[0]
                if field_num == 2:
                    power_val = val
                elif field_num == 3:
                    soc_val = val
            offset += 4
        elif wire_type == 0:
            _, offset = _decode_varint(sub, offset)
        elif wire_type == 1:
            offset += 8
        else:
            break
    return ts, power_val, soc_val


def _parse_usage_point(sub: bytes) -> tuple[int, float]:
    """Parse a 13-byte usage sub-message: {timestamp, kWh}."""
    ts = 0
    kwh = 0.0
    offset = 0
    while offset < len(sub):
        tag_val, new_offset = _decode_varint(sub, offset)
        if new_offset == offset:
            break
        offset = new_offset
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        if wire_type == 2:
            length, offset = _decode_varint(sub, offset)
            inner = sub[offset:offset + length]
            offset += length
            if field_num == 1 and inner[0] == 0x08:
                ts, _ = _decode_varint(inner, 1)
        elif wire_type == 5:
            if offset + 4 <= len(sub):
                val = struct.unpack("<f", sub[offset:offset + 4])[0]
                if field_num == 2:
                    kwh = val
            offset += 4
        elif wire_type == 0:
            _, offset = _decode_varint(sub, offset)
        elif wire_type == 1:
            offset += 8
        else:
            break
    return ts, kwh


def _parse_grid_summary(sub: bytes, result: dict[str, Any]) -> None:
    """Parse Field 6 summary: {field1: total_kwh, field3: avg_kwh}."""
    offset = 0
    while offset < len(sub):
        tag_val, new_offset = _decode_varint(sub, offset)
        if new_offset == offset:
            break
        offset = new_offset
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        if wire_type == 5:
            if offset + 4 <= len(sub):
                val = struct.unpack("<f", sub[offset:offset + 4])[0]
                if field_num == 1:
                    result["daily_total_kwh_grid"] = round(val, 2)
                elif field_num == 3:
                    result["daily_avg_kwh_grid"] = round(val, 4)
            offset += 4
        elif wire_type == 0:
            _, offset = _decode_varint(sub, offset)
        elif wire_type == 2:
            length, offset = _decode_varint(sub, offset)
            offset += length
        elif wire_type == 1:
            offset += 8
        else:
            break


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

    async def _call(self, method: str, payload: bytes = b"") -> bytes:
        """Make a gRPC-Web API call."""
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
                _LOGGER.error(
                    "gRPC error calling %s: status=%s message=%s",
                    method,
                    grpc_status,
                    grpc_message,
                )
                return b""
            data = _parse_grpc_frame(response_data)
            return data

    async def get_dashboard_root(self, service_location_id: str) -> dict[str, Any]:
        """Get dashboard root data including backup hours and status."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetDashboardRoot", payload)
        return self._parse_dashboard_root(data)

    async def get_recent_usage(self, service_location_id: str) -> list[dict[str, Any]]:
        """Get recent 15-min interval usage data."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetRecentUsage", payload)
        return self._parse_recent_usage(data)

    async def get_grid_status(self, service_location_id: str) -> dict[str, Any]:
        """Get grid status (outage status, battery remaining hours)."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetGridStatus", payload)
        return self._parse_grid_status(data)

    async def get_usage_energy(self, service_location_id: str) -> dict[str, Any]:
        """Get energy breakdown (grid-to-home, solar-to-home kWh)."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetUsageEnergy", payload)
        return self._parse_usage_energy(data)

    async def get_billing_metadata(self, service_location_id: str) -> dict[str, Any]:
        """Get billing info (amount, due date)."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetBillingMetadata", payload)
        return self._parse_billing_metadata(data)

    async def get_wifi_metrics(self, service_location_id: str) -> dict[str, Any]:
        """Get battery WiFi connectivity metrics."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetWifiMetrics", payload)
        return self._parse_wifi_metrics(data)

    async def get_usage_cycles(self, service_location_id: str) -> dict[str, Any]:
        """Get usage cycle dates and asset ID."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetUsageCycles", payload)
        return self._parse_usage_cycles(data)

    @staticmethod
    def _parse_dashboard_root(data: bytes) -> dict[str, Any]:
        """Parse MobileGetDashboardRoot response."""
        result: dict[str, Any] = {
            "backup_seconds": 0,
            "backup_hours": 0.0,
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
                # Backup seconds remaining (varint)
                val, offset = _decode_varint(data, offset)
                result["backup_seconds"] = val
                result["backup_hours"] = round(val / 3600, 2)
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

        Response structure:
          Field 1 (repeated): visible/scanned networks { ssid (str,1), signal (int,2), is_connected (bool,3) }
          Field 2: connected network { ssid (str,1), signal (int,2) }

        We prefer field 2 (dedicated connected-network field). If absent, we fall back
        to the field-1 entry flagged is_connected=true.
        """
        result: dict[str, Any] = {"ssid": None, "signal": None, "connected": False}
        if not data or len(data) < 4:
            return result

        def _parse_network_sub(sub: bytes) -> dict[str, Any]:
            entry: dict[str, Any] = {"ssid": None, "signal": None, "is_connected": False}
            off = 0
            while off < len(sub):
                tag_val, new_off = _decode_varint(sub, off)
                if new_off == off:
                    break
                off = new_off
                field_num = tag_val >> 3
                wire_type = tag_val & 0x07
                if wire_type == 2:
                    length, off = _decode_varint(sub, off)
                    value = sub[off : off + length]
                    off += length
                    if field_num == 1:
                        try:
                            entry["ssid"] = value.decode("utf-8")
                        except Exception:
                            pass
                elif wire_type == 0:
                    val, off = _decode_varint(sub, off)
                    if field_num == 2:
                        entry["signal"] = val
                    elif field_num == 3:
                        entry["is_connected"] = bool(val)
                else:
                    off = _skip_field(sub, off, wire_type)
            return entry

        connected_entry: dict[str, Any] | None = None
        flagged_entry: dict[str, Any] | None = None

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
                    # Visible/scanned network — check for is_connected flag
                    entry = _parse_network_sub(sub)
                    if entry.get("is_connected") and flagged_entry is None:
                        flagged_entry = entry
                elif field_num == 2:
                    # Dedicated connected-network field
                    connected_entry = _parse_network_sub(sub)
            elif wire_type == 0:
                _, offset = _decode_varint(data, offset)
            else:
                offset = _skip_field(data, offset, wire_type)

        target = connected_entry or flagged_entry
        if target and target.get("ssid"):
            result["ssid"] = target["ssid"]
            result["signal"] = target["signal"]
            result["connected"] = True

        _LOGGER.debug("WifiMetrics parsed: %s", result)
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
    def _parse_grid_status(data: bytes) -> dict[str, Any]:
        """Parse MobileGetGridStatus response.

        Rich response with time-series data:
          Field 1 (repeated submsg): 15-min usage intervals {timestamp, kWh}
          Field 2 (repeated submsg): 15-min grid data {timestamp, power_value, battery_soc%}
          Field 3 (submsg): time range marker
          Field 4 (repeated submsg): hourly usage totals {timestamp, kWh}
          Field 6 (submsg): summary {total_kwh, avg_kwh}
        """
        result: dict[str, Any] = {
            "available": False,
            "grid_is_up": True,
            "battery_soc_percent": None,
            "battery_remaining_seconds": 0,
            "current_power_amps": None,
            "hourly_usage": [],
        }
        if not data:
            return result

        result["available"] = True
        _LOGGER.debug("GridStatus raw (%d bytes)", len(data))

        # Parse top-level fields
        offset = 0
        latest_soc: float | None = None
        latest_power: float | None = None
        latest_ts = 0
        hourly: list[dict[str, Any]] = []

        while offset < len(data):
            if offset >= len(data):
                break
            tag_val, new_offset = _decode_varint(data, offset)
            if new_offset == offset:
                break
            offset = new_offset
            field_num = tag_val >> 3
            wire_type = tag_val & 0x07

            if wire_type == 2:  # length-delimited (sub-message)
                length, offset = _decode_varint(data, offset)
                if offset + length > len(data):
                    break
                sub = data[offset:offset + length]
                offset += length

                if field_num == 2 and length == 18:
                    # 15-min grid data: {field1: {field1: timestamp}, field2: power, field3: soc}
                    ts, power_val, soc_val = _parse_grid_data_point(sub)
                    if ts and ts > latest_ts:
                        latest_ts = ts
                        latest_power = power_val
                        latest_soc = soc_val

                elif field_num == 4 and length == 13:
                    # Hourly usage: {field1: {field1: timestamp}, field2: kWh}
                    ts, kwh = _parse_usage_point(sub)
                    if ts:
                        hourly.append({"timestamp": ts, "kwh": round(kwh, 3)})

                elif field_num == 6:
                    # Summary: {field1: total_kwh (fixed32), field3: avg_kwh (fixed32)}
                    _parse_grid_summary(sub, result)

            elif wire_type == 0:
                val, offset = _decode_varint(data, offset)
                # Simple varint fields (fallback for simpler responses)
                if field_num == 1:
                    result["grid_is_up"] = val == 0
                elif field_num == 2:
                    result["battery_remaining_seconds"] = val
                elif field_num == 3:
                    result["battery_soc_percent"] = val
            elif wire_type == 5:
                offset += 4
            elif wire_type == 1:
                offset += 8
            else:
                break

        # Use latest time-series SoC if found
        if latest_soc is not None:
            result["battery_soc_percent"] = round(latest_soc, 1)
        if latest_power is not None:
            result["current_power_amps"] = round(latest_power, 2)
        if hourly:
            result["hourly_usage"] = hourly

        _LOGGER.debug(
            "GridStatus: soc=%.1f%%, power=%.1f, hourly_points=%d",
            result["battery_soc_percent"] or 0,
            result["current_power_amps"] or 0,
            len(hourly),
        )
        return result

    @staticmethod
    def _parse_usage_energy(data: bytes) -> dict[str, Any]:
        """Parse MobileGetUsageEnergy response.

        Expected fields (float32 or varint):
          Field 1: grid_to_home_kwh
          Field 2: solar_to_home_kwh
          Field 3: battery_to_home_kwh
        """
        result: dict[str, Any] = {
            "grid_to_home_kwh": None,
            "solar_to_home_kwh": None,
            "battery_to_home_kwh": None,
        }
        if not data:
            return result

        _LOGGER.debug("UsageEnergy raw hex: %s", data.hex())

        offset = 0
        while offset < len(data):
            if offset >= len(data):
                break
            tag = data[offset]
            field_num = tag >> 3
            wire_type = tag & 0x07
            offset += 1

            if wire_type == 5:  # 32-bit (float)
                if offset + 4 <= len(data):
                    val = struct.unpack("<f", data[offset : offset + 4])[0]
                    if field_num == 1:
                        result["grid_to_home_kwh"] = round(val, 3)
                    elif field_num == 2:
                        result["solar_to_home_kwh"] = round(val, 3)
                    elif field_num == 3:
                        result["battery_to_home_kwh"] = round(val, 3)
                offset += 4
            elif wire_type == 0:
                _, offset = _decode_varint(data, offset)
            else:
                offset = _skip_field(data, offset, wire_type)

        _LOGGER.debug("UsageEnergy parsed: %s", result)
        return result

    @staticmethod
    def _parse_billing_metadata(data: bytes) -> dict[str, Any]:
        """Parse MobileGetBillingMetadata response.

        Expected fields:
          Field 1 (sub-message or string): plan_name
          Field 2 (varint): amount_cents
          Field 3 (string): due_date (ISO string or similar)
        """
        result: dict[str, Any] = {
            "amount_cents": None,
            "due_date": None,
        }
        if not data:
            return result

        _LOGGER.debug("BillingMetadata raw hex: %s", data.hex())

        offset = 0
        while offset < len(data):
            if offset >= len(data):
                break
            tag = data[offset]
            field_num = tag >> 3
            wire_type = tag & 0x07
            offset += 1

            if wire_type == 0:  # varint
                val, offset = _decode_varint(data, offset)
                if field_num == 2:
                    result["amount_cents"] = val
            elif wire_type == 2:  # length-delimited (string)
                str_len, offset = _decode_varint(data, offset)
                str_data = data[offset : offset + str_len]
                offset += str_len
                if field_num == 3:
                    try:
                        result["due_date"] = str_data.decode("utf-8")
                    except (UnicodeDecodeError, ValueError):
                        pass
            else:
                offset = _skip_field(data, offset, wire_type)

        _LOGGER.debug("BillingMetadata parsed: %s", result)
        return result
