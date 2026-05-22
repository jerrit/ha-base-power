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


class BasePowerApiClient:
    """Client for the Base Power gRPC-Web API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the API client."""
        self._session = session
        self._jwt: str | None = None

    def set_jwt(self, jwt: str) -> None:
        """Set the current JWT for API calls."""
        self._jwt = jwt

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
            return _parse_grpc_frame(response_data)

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
        """Get grid status (battery SoC, power flow - pending telemetry)."""
        payload = _encode_service_location_request(service_location_id)
        data = await self._call("MobileGetGridStatus", payload)
        if not data:
            return {"available": False}
        return {"available": True, "raw": data}

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
        while offset < len(data):
            tag = data[offset]
            field_num = tag >> 3
            wire_type = tag & 0x07
            offset += 1

            if field_num == 7 and wire_type == 0:
                # Backup seconds remaining (varint)
                val, offset = _decode_varint(data, offset)
                result["backup_seconds"] = val
                result["backup_hours"] = round(val / 3600, 2)
            elif field_num == 3 and wire_type == 2:
                # Status sub-message
                msg_len, offset = _decode_varint(data, offset)
                end = offset + msg_len
                sub_fields: dict[int, int] = {}
                while offset < end:
                    inner_tag = data[offset]
                    inner_field = inner_tag >> 3
                    inner_wire = inner_tag & 0x07
                    offset += 1
                    if inner_wire == 0:
                        inner_val, offset = _decode_varint(data, offset)
                        sub_fields[inner_field] = inner_val
                    else:
                        offset = _skip_field(data, offset, inner_wire)
                _LOGGER.debug("DashboardRoot status sub-fields: %s", sub_fields)
                result["battery_count"] = sub_fields.get(1, 0)
                result["battery_status"] = sub_fields.get(2, 0)
                result["has_solar"] = bool(sub_fields.get(4, 0))
            else:
                offset = _skip_field(data, offset, wire_type)

        # battery_count of 0 means unknown; default to 1
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
        """Parse MobileGetWifiMetrics response."""
        result: dict[str, Any] = {"ssid": None, "signal": None, "connected": False}
        if not data or len(data) < 4:
            return result

        if data[0] == 0x0A:
            inner_len = data[1]
            inner = data[2 : 2 + inner_len]
            if inner[0] == 0x0A:
                ssid_len = inner[1]
                result["ssid"] = inner[2 : 2 + ssid_len].decode("utf-8")
                sig_offset = 2 + ssid_len
                if sig_offset < len(inner) and inner[sig_offset] == 0x10:
                    result["signal"] = inner[sig_offset + 1]
            result["connected"] = True

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
