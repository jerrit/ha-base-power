"""Parsers for the dashboard.mobile.v2 services.

Base's newer app calls these services. They sit next to the older
dashboard.DashboardAPI methods on the same host and take the same JWT.
Field numbers come from the server's gRPC reflection (November 2026).

Proto3 leaves out fields that hold zero. A number missing from a message
that is present is 0, not unknown.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime
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
ACCOUNT_TYPE_BATTERY = 2


def encode_address_request(address_id: str) -> bytes:
    """Field 1 is the address id string."""
    encoded = address_id.encode("utf-8")
    return b"\x0a" + _encode_varint(len(encoded)) + encoded


def encode_billing_cycle_request(address_id: str, account_type: int = ACCOUNT_TYPE_ENERGY) -> bytes:
    """Field 1 is AccountReference {1 address, 2 type}.

    GetCurrentBillingCycleRequest and GetAccountRequest share this shape.
    """
    reference = encode_address_request(address_id) + b"\x10" + _encode_varint(account_type)
    return b"\x0a" + _encode_varint(len(reference)) + reference


def _int64(value: int) -> int:
    """A negative int64 arrives as a 64-bit two's-complement varint."""
    return value - (1 << 64) if value >= 1 << 63 else value


def parse_locations(data: bytes) -> list[dict[str, str]]:
    """Parse ListLocationsResponse into address ids with a short label.

    locations (1, repeated LocationSummary): 1 address_id, 2 DisplayAddress
    {1 line_1, 3 city}, 3 status. The label is for the setup picker only.
    """
    locations: list[dict[str, str]] = []
    for field_num, kind, summary in _iter_fields(data):
        if field_num != 1 or kind != "bytes":
            continue
        address_id = ""
        parts: dict[int, str] = {}
        for sub_num, sub_kind, value in _iter_fields(summary):
            if sub_num == 1 and sub_kind == "bytes":
                address_id = value.decode("utf-8", "replace").strip()
            elif sub_num == 2 and sub_kind == "bytes":
                for addr_num, addr_kind, text in _iter_fields(value):
                    if addr_num in (1, 3) and addr_kind == "bytes":
                        parts[addr_num] = text.decode("utf-8", "replace").strip()
        if address_id:
            label = ", ".join(p for p in (parts.get(1), parts.get(3)) if p)
            locations.append({"address_id": address_id, "label": label or address_id})
    return locations


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
        "cost_24h_cents": None,
        "cost_currency": None,
        "home_power": [],
        "energy_today": None,
    }


def _sample(value: bytes) -> tuple[int | None, dict[int, float]]:
    """Read a sample message: field 1 Timestamp, other fields doubles."""
    stamp: int | None = None
    numbers: dict[int, float] = {}
    for sub_num, sub_kind, sub_value in _iter_fields(value):
        if sub_num == 1 and sub_kind == "bytes":
            stamp = _timestamp_seconds(sub_value)
        elif sub_kind == "double":
            numbers[sub_num] = sub_value
    return stamp, numbers


def parse_daily_overview(data: bytes, start: int, end: int) -> dict[str, Any]:
    """Parse GetDailyOverviewResponse.

    The reply is a rolling 24 hours. energy_mix_24h covers that whole window,
    not the local day. cost_today_cents sums hourly grid cost in [start, end). cost_24h_cents
    sums every hour in the reply, the window the app's daily overview shows.

    home_power has the same shape as MobileGetRecentUsage: 15-minute average
    kW as kwh (kW / 4) and watts (kW * 1000). energy_today sums the hourly
    samples in [start, end), the same as MobileGetUsageEnergy. Its
    grid_to_home_kwh is home load, like the older call's "load" field.
    """
    result = empty_overview()
    if not data:
        return result
    cost_total = 0.0
    cost_seen = False
    cost_24h = 0.0
    cost_24h_seen = False
    home_kwh = 0.0
    solar_kwh = 0.0
    hours_today = 0
    for field_num, kind, value in _iter_fields(data):
        if kind != "bytes":
            continue
        result["available"] = True
        if field_num == 1:
            stamp, numbers = _sample(value)
            kw = numbers.get(2, 0.0)
            if stamp is not None and math.isfinite(kw):
                result["home_power"].append(
                    {"timestamp": stamp, "kwh": round(kw / 4, 3), "watts": round(kw * 1000)}
                )
        elif field_num == 4:
            stamp, numbers = _sample(value)
            home = numbers.get(2, 0.0)
            solar = numbers.get(3, 0.0)
            # A non-finite sample is dropped, so a bad hour cannot publish NaN.
            if (
                stamp is not None
                and start <= stamp < end
                and math.isfinite(home)
                and math.isfinite(solar)
            ):
                home_kwh += home
                solar_kwh += solar
                hours_today += 1
        elif field_num == 3:
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
            if stamp is None or not math.isfinite(cents):
                continue
            # The reply is the last 24 hours, the same window as the app.
            cost_24h += cents
            cost_24h_seen = True
            if start <= stamp < end:
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
    if cost_24h_seen:
        result["cost_24h_cents"] = round(cost_24h, 2)
    if hours_today:
        result["energy_today"] = {
            "grid_to_home_kwh": round(home_kwh, 3),
            "solar_to_home_kwh": round(solar_kwh, 3),
            "battery_to_home_kwh": None,
        }
    result["home_power"].sort(key=lambda point: point["timestamp"])
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


# Battery output, in kW, that counts as grid support while the grid is up.
GRID_SUPPORT_STORAGE_KW = 0.2


def live_grid_support(grid_is_up: bool | None, storage_kw: float | None) -> bool | None:
    """True when the battery powers the home while the grid is up.

    On grid, Base runs the battery only for grid support. During an outage
    the battery also powers the home, so a down or unknown grid gives None.
    The threshold skips small readings that are noise, not an event.
    """
    if grid_is_up is not True:
        return None
    if isinstance(storage_kw, bool) or not isinstance(storage_kw, (int, float)):
        return None
    if not math.isfinite(storage_kw):
        return None
    return storage_kw >= GRID_SUPPORT_STORAGE_KW


def parse_recent_grid_voltage(data: bytes) -> dict[str, Any]:
    """Parse GetRecentGridVoltageResponse into the MobileGetGridStatus shape.

    Field 1 repeats {1 observed_at, 2 voltage_v}. The latest sample wins.
    """
    result: dict[str, Any] = {
        "available": False,
        "has_gateway": None,
        "voltage_volts": None,
        "voltage_samples": 0,
    }
    samples: list[tuple[int, float]] = []
    for field_num, kind, value in _iter_fields(data):
        if field_num == 1 and kind == "bytes":
            stamp, numbers = _sample(value)
            volts = numbers.get(2, 0.0)
            if stamp is not None and math.isfinite(volts):
                samples.append((stamp, volts))
    if samples:
        result["available"] = True
        result["voltage_volts"] = max(samples)[1]
        result["voltage_samples"] = len(samples)
    return result


def empty_battery_account() -> dict[str, Any]:
    return {"plan_name": None, "hardware_sku": None, "equipment_description": None}


def parse_battery_account(data: bytes) -> dict[str, Any]:
    """Parse GetAccountResponse for the battery account. Reads only the plan.

    account (1) → plan (2) → display_name (1), battery (3) → equipment (1,
    repeated) → description (1), hardware_sku (2). The reply also has
    payment details, which are skipped and never logged.
    """
    result = empty_battery_account()
    account = next((v for f, k, v in _iter_fields(data) if f == 1 and k == "bytes"), None)
    plan = None
    if account is not None:
        plan = next((v for f, k, v in _iter_fields(account) if f == 2 and k == "bytes"), None)
    if plan is None:
        return result
    for field_num, kind, value in _iter_fields(plan):
        if field_num == 1 and kind == "bytes":
            result["plan_name"] = value.decode("utf-8", errors="replace") or None
        elif field_num == 3 and kind == "bytes":
            for sub_num, sub_kind, sub_value in _iter_fields(value):
                if sub_num == 1 and sub_kind == "bytes" and result["hardware_sku"] is None:
                    for eq_num, eq_kind, eq_value in _iter_fields(sub_value):
                        text = eq_value.decode("utf-8", errors="replace") if eq_kind == "bytes" else None
                        if eq_num == 1 and text:
                            result["equipment_description"] = text
                        elif eq_num == 2 and text:
                            result["hardware_sku"] = text
    _LOGGER.debug("Battery account plan=%s sku=%s", result["plan_name"], result["hardware_sku"])
    return result


def empty_energy_account() -> dict[str, Any]:
    return {
        "due_at": None,
        "amount_due_cents": None,
        "past_due": None,
        "outstanding_balance_cents": None,
        "next_bill_date": None,
        "next_autopay_date": None,
    }


def parse_energy_account(data: bytes) -> dict[str, Any]:
    """Parse ListAccountsResponse and read the energy account's summary.

    accounts (1, repeated AccountSummary): 1 account_type, 5 outstanding
    balance cents, 7 next autopay date, 8 next bill date, 10 oldest open
    obligation {3 original amount cents, 4 due_at, 7 past_due}. Timestamps
    are UTC seconds. All None when there is no energy account, such as a
    home not billed by Base for energy. No open obligation means nothing is
    owed, so due_at stays None.
    """
    result = empty_energy_account()
    for field_num, kind, summary in _iter_fields(data):
        if field_num != 1 or kind != "bytes":
            continue
        fields = list(_iter_fields(summary))
        account_type = next((v for f, k, v in fields if f == 1 and k == "varint"), 0)
        if account_type != ACCOUNT_TYPE_ENERGY:
            continue
        result["outstanding_balance_cents"] = 0
        result["past_due"] = False
        for sub_num, sub_kind, value in fields:
            if sub_num == 5 and sub_kind == "varint":
                result["outstanding_balance_cents"] = _int64(value)
            elif sub_num == 7 and sub_kind == "bytes":
                result["next_autopay_date"] = _timestamp_seconds(value)
            elif sub_num == 8 and sub_kind == "bytes":
                result["next_bill_date"] = _timestamp_seconds(value)
            elif sub_num == 10 and sub_kind == "bytes":
                for ob_num, ob_kind, ob_value in _iter_fields(value):
                    if ob_num == 3 and ob_kind == "varint":
                        result["amount_due_cents"] = _int64(ob_value)
                    elif ob_num == 4 and ob_kind == "bytes":
                        result["due_at"] = _timestamp_seconds(ob_value)
                    elif ob_num == 7 and ob_kind == "varint":
                        result["past_due"] = bool(ob_value)
        break
    _LOGGER.debug(
        "Energy account due_at=%s past_due=%s next_bill=%s",
        result["due_at"],
        result["past_due"],
        result["next_bill_date"],
    )
    return result


def local_due_date(seconds: int | None, tz: Any) -> date | None:
    """Due date as a calendar day.

    A timestamp at exactly midnight UTC is a date with no time, so its UTC
    day is the due day. Converting it to US time would show the day before.
    Any other timestamp uses the home's local day.
    """
    if seconds is None:
        return None
    moment = datetime.fromtimestamp(seconds, UTC)
    if seconds % 86400 == 0:
        return moment.date()
    return moment.astimezone(tz).date()
