"""Base Power gRPC-Web API client.

All encoding/decoding is done manually to avoid a protobuf library dependency.
The gRPC-Web wire format is: 0x00 (data flag) + 4-byte BE length + protobuf payload.
"""
from __future__ import annotations

import logging
import struct

import aiohttp

from .auth import ClerkAuth, ClerkAuthError
from .const import BASE_API_URL, BATTERY_STATUS, SERVICE_STATE

_LOGGER = logging.getLogger(__name__)


class BasePowerAPIError(Exception):
    """Raised when an API call fails."""


# ---------------------------------------------------------------------------
# Protobuf helper — minimal varint / field reader
# ---------------------------------------------------------------------------

def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a protobuf varint starting at offset. Returns (value, new_offset)."""
    value = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            break
    return value, offset


def _encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a protobuf length-delimited string field."""
    encoded = value.encode("utf-8")
    tag = (field_number << 3) | 2  # wire type 2 = length-delimited
    return bytes([tag, len(encoded)]) + encoded


def _wrap_grpc(proto_bytes: bytes) -> bytes:
    """Wrap protobuf bytes in a gRPC-Web data frame."""
    return b"\x00" + struct.pack(">I", len(proto_bytes)) + proto_bytes


def _unwrap_grpc(response: bytes) -> bytes:
    """Extract the protobuf payload from a gRPC-Web response.

    A response may contain multiple frames; we collect all data frames
    (flag byte 0x00) and ignore trailer frames (0x80).
    """
    result = bytearray()
    offset = 0
    while offset + 5 <= len(response):
        flag = response[offset]
        length = struct.unpack(">I", response[offset + 1 : offset + 5])[0]
        offset += 5
        if flag == 0x00 and offset + length <= len(response):
            result.extend(response[offset : offset + length])
        offset += length
    return bytes(result)


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

EMPTY_REQUEST = b"\x00\x00\x00\x00\x00"  # gRPC-Web frame for an empty proto


class BasePowerAPI:
    """Async client for the Base Power gRPC-Web API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: ClerkAuth,
        service_location_id: str,
    ) -> None:
        self._session = session
        self._auth = auth
        self._service_location_id = service_location_id

    # ------------------------------------------------------------------
    # Low-level transport
    # ------------------------------------------------------------------

    def _location_payload(self) -> bytes:
        """Build the protobuf request body for endpoints that take service_location_id."""
        return _encode_string_field(1, self._service_location_id)

    async def _call(self, method: str, payload: bytes = b"") -> bytes:
        """Execute a gRPC-Web POST. Returns the raw protobuf response bytes."""
        jwt = await self._auth.get_valid_jwt()
        url = f"{BASE_API_URL}/{method}"
        headers = {
            "Content-Type": "application/grpc-web+proto",
            "authorization": jwt,   # NO "Bearer" prefix — Base Power API requires raw JWT
            "x-grpc-web": "1",
        }
        body = _wrap_grpc(payload)

        try:
            async with self._session.post(url, headers=headers, data=body) as resp:
                if resp.status != 200:
                    raise BasePowerAPIError(
                        f"HTTP {resp.status} calling {method}"
                    )
                raw = await resp.read()
                return _unwrap_grpc(raw)
        except aiohttp.ClientError as err:
            raise BasePowerAPIError(f"Network error calling {method}: {err}") from err

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_user(self) -> dict:
        """MobileGetUser — returns user profile."""
        raw = await self._call("MobileGetUser")
        return _parse_user(raw)

    async def get_service_locations(self) -> list[dict]:
        """MobileGetDashboardRoots — returns all service locations."""
        raw = await self._call("MobileGetDashboardRoots")
        return _parse_dashboard_roots(raw)

    async def get_dashboard_root(self) -> dict:
        """MobileGetDashboardRoot — primary data source for battery/energy status."""
        raw = await self._call("MobileGetDashboardRoot", self._location_payload())
        return _parse_dashboard_root(raw)

    async def get_grid_status(self) -> dict:
        """MobileGetGridStatus — outage / grid connection status."""
        raw = await self._call("MobileGetGridStatus", self._location_payload())
        return _parse_grid_status(raw)

    async def get_recent_usage(self) -> dict:
        """MobileGetRecentUsage — 15-min interval energy readings for today."""
        raw = await self._call("MobileGetRecentUsage", self._location_payload())
        return _parse_recent_usage(raw)

    async def get_billing_accounts(self) -> dict:
        """MobileGetBillingAccounts — billing amounts and account status."""
        raw = await self._call("MobileGetBillingAccounts", self._location_payload())
        return _parse_billing(raw)

    async def get_usage_cycles(self) -> dict:
        """MobileGetUsageCycles — billing cycle dates and asset ID."""
        raw = await self._call("MobileGetUsageCycles", self._location_payload())
        return _parse_usage_cycles(raw)

    async def send_manual_backup(self) -> None:
        """MobileSendManualBackupCommand — trigger battery backup mode."""
        await self._call("MobileSendManualBackupCommand", self._location_payload())

    async def send_overcurrent_reset(self) -> None:
        """MobileSendOverCurrentCommand — reset overcurrent protection."""
        await self._call("MobileSendOverCurrentCommand", self._location_payload())


# ---------------------------------------------------------------------------
# Protobuf response parsers
# ---------------------------------------------------------------------------

def _parse_user(data: bytes) -> dict:
    result: dict = {}
    offset = 0
    field_map = {2: "email", 3: "first_name", 4: "last_name", 5: "phone", 6: "language"}
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 0:
                value, offset = _read_varint(data, offset)
                if field == 1:
                    result["user_id"] = value
            elif wire == 2:
                length, offset = _read_varint(data, offset)
                raw = data[offset : offset + length]
                offset += length
                if field in field_map:
                    result[field_map[field]] = raw.decode("utf-8", errors="replace")
        except Exception:
            break
    return result


def _parse_dashboard_roots(data: bytes) -> list[dict]:
    locations: list[dict] = []
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 2:
                length, offset = _read_varint(data, offset)
                sub = data[offset : offset + length]
                offset += length
                if field == 1:
                    loc = _parse_location_entry(sub)
                    if loc.get("service_location_id"):
                        locations.append(loc)
        except Exception:
            break
    return locations


def _parse_location_entry(data: bytes) -> dict:
    result: dict = {}
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 2:
                length, offset = _read_varint(data, offset)
                raw = data[offset : offset + length]
                offset += length
                if field == 1:
                    result["service_location_id"] = raw.decode("utf-8", errors="replace")
                elif field == 2:
                    result["address"] = _parse_address(raw)
        except Exception:
            break
    return result


def _parse_address(data: bytes) -> dict:
    result: dict = {}
    field_map = {1: "line1", 2: "line2", 3: "city", 4: "state", 5: "postal_code", 6: "country", 8: "timezone"}
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 2:
                length, offset = _read_varint(data, offset)
                raw = data[offset : offset + length]
                offset += length
                if field in field_map:
                    result[field_map[field]] = raw.decode("utf-8", errors="replace")
            elif wire == 0:
                _, offset = _read_varint(data, offset)
            elif wire == 5:
                offset += 4
            elif wire == 1:
                offset += 8
        except Exception:
            break
    return result


def _parse_dashboard_root(data: bytes) -> dict:
    result: dict = {
        "battery_status": 0,
        "battery_status_name": BATTERY_STATUS.get(0, "unknown"),
        "service_state": 0,
        "service_state_name": SERVICE_STATE.get(0, "unknown"),
        "has_solar": False,
        "has_automatic_backup": False,
        "grid_to_home_kwh": None,
        "solar_to_home_kwh": None,
        "energy_to_home_kwh": None,
        "address": {},
    }
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 2:
                length, offset = _read_varint(data, offset)
                sub = data[offset : offset + length]
                offset += length
                if field == 2:
                    result["address"] = _parse_address(sub)
                elif field == 3:
                    _parse_battery_sub(sub, result)
                elif field == 4:
                    _parse_energy_sub(sub, result)
            elif wire == 0:
                _, offset = _read_varint(data, offset)
            elif wire == 5:
                offset += 4
            elif wire == 1:
                offset += 8
        except Exception:
            break
    return result


def _parse_battery_sub(data: bytes, result: dict) -> None:
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 0:
                value, offset = _read_varint(data, offset)
                if field == 1:
                    result["battery_status"] = value
                    result["battery_status_name"] = BATTERY_STATUS.get(value, "unknown")
                elif field == 2:
                    result["service_state"] = value
                    result["service_state_name"] = SERVICE_STATE.get(value, "unknown")
                elif field == 4:
                    result["has_solar"] = bool(value)
                elif field == 5:
                    result["has_automatic_backup"] = bool(value)
            elif wire == 2:
                length, offset = _read_varint(data, offset)
                offset += length
            elif wire == 5:
                offset += 4
        except Exception:
            break


def _parse_energy_sub(data: bytes, result: dict) -> None:
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 5:  # float32, little-endian
                value = struct.unpack("<f", data[offset : offset + 4])[0]
                offset += 4
                if field == 1:
                    result["grid_to_home_kwh"] = round(value, 3)
                elif field == 2:
                    result["solar_to_home_kwh"] = round(value, 3)
                elif field == 3:
                    result["energy_to_home_kwh"] = round(value, 3)
            elif wire == 0:
                _, offset = _read_varint(data, offset)
            elif wire == 2:
                length, offset = _read_varint(data, offset)
                offset += length
            elif wire == 1:
                offset += 8
        except Exception:
            break


def _parse_recent_usage(data: bytes) -> dict:
    points: list[dict] = []
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 2:
                length, offset = _read_varint(data, offset)
                sub = data[offset : offset + length]
                offset += length
                if field == 1:
                    point = _parse_usage_point(sub)
                    if point is not None:
                        points.append(point)
            elif wire == 0:
                _, offset = _read_varint(data, offset)
        except Exception:
            break

    if not points:
        return {
            "data_points": [],
            "current_interval_kwh": 0.0,
            "current_power_watts": 0,
            "daily_total_kwh": 0.0,
            "daily_peak_kwh": 0.0,
            "daily_low_kwh": 0.0,
            "intervals_today": 0,
        }

    values = [p["kwh"] for p in points]
    return {
        "data_points": points,
        "current_interval_kwh": values[-1],
        "current_power_watts": round(values[-1] * 4 * 1000),  # kWh/15min → Watts
        "daily_total_kwh": round(sum(values), 3),
        "daily_peak_kwh": max(values),
        "daily_low_kwh": min(values),
        "intervals_today": len(values),
    }


def _parse_usage_point(data: bytes) -> dict | None:
    timestamp = None
    kwh = None
    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 2 and field == 1:  # Timestamp message
                length, offset = _read_varint(data, offset)
                ts_data = data[offset : offset + length]
                offset += length
                # Timestamp.seconds is field 1 (varint) inside the message
                if ts_data and ts_data[0] == 0x08:
                    ts, _ = _read_varint(ts_data, 1)
                    timestamp = ts
            elif wire == 5 and field == 2:  # float32 kWh value
                kwh = round(struct.unpack("<f", data[offset : offset + 4])[0], 3)
                offset += 4
            elif wire == 0:
                _, offset = _read_varint(data, offset)
            elif wire == 2:
                length, offset = _read_varint(data, offset)
                offset += length
        except Exception:
            break

    if kwh is not None:
        return {"timestamp": timestamp, "kwh": kwh}
    return None


def _parse_grid_status(data: bytes) -> dict:
    result = {
        "grid_connected": True,
        "outage_active": False,
        "backup_active": False,
    }
    if not data:
        return result

    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 0:
                value, offset = _read_varint(data, offset)
                if field == 1:
                    result["grid_connected"] = value == 1
                elif field == 2:
                    result["outage_active"] = value == 1
                elif field == 3:
                    result["backup_active"] = value == 1
            elif wire == 2:
                length, offset = _read_varint(data, offset)
                offset += length
            elif wire == 5:
                offset += 4
            elif wire == 1:
                offset += 8
        except Exception:
            break
    return result


def _parse_billing(data: bytes) -> dict:
    result: dict = {
        "total_amount_cents": None,
        "monthly_fee_cents": None,
        "account_status": 0,
        "solar_buyback_rate_cents": None,
    }
    if not data:
        return result

    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 0:
                value, offset = _read_varint(data, offset)
                if field == 1:
                    result["total_amount_cents"] = value
                elif field == 2:
                    result["monthly_fee_cents"] = value
                elif field == 3:
                    result["account_status"] = value
                elif field == 10:
                    result["solar_buyback_rate_cents"] = value
            elif wire == 2:
                length, offset = _read_varint(data, offset)
                offset += length
            elif wire == 5:
                offset += 4
            elif wire == 1:
                offset += 8
        except Exception:
            break
    return result


def _parse_usage_cycles(data: bytes) -> dict:
    result: dict = {"asset_id": None, "cycles": []}
    if not data:
        return result

    offset = 0
    while offset < len(data):
        try:
            tag, offset = _read_varint(data, offset)
            field = tag >> 3
            wire = tag & 0x7
            if wire == 2:
                length, offset = _read_varint(data, offset)
                raw = data[offset : offset + length]
                offset += length
                if field == 2:  # asset_id string at root level
                    result["asset_id"] = raw.decode("utf-8", errors="replace")
            elif wire == 0:
                _, offset = _read_varint(data, offset)
        except Exception:
            break
    return result
