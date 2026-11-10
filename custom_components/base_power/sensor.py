"""Sensor entities for Base Power integration.

Every sensor is one row in SENSORS: what it is called, its unit, and a
function that reads its value from the coordinator data.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import daily_grid_total_kwh
from .api_v2 import BATTERY_STATES, local_due_date
from .const import DOMAIN
from .coordinator import BasePowerCoordinator
from .entity import BasePowerEntity

Data = dict[str, Any]

# Base bills in US dollars.
CURRENCY = "USD"

# MobileGetDashboardRoot battery.status
BATTERY_STATUS_MAP = {
    0: "Unknown",
    1: "Not Installed",
    2: "Installed",
    3: "In Service",
    4: "Updating Firmware",
    5: "No Install Planned",
}


@dataclass(frozen=True, kw_only=True)
class BasePowerSensorDescription(SensorEntityDescription):
    """One Base Power sensor. ``key`` is the end of its unique id."""

    value_fn: Callable[[Data], Any]
    attributes_fn: Callable[[Data], dict[str, Any]] | None = None
    # Fed by the 15-minute usage tier. Unavailable when that data is stale.
    usage_tier: bool = False
    # Only for homes Base bills for energy.
    billing: bool = False
    # Only for homes with solar.
    solar_only: bool = False


def _section(data: Data, name: str) -> Data:
    return data.get(name) or {}


def _finite_number(value: Any) -> float | None:
    """Return value as a float, or None when it is missing, a bool, or not finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _watts(key: str) -> Callable[[Data], int | None]:
    """Read a live power flow in kW from the grid poll and return watts."""

    def read(data: Data) -> int | None:
        kw = _finite_number(_section(data, "grid").get(key))
        return round(kw * 1000) if kw is not None else None

    return read


def _dollars(section: str, key: str) -> Callable[[Data], float | None]:
    """Read an amount in cents and return dollars."""

    def read(data: Data) -> float | None:
        cents = _section(data, section).get(key)
        return round(cents / 100, 2) if cents is not None else None

    return read


def _iso(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _present(**values: Any) -> dict[str, Any]:
    """Keep the attributes that have a value."""
    return {key: value for key, value in values.items() if value is not None}


def _grid_voltage(data: Data) -> float | None:
    volts = _finite_number(_section(data, "grid").get("voltage_volts"))
    return round(volts, 1) if volts is not None else None


def _battery_status(data: Data) -> str:
    status = _section(data, "dashboard").get("battery_status", 0)
    return BATTERY_STATUS_MAP.get(status, f"Unknown ({status})")


def _last_bill(data: Data) -> float | None:
    """The newer billing API's last bill, then the older call's."""
    cents = _section(data, "energy_account").get("last_bill_amount_cents")
    if cents is None:
        cents = _section(data, "billing").get("last_bill_cents")
    return round(cents / 100, 2) if cents is not None else None


def _bill_due_date(data: Data) -> date | None:
    return local_due_date(
        _section(data, "energy_account").get("due_at"), dt_util.get_default_time_zone()
    )


def _bill_due_attributes(data: Data) -> dict[str, Any]:
    account = _section(data, "energy_account")
    tz = dt_util.get_default_time_zone()
    attributes: dict[str, Any] = {}
    for key in ("amount_due_cents", "outstanding_balance_cents"):
        cents = account.get(key)
        if cents is not None:
            attributes[key.replace("_cents", "")] = round(cents / 100, 2)
    if account.get("past_due") is not None:
        attributes["past_due"] = account["past_due"]
    for key in ("next_bill_date", "next_autopay_date"):
        day = local_due_date(account.get(key), tz)
        if day is not None:
            attributes[key] = day.isoformat()
    if account.get("due_at") is not None:
        attributes["due_at_utc"] = _iso(account["due_at"])
    return attributes


def _battery_state_attributes(data: Data) -> dict[str, Any]:
    snapshot = _section(data, "snapshot")
    return _present(
        observed_at=_iso(snapshot.get("observed_at")),
        state_of_energy_percent=snapshot.get("state_of_energy_percent"),
        overcurrent_limit_kw=snapshot.get("overcurrent_limit_kw"),
    )


def _mix_24h(key: str) -> Callable[[Data], float | None]:
    def read(data: Data) -> float | None:
        mix = _section(data, "overview").get("energy_mix_24h")
        return mix.get(key) if mix is not None else None

    return read


def _grid_support_attributes(data: Data) -> dict[str, Any]:
    intervals = _section(data, "overview").get("grid_support") or []
    if not intervals:
        return {}
    latest = max(intervals, key=lambda item: item.get("start") or 0)
    return {"last_start": _iso(latest.get("start")), "last_end": _iso(latest.get("end"))}


def _cost_attributes(data: Data) -> dict[str, Any]:
    cents = _section(data, "overview").get("cost_24h_cents")
    return {"cost_last_24h": round(cents / 100, 2)} if cents is not None else {}


def _cycle_attributes(data: Data) -> dict[str, Any]:
    cycle = _section(data, "billing_cycle")
    return _present(
        service_period_start=_iso(cycle.get("service_period_start")),
        data_through=_iso(cycle.get("data_through")),
    )


def _hardware_attributes(data: Data) -> dict[str, Any]:
    account = _section(data, "battery_account")
    attributes = _present(
        plan_name=account.get("plan_name") or None,
        equipment_description=account.get("equipment_description") or None,
    )
    batteries = _section(data, "dashboard").get("batteries") or []
    if batteries:
        # Base's newer model fields, one entry per battery
        attributes["battery_models"] = [
            f"{battery.get('generation') or 'unknown'} {battery.get('variant') or 'unknown'}"
            for battery in batteries
        ]
    return attributes


def _next_service(data: Data) -> datetime | None:
    seconds = _section(data, "dashboard").get("upcoming_service_at")
    return datetime.fromtimestamp(seconds, UTC) if seconds else None


def _derived(key: str) -> Callable[[Data], Any]:
    return lambda data: _section(data, "derived").get(key)


def _energy(key: str) -> Callable[[Data], Any]:
    return lambda data: _section(data, "energy").get(key)


_KWH = UnitOfEnergy.KILO_WATT_HOUR
_MEASUREMENT = SensorStateClass.MEASUREMENT
_TOTAL_INCREASING = SensorStateClass.TOTAL_INCREASING
_DIAGNOSTIC = EntityCategory.DIAGNOSTIC

SENSORS: tuple[BasePowerSensorDescription, ...] = (
    # Battery
    BasePowerSensorDescription(
        key="backup_hours",
        name="Battery Backup Time (Current Load)",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        icon="mdi:battery-clock",
        value_fn=_derived("backup_hours"),
    ),
    BasePowerSensorDescription(
        key="backup_hours_750w",
        name="Battery Backup Time (750 W Essentials)",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        icon="mdi:battery-clock-outline",
        value_fn=_derived("backup_hours_750w"),
    ),
    BasePowerSensorDescription(
        key="battery_percent",
        name="Battery Level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=_MEASUREMENT,
        value_fn=_derived("battery_percent"),
        attributes_fn=lambda data: _present(
            source=_section(data, "derived").get("battery_percent_source") or None
        ),
    ),
    BasePowerSensorDescription(
        key="battery_count",
        name="Battery Count",
        state_class=_MEASUREMENT,
        icon="mdi:battery-multiple",
        value_fn=_derived("battery_count"),
    ),
    BasePowerSensorDescription(
        key="capacity",
        name="System Capacity",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=_MEASUREMENT,
        icon="mdi:battery-high",
        value_fn=_derived("capacity_kwh"),
        attributes_fn=lambda data: {
            key: _section(data, "dashboard").get(key)
            for key in ("capacity_source", "detected_capacity_kwh", "model_capacity_kwh")
        },
    ),
    BasePowerSensorDescription(
        key="battery_status",
        name="Battery Status",
        icon="mdi:battery-heart-variant",
        value_fn=_battery_status,
    ),
    BasePowerSensorDescription(
        key="battery_state",
        name="Battery State",
        translation_key="battery_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(BATTERY_STATES.values()),
        icon="mdi:home-battery",
        value_fn=lambda data: _section(data, "snapshot").get("state"),
        attributes_fn=_battery_state_attributes,
    ),
    BasePowerSensorDescription(
        key="hardware_sku",
        name="Battery Hardware",
        icon="mdi:barcode",
        entity_category=_DIAGNOSTIC,
        value_fn=lambda data: _section(data, "battery_account").get("hardware_sku"),
        attributes_fn=_hardware_attributes,
    ),
    BasePowerSensorDescription(
        key="next_service",
        name="Next Service Visit",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:wrench-clock",
        entity_category=_DIAGNOSTIC,
        value_fn=_next_service,
    ),
    # Live power, from the 60-second poll
    BasePowerSensorDescription(
        key="current_power",
        name="Current Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=_MEASUREMENT,
        icon="mdi:flash",
        value_fn=_derived("current_power_watts"),
    ),
    BasePowerSensorDescription(
        key="power_from_grid",
        name="Power from Grid",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=_MEASUREMENT,
        icon="mdi:transmission-tower",
        value_fn=_watts("power_from_grid_kw"),
    ),
    BasePowerSensorDescription(
        key="battery_power",
        name="Battery Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=_MEASUREMENT,
        icon="mdi:home-battery",
        value_fn=_watts("power_from_storage_kw"),
    ),
    BasePowerSensorDescription(
        key="solar_power",
        name="Solar Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=_MEASUREMENT,
        icon="mdi:solar-power",
        value_fn=_watts("power_from_solar_kw"),
        solar_only=True,
    ),
    BasePowerSensorDescription(
        key="grid_voltage",
        name="Grid Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:sine-wave",
        value_fn=_grid_voltage,
    ),
    BasePowerSensorDescription(
        key="grid_power_amps",
        name="Grid Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=_MEASUREMENT,
        icon="mdi:current-ac",
        value_fn=lambda data: _section(data, "grid").get("current_power_amps"),
    ),
    # 15-minute usage
    BasePowerSensorDescription(
        key="current_energy",
        name="Current Interval Energy",
        native_unit_of_measurement=_KWH,
        state_class=_MEASUREMENT,
        icon="mdi:lightning-bolt",
        value_fn=_derived("current_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="daily_peak",
        name="Daily Peak",
        native_unit_of_measurement=_KWH,
        state_class=_MEASUREMENT,
        icon="mdi:arrow-up-bold",
        value_fn=_derived("daily_peak_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="daily_low",
        name="Daily Low",
        native_unit_of_measurement=_KWH,
        state_class=_MEASUREMENT,
        icon="mdi:arrow-down-bold",
        value_fn=_derived("daily_low_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="daily_total",
        name="Daily Total Energy",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        icon="mdi:sigma",
        value_fn=_derived("daily_total_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="intervals",
        name="Intervals Today",
        state_class=_MEASUREMENT,
        icon="mdi:counter",
        value_fn=_derived("intervals_today"),
        attributes_fn=lambda data: {
            "usage_source": _section(data, "sources").get("usage"),
            "energy_source": _section(data, "sources").get("energy"),
        },
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="daily_avg_grid",
        name="Daily Average Grid Interval",
        native_unit_of_measurement=_KWH,
        state_class=_MEASUREMENT,
        icon="mdi:chart-bell-curve",
        value_fn=_derived("daily_avg_kwh"),
        usage_tier=True,
    ),
    # Today's energy. The data key is grid_to_home_kwh for history, but
    # Base reports home load there, not grid draw.
    BasePowerSensorDescription(
        key="grid_to_home",
        name="Home Usage Today (All Sources)",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:home-lightning-bolt-outline",
        value_fn=_energy("grid_to_home_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="solar_to_home",
        name="Solar to Home Energy",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:solar-power-variant",
        value_fn=_energy("solar_to_home_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="battery_to_home",
        name="Battery to Home Energy",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:battery-arrow-down",
        value_fn=_energy("battery_to_home_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="solar_export_today",
        name="Solar Export Today",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:transmission-tower-export",
        value_fn=lambda data: _section(data, "overview").get("solar_export_today_kwh"),
        usage_tier=True,
        solar_only=True,
    ),
    # The next two repeat Home Usage Today. They stay for installs that
    # already use them and are off by default on new ones.
    BasePowerSensorDescription(
        key="daily_total_grid",
        name="Daily Total (Home Usage)",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        icon="mdi:counter",
        value_fn=daily_grid_total_kwh,
        usage_tier=True,
        entity_registry_enabled_default=False,
    ),
    BasePowerSensorDescription(
        key="total_home_energy",
        name="Total Home Energy",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:home-lightning-bolt",
        value_fn=_derived("total_home_kwh"),
        usage_tier=True,
        entity_registry_enabled_default=False,
    ),
    BasePowerSensorDescription(
        key="self_sufficiency",
        name="Self-Sufficiency",
        native_unit_of_measurement=PERCENTAGE,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:leaf",
        value_fn=_derived("self_sufficiency_percent"),
        usage_tier=True,
    ),
    # Running totals that never reset. Use these in the Energy dashboard.
    # The daily sensors miss each day's last hour, which Base posts after
    # midnight. These add every hour once, whenever it arrives.
    BasePowerSensorDescription(
        key="home_usage_total",
        name="Home Usage (Total)",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:home-lightning-bolt",
        value_fn=_derived("home_usage_total_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="solar_to_home_total",
        name="Solar to Home (Total)",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:solar-power",
        value_fn=_derived("solar_total_kwh"),
        usage_tier=True,
        solar_only=True,
    ),
    # Base's rolling 24 hours. No state class: a sliding window is not a
    # daily total, so it must not feed long-term energy statistics.
    BasePowerSensorDescription(
        key="grid_to_home_kwh_24h",
        name="Grid to Home (24 h)",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        icon="mdi:transmission-tower-export",
        value_fn=_mix_24h("grid_to_home_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="solar_to_home_kwh_24h",
        name="Solar to Home (24 h)",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        icon="mdi:solar-power",
        value_fn=_mix_24h("solar_to_home_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="battery_to_home_kwh_24h",
        name="Battery to Home (24 h)",
        native_unit_of_measurement=_KWH,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        icon="mdi:battery-arrow-down",
        value_fn=_mix_24h("battery_to_home_kwh"),
        usage_tier=True,
    ),
    BasePowerSensorDescription(
        key="grid_support_events_24h",
        name="Grid Support Events (24 h)",
        state_class=_MEASUREMENT,
        icon="mdi:transmission-tower-import",
        value_fn=_derived("grid_support_events_24h"),
        attributes_fn=_grid_support_attributes,
        usage_tier=True,
    ),
    # Billing. Left out on homes Base does not bill for energy.
    BasePowerSensorDescription(
        key="bill_amount",
        name="Last Bill Amount",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:currency-usd",
        entity_category=_DIAGNOSTIC,
        value_fn=_last_bill,
        billing=True,
    ),
    BasePowerSensorDescription(
        key="bill_due_date",
        name="Bill Due Date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock",
        entity_category=_DIAGNOSTIC,
        value_fn=_bill_due_date,
        attributes_fn=_bill_due_attributes,
        billing=True,
    ),
    BasePowerSensorDescription(
        key="energy_cost_today",
        name="Energy Cost Today",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        icon="mdi:currency-usd",
        value_fn=_dollars("overview", "cost_today_cents"),
        attributes_fn=_cost_attributes,
        usage_tier=True,
        billing=True,
    ),
    BasePowerSensorDescription(
        key="cycle_net_accrued_cents",
        name="Bill to Date",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        icon="mdi:cash-clock",
        value_fn=_dollars("billing_cycle", "net_accrued_cents"),
        attributes_fn=_cycle_attributes,
        billing=True,
    ),
    BasePowerSensorDescription(
        key="cycle_projected_charges_cents",
        name="Projected Bill",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        icon="mdi:cash-fast",
        value_fn=_dollars("billing_cycle", "projected_charges_cents"),
        attributes_fn=_cycle_attributes,
        billing=True,
    ),
    BasePowerSensorDescription(
        key="cycle_solar_credit_cents",
        name="Solar Buyback Credit",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        icon="mdi:cash-plus",
        value_fn=_dollars("billing_cycle", "solar_credit_cents"),
        attributes_fn=_cycle_attributes,
        billing=True,
    ),
    # Diagnostics
    BasePowerSensorDescription(
        key="wifi_signal",
        name="WiFi Signal",
        native_unit_of_measurement=PERCENTAGE,
        state_class=_MEASUREMENT,
        icon="mdi:wifi",
        entity_category=_DIAGNOSTIC,
        value_fn=lambda data: _section(data, "wifi").get("signal"),
        attributes_fn=lambda data: _present(
            last_updated=_iso(_section(data, "wifi").get("last_updated"))
        ),
    ),
    BasePowerSensorDescription(
        key="wifi_ssid",
        name="WiFi SSID",
        icon="mdi:wifi-settings",
        entity_category=_DIAGNOSTIC,
        value_fn=lambda data: _section(data, "wifi").get("ssid"),
        attributes_fn=lambda data: _present(
            state=_section(data, "wifi").get("state"),
            source=_section(data, "wifi").get("source"),
        ),
    ),
    # The unique id says asset_id for history. Base's field is accountId.
    BasePowerSensorDescription(
        key="asset_id",
        name="Account ID",
        icon="mdi:identifier",
        entity_category=_DIAGNOSTIC,
        value_fn=lambda data: _section(data, "cycles").get("account_id"),
    ),
)


def _has_solar(data: Data | None) -> bool:
    return bool(_section(data or {}, "dashboard").get("has_solar"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Base Power sensors from a config entry."""
    coordinator: BasePowerCoordinator = entry.runtime_data
    data = coordinator.data or {}
    solar = _has_solar(data)

    entities = [
        BasePowerSensor(coordinator, entry, description)
        for description in SENSORS
        if solar or not description.solar_only
    ]

    # A site without Base energy service has no energy bills. Leave out the
    # billing sensors that have nothing to show, and remove ones left from
    # earlier versions. Any that still have a value stay.
    if _section(data, "location").get("energy_service") == "no_service":
        registry = er.async_get(hass)
        kept = []
        for entity in entities:
            if entity.entity_description.billing and entity.native_value is None:
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, entity.unique_id)
                if entity_id:
                    registry.async_remove(entity_id)
                continue
            kept.append(entity)
        entities = kept

    async_add_entities(entities)

    if solar:
        return

    # Base can report solar after setup: the first fetch may fail, or the
    # panels may be added later. Add the solar sensors when it does.
    @callback
    def add_solar_sensors() -> None:
        nonlocal solar
        if solar or not _has_solar(coordinator.data):
            return
        solar = True
        async_add_entities(
            BasePowerSensor(coordinator, entry, description)
            for description in SENSORS
            if description.solar_only
        )

    entry.async_on_unload(coordinator.async_add_listener(add_solar_sensors))


class BasePowerSensor(BasePowerEntity, SensorEntity):
    """A Base Power sensor described by one row of SENSORS."""

    entity_description: BasePowerSensorDescription

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        description: BasePowerSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._usage_tier = description.usage_tier

    @property
    def native_value(self) -> Any:
        """Return the value, or None (unknown) before the first data."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the extra attributes, when this sensor has any."""
        attributes_fn = self.entity_description.attributes_fn
        if attributes_fn is None:
            return None
        return attributes_fn(self.coordinator.data or {})
