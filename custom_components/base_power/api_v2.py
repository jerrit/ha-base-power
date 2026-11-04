"""Parsers for the dashboard.mobile.v2 services.

Base's newer app calls these services. They sit next to the older
dashboard.DashboardAPI methods on the same host and take the same JWT.
Field numbers come from the server's gRPC reflection (November 2026).

Proto3 leaves out fields that hold zero. A number missing from a message
that is present is 0, not unknown.
"""

from __future__ import annotations

import logging
from typing import Any

from .api import _encode_varint, _iter_fields, _timestamp_seconds

_LOGGER = logging.getLogger(__name__)

V2_PACKAGE = "dashboard.mobile.v2"

# BatterySnapshot oneof: field number → state name
BATTERY_STATES: dict[int, str] = {
    2: "telemetry_unavailable",
    3: "on_grid",
    4: "outage",
    5: "no_home_power",
    6: "overcurrent",
    7: "overcurrent_standby",
}
OFF_GRID_STATES = frozenset({"outage", "no_home_power", "overcurrent", "overcurrent_standby"})

# Field layout of each state message: field number → key
_STATE_FIELDS: dict[str, dict[int, str]] = {
    "on_grid": {1: "observed_at", 2: "power_flow", 3: "backup_hours", 4: "backup_hours_750w"},
    "outage": {
        1: "observed_at", 2: "state_of_energy_percent", 3: "power_flow",
        4: "backup_hours", 5: "backup_hours_750w",
    },
    "no_home_power": {
        1: "observed_at", 2: "state_of_energy_percent", 3: "backup_hours", 4: "backup_hours_750w",
    },
    "overcurrent": {
        1: "observed_at", 2: "state_of_energy_percent", 3: "power_flow",
        4: "backup_hours", 5: "backup_hours_750w", 6: "overcurrent_limit_kw",
    },
    "overcurrent_standby": {
        1: "observed_at", 2: "state_of_energy_percent", 3: "backup_hours",
        4: "backup_hours_750w", 5: "overcurrent_limit_kw",
    },
    "telemetry_unavailable": {},
}
_POWER_FLOW_FIELDS = {
    1: "from_grid_kw",
    2: "from_storage_kw",
    3: "from_solar_kw",
    4: "non_solar_to_home_kw",
    5: "to_home_kw",
}
_NUMBER_KEYS = ("backup_hours", "backup_hours_750w", "overcurrent_limit_kw")

ACCOUNT_TYPE_ENERGY = 1


def encode_address_request(address_id: str) -> bytes:
    """Field 1 is the address id string."""
    encoded = address_id.encode("utf-8")
    return b"\x0a" + _encode_varint(len(encoded)) + encoded


def encode_billing_cycle_request(address_id: str, account_type: int = ACCOUNT_TYPE_ENERGY) -> bytes:
    """GetCurrentBillingCycleRequest: field 1 is AccountReference {1 address, 2 type}."""
    reference = encode_address_request(address_id) + b"\x10" + _encode_varint(account_type)
    return b"\x0a" + _encode_varint(len(reference)) + reference


def _int64(value: int) -> int:
    """A negative int64 arrives as a 64-bit two's-complement varint."""
    return value - (1 << 64) if value >= 1 << 63 else value


def empty_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "state": None,
        "observed_at": None,
        "state_of_energy_percent": None,
        "backup_hours": None,
        "backup_hours_750w": None,
        "overcurrent_limit_kw": None,
        "power_flow": None,
    }


def parse_battery_snapshot(data: bytes) -> dict[str, Any]:
    """Parse GetBatterySnapshotResponse. The state is which oneof field is set."""
    result = empty_snapshot()
    snapshot = next((v for f, k, v in _iter_fields(data) if f == 1 and k == "bytes"), None)
    if snapshot is None:
        return result
    for field_num, kind, value in _iter_fields(snapshot):
        state = BATTERY_STATES.get(field_num)
        if state is None or kind != "bytes":
            continue
        layout = _STATE_FIELDS[state]
        result["available"] = True
        result["state"] = state
        for key in _NUMBER_KEYS:
            if key in layout.values():
                result[key] = 0.0
        if "state_of_energy_percent" in layout.values():
            result["state_of_energy_percent"] = 0
        if "power_flow" in layout.values():
            result["power_flow"] = {key: 0.0 for key in _POWER_FLOW_FIELDS.values()}
        for sub_num, sub_kind, sub_value in _iter_fields(value):
            key = layout.get(sub_num)
            if key == "observed_at" and sub_kind == "bytes":
                result["observed_at"] = _timestamp_seconds(sub_value)
            elif key == "state_of_energy_percent" and sub_kind == "varint":
                result[key] = _int64(sub_value)
            elif key in _NUMBER_KEYS and sub_kind == "double":
                result[key] = round(sub_value, 2)
            elif key == "power_flow" and sub_kind == "bytes":
                for flow_num, flow_kind, flow_value in _iter_fields(sub_value):
                    name = _POWER_FLOW_FIELDS.get(flow_num)
                    if name and flow_kind == "double":
                        result["power_flow"][name] = round(flow_value, 3)
    _LOGGER.debug("BatterySnapshot parsed: %s", result)
    return result


def empty_overview() -> dict[str, Any]:
    return {
        "available": False,
        "energy_mix_24h": None,
        "grid_support": [],
        "cost_today_cents": None,
        "cost_currency": None,
    }


def parse_daily_overview(data: bytes, start: int, end: int) -> dict[str, Any]:
    """Parse GetDailyOverviewResponse.

    The reply is a rolling 24 hours. energy_mix_24h covers that whole window,
    not the local day. cost_today_cents sums hourly grid cost in [start, end).
    """
    result = empty_overview()
    if not data:
        return result
    cost_total = 0.0
    cost_seen = False
    for field_num, kind, value in _iter_fields(data):
        if kind != "bytes":
            continue
        result["available"] = True
        if field_num == 3:
            interval: dict[str, int | None] = {"start": None, "end": None}
            for sub_num, sub_kind, sub_value in _iter_fields(value):
                if sub_kind == "bytes" and sub_num in (1, 2):
                    interval["start" if sub_num == 1 else "end"] = _timestamp_seconds(sub_value)
            if interval["start"] is not None:
                result["grid_support"].append(interval)
        elif field_num == 5:
            stamp = None
            cents = 0.0
            for sub_num, sub_kind, sub_value in _iter_fields(value):
                if sub_num == 1 and sub_kind == "bytes":
                    stamp = _timestamp_seconds(sub_value)
                elif sub_num == 2 and sub_kind == "double":
                    cents = sub_value
            if stamp is not None and start <= stamp < end:
                cost_total += cents
                cost_seen = True
        elif field_num == 6:
            result["cost_currency"] = value.decode("utf-8", errors="replace") or None
        elif field_num == 7:
            mix = {"grid_to_home_kwh": 0.0, "solar_to_home_kwh": 0.0, "battery_to_home_kwh": 0.0}
            names = {1: "grid_to_home_kwh", 2: "solar_to_home_kwh", 3: "battery_to_home_kwh"}
            for sub_num, sub_kind, sub_value in _iter_fields(value):
                if sub_num in names and sub_kind == "double":
                    mix[names[sub_num]] = round(sub_value, 3)
            result["energy_mix_24h"] = mix
    if cost_seen:
        result["cost_today_cents"] = round(cost_total, 2)
    _LOGGER.debug(
        "DailyOverview parsed: mix=%s grid_support=%d cost_today=%s",
        result["energy_mix_24h"],
        len(result["grid_support"]),
        result["cost_today_cents"],
    )
    return result


def empty_billing_cycle() -> dict[str, Any]:
    return {
        "currency": None,
        "service_period_start": None,
        "data_through": None,
        "gross_charges_cents": None,
        "solar_credit_cents": None,
        "net_accrued_cents": None,
        "projected_charges_cents": None,
    }


def parse_current_billing_cycle(data: bytes) -> dict[str, Any]:
    """Parse GetCurrentBillingCycleResponse. Empty on accounts without Base billing."""
    result = empty_billing_cycle()
    cycle = next((v for f, k, v in _iter_fields(data) if f == 1 and k == "bytes"), None)
    if cycle is None:
        return result
    amounts = {
        5: "gross_charges_cents",
        6: "solar_credit_cents",
        7: "net_accrued_cents",
        8: "projected_charges_cents",
    }
    for key in amounts.values():
        result[key] = 0
    for field_num, kind, value in _iter_fields(cycle):
        if field_num == 1 and kind == "bytes":
            result["currency"] = value.decode("utf-8", errors="replace") or None
        elif field_num == 2 and kind == "bytes":
            result["service_period_start"] = _timestamp_seconds(value)
        elif field_num == 4 and kind == "bytes":
            result["data_through"] = _timestamp_seconds(value)
        elif field_num in amounts and kind == "varint":
            result[amounts[field_num]] = _int64(value)
    _LOGGER.debug("CurrentBillingCycle parsed: %s", result)
    return result


def grid_support_active(intervals: list[dict[str, int | None]], now: int) -> bool:
    """True when now is inside an interval. A missing end means it is still running."""
    for interval in intervals:
        start = interval.get("start")
        end = interval.get("end")
        if start is not None and start <= now and (end is None or now < end):
            return True
    return False
