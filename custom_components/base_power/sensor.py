"""Sensor entities for Base Power integration."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
    PERCENTAGE,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import daily_grid_total_kwh
from .api_v2 import BATTERY_STATES, local_due_date
from .const import DOMAIN, CONF_SERVICE_LOCATION_ID
from .coordinator import BasePowerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Base Power sensors from a config entry."""
    coordinator: BasePowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    service_location_id = entry.data[CONF_SERVICE_LOCATION_ID]

    entities = [
        BasePowerBackupHoursSensor(coordinator, entry),
        BasePowerBatteryPercentSensor(coordinator, entry),
        BasePowerBatteryCountSensor(coordinator, entry),
        BasePowerCapacitySensor(coordinator, entry),
        BasePowerCurrentPowerSensor(coordinator, entry),
        BasePowerCurrentEnergySensor(coordinator, entry),
        BasePowerDailyPeakSensor(coordinator, entry),
        BasePowerDailyLowSensor(coordinator, entry),
        BasePowerDailyTotalSensor(coordinator, entry),
        BasePowerIntervalCountSensor(coordinator, entry),
        BasePowerBatteryStatusSensor(coordinator, entry),
        BasePowerWifiSignalSensor(coordinator, entry),
        BasePowerWifiSsidSensor(coordinator, entry),
        BasePowerGridToHomeSensor(coordinator, entry),
        BasePowerSolarToHomeSensor(coordinator, entry),
        BasePowerBatteryToHomeSensor(coordinator, entry),
        BasePowerBillAmountSensor(coordinator, entry),
        BasePowerBillDueDateSensor(coordinator, entry),
        BasePowerAssetIdSensor(coordinator, entry),
        BasePowerGridPowerAmpsSensor(coordinator, entry),
        BasePowerDailyTotalGridSensor(coordinator, entry),
        BasePowerDailyAvgGridSensor(coordinator, entry),
        BasePowerTotalHomeEnergySensor(coordinator, entry),
        BasePowerSelfSufficiencySensor(coordinator, entry),
        BasePowerBatteryStateSensor(coordinator, entry),
        BasePowerHardwareSkuSensor(coordinator, entry),
        BasePowerBackupHours750Sensor(coordinator, entry),
        BasePowerEnergyMix24hSensor(coordinator, entry, "grid_to_home_kwh", "Grid to Home (24 h)", "mdi:transmission-tower-export"),
        BasePowerEnergyMix24hSensor(coordinator, entry, "solar_to_home_kwh", "Solar to Home (24 h)", "mdi:solar-power"),
        BasePowerEnergyMix24hSensor(coordinator, entry, "battery_to_home_kwh", "Battery to Home (24 h)", "mdi:battery-arrow-down"),
        BasePowerGridSupportEventsSensor(coordinator, entry),
        BasePowerEnergyCostTodaySensor(coordinator, entry),
        BasePowerBillingCycleSensor(coordinator, entry, "net_accrued_cents", "Bill to Date", "mdi:cash-clock"),
        BasePowerBillingCycleSensor(coordinator, entry, "projected_charges_cents", "Projected Bill", "mdi:cash-fast"),
        BasePowerBillingCycleSensor(coordinator, entry, "solar_credit_cents", "Solar Buyback Credit", "mdi:cash-plus"),
        BasePowerRunningTotalSensor(coordinator, entry, "home_usage_total_kwh", "home_usage_total", "Home Usage (Total)", "mdi:home-lightning-bolt"),
        BasePowerGridVoltageSensor(coordinator, entry),
        BasePowerLivePowerSensor(coordinator, entry, "power_from_grid_kw", "power_from_grid", "Power from Grid", "mdi:transmission-tower"),
        BasePowerLivePowerSensor(coordinator, entry, "power_from_storage_kw", "battery_power", "Battery Power", "mdi:home-battery"),
    ]
    data = coordinator.data or {}
    if (data.get("dashboard") or {}).get("has_solar"):
        entities.append(
            BasePowerRunningTotalSensor(coordinator, entry, "solar_total_kwh", "solar_to_home_total", "Solar to Home (Total)", "mdi:solar-power")
        )
        entities.append(
            BasePowerLivePowerSensor(coordinator, entry, "power_from_solar_kw", "solar_power", "Solar Power", "mdi:solar-power")
        )

    # A site without Base energy service has no energy bills. Leave out the
    # billing sensors that have nothing to show, and remove ones left from
    # earlier versions. Any that still have a value stay.
    if (data.get("location") or {}).get("energy_service") == "no_service":
        registry = er.async_get(hass)
        kept = []
        for entity in entities:
            if isinstance(entity, BILLING_SENSORS) and entity.native_value is None:
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, entity.unique_id)
                if entity_id:
                    registry.async_remove(entity_id)
                continue
            kept.append(entity)
        entities = kept

    async_add_entities(entities)


class BasePowerSensorBase(CoordinatorEntity[BasePowerCoordinator], SensorEntity):
    """Base class for Base Power sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data[CONF_SERVICE_LOCATION_ID])},
            "name": f"Base Power {entry.data[CONF_SERVICE_LOCATION_ID]}",
            "manufacturer": "Base Power",
            "model": "Home Battery System",
        }


class BasePowerBackupHoursSensor(BasePowerSensorBase):
    """Backup hours at the home's current load, from Base."""

    _attr_name = "Battery Backup Time (Current Load)"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-clock"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_backup_hours"

    @property
    def native_value(self) -> float | None:
        """Return backup hours reported by the service context."""
        if self.coordinator.data:
            return self.coordinator.data.get("derived", {}).get("backup_hours")
        return None


class BasePowerBatteryPercentSensor(BasePowerSensorBase):
    """Sensor for estimated battery percentage."""

    _attr_name = "Battery Level"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_battery_percent"

    @property
    def native_value(self) -> float | None:
        """Return estimated battery percentage."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("battery_percent")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Say whether the percentage came from service-context telemetry."""
        if not self.coordinator.data:
            return {}
        source = self.coordinator.data.get("derived", {}).get("battery_percent_source")
        if not source:
            return {}
        return {"source": source}


class BasePowerBatteryCountSensor(BasePowerSensorBase):
    """Sensor for number of battery units installed."""

    _attr_name = "Battery Count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-multiple"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_battery_count"

    @property
    def native_value(self) -> int | None:
        """Return number of battery units."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("battery_count")
        return None


class BasePowerCapacitySensor(BasePowerSensorBase):
    """Sensor for total system capacity in kWh."""

    _attr_name = "System Capacity"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-high"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_capacity"

    @property
    def native_value(self) -> float | None:
        """Return total system capacity in kWh."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("capacity_kwh")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Where the capacity came from, and what the hardware SKU implies."""
        dashboard = (self.coordinator.data or {}).get("dashboard") or {}
        return {
            "capacity_source": dashboard.get("capacity_source"),
            "detected_capacity_kwh": dashboard.get("detected_capacity_kwh"),
            "model_capacity_kwh": dashboard.get("model_capacity_kwh"),
        }


class BasePowerCurrentPowerSensor(BasePowerSensorBase):
    """Sensor for current power consumption (watts)."""

    _attr_name = "Current Power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_current_power"

    @property
    def native_value(self) -> int | None:
        """Return current power in watts."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("current_power_watts")
        return None


class BasePowerCurrentEnergySensor(BasePowerSensorBase):
    """Sensor for current 15-min interval energy."""

    _attr_name = "Current Interval Energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_current_energy"

    @property
    def native_value(self) -> float | None:
        """Return current interval kWh."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("current_kwh")
        return None


class BasePowerDailyPeakSensor(BasePowerSensorBase):
    """Sensor for daily peak energy interval."""

    _attr_name = "Daily Peak"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:arrow-up-bold"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_daily_peak"

    @property
    def native_value(self) -> float | None:
        """Return daily peak kWh interval."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("daily_peak_kwh")
        return None


class BasePowerDailyLowSensor(BasePowerSensorBase):
    """Sensor for daily low energy interval."""

    _attr_name = "Daily Low"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:arrow-down-bold"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_daily_low"

    @property
    def native_value(self) -> float | None:
        """Return daily low kWh interval."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("daily_low_kwh")
        return None


class BasePowerDailyTotalSensor(BasePowerSensorBase):
    """Sensor for daily total energy consumption."""

    _attr_name = "Daily Total Energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:sigma"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_daily_total"

    @property
    def native_value(self) -> float | None:
        """Return daily total kWh."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("daily_total_kwh")
        return None


class BasePowerIntervalCountSensor(BasePowerSensorBase):
    """Sensor for number of usage intervals today."""

    _attr_name = "Intervals Today"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_intervals"

    @property
    def native_value(self) -> int | None:
        """Return number of intervals."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("intervals_today")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Which API supplied the 15-minute usage and today's energy."""
        sources = (self.coordinator.data or {}).get("sources") or {}
        return {
            "usage_source": sources.get("usage"),
            "energy_source": sources.get("energy"),
        }


BATTERY_STATUS_MAP = {
    0: "Unknown",
    1: "Not Installed",
    2: "Installed",
    3: "In Service",
    4: "Offline",
}


class BasePowerBatteryStatusSensor(BasePowerSensorBase):
    """Sensor for battery status (enum)."""

    _attr_name = "Battery Status"
    _attr_icon = "mdi:battery-heart-variant"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_battery_status"

    @property
    def native_value(self) -> str | None:
        """Return battery status as string."""
        if self.coordinator.data:
            status = self.coordinator.data["dashboard"].get("battery_status", 0)
            return BATTERY_STATUS_MAP.get(status, f"Unknown ({status})")
        return None


class BasePowerWifiSignalSensor(BasePowerSensorBase):
    """Sensor for battery WiFi signal strength."""

    _attr_name = "WiFi Signal"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_wifi_signal"

    @property
    def native_value(self) -> int | None:
        """Return WiFi signal strength percentage."""
        if self.coordinator.data:
            return self.coordinator.data.get("wifi", {}).get("signal")
        return None


class BasePowerWifiSsidSensor(BasePowerSensorBase):
    """Sensor for battery WiFi SSID."""

    _attr_name = "WiFi SSID"
    _attr_icon = "mdi:wifi-settings"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_wifi_ssid"

    @property
    def native_value(self) -> str | None:
        """Return WiFi SSID."""
        if self.coordinator.data:
            return self.coordinator.data.get("wifi", {}).get("ssid")
        return None


class BasePowerGridToHomeSensor(BasePowerSensorBase):
    """Today's home usage in kWh.

    The data key is grid_to_home_kwh for history, but Base reports home load
    here, not grid draw. It equals grid energy only when solar and the
    battery supply nothing.
    """

    _attr_name = "Home Usage Today (All Sources)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:home-lightning-bolt-outline"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_grid_to_home"

    @property
    def native_value(self) -> float | None:
        """Return grid-to-home energy in kWh."""
        if self.coordinator.data:
            return self.coordinator.data.get("energy", {}).get("grid_to_home_kwh")
        return None


class BasePowerSolarToHomeSensor(BasePowerSensorBase):
    """Sensor for solar-to-home energy."""

    _attr_name = "Solar to Home Energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_solar_to_home"

    @property
    def native_value(self) -> float | None:
        """Return solar-to-home energy in kWh."""
        if self.coordinator.data:
            return self.coordinator.data.get("energy", {}).get("solar_to_home_kwh")
        return None


class BasePowerBatteryToHomeSensor(BasePowerSensorBase):
    """Sensor for battery-to-home energy."""

    _attr_name = "Battery to Home Energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:battery-arrow-down"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_battery_to_home"

    @property
    def native_value(self) -> float | None:
        """Return battery-to-home energy in kWh."""
        if self.coordinator.data:
            return self.coordinator.data.get("energy", {}).get("battery_to_home_kwh")
        return None


class BasePowerBillAmountSensor(BasePowerSensorBase):
    """Amount of the last energy bill.

    Reads the energy account from Base's newer billing API, then the older
    billing call's last-bill field. Before 1.14 this showed spend so far
    this cycle, the same as Bill to Date.
    """

    _attr_name = "Last Bill Amount"
    _attr_native_unit_of_measurement = "$"
    _attr_icon = "mdi:currency-usd"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_bill_amount"

    @property
    def native_value(self) -> float | None:
        """Return the last bill in dollars."""
        data = self.coordinator.data or {}
        cents = (data.get("energy_account") or {}).get("last_bill_amount_cents")
        if cents is None:
            cents = (data.get("billing") or {}).get("last_bill_cents")
        return round(cents / 100, 2) if cents is not None else None


class BasePowerBillDueDateSensor(BasePowerSensorBase):
    """Due date of the oldest unpaid energy bill.

    Unknown when nothing is owed or the home is not billed by Base for energy.
    """

    _attr_name = "Bill Due Date"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.DATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_bill_due_date"

    def _account(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("energy_account") or {}

    @property
    def native_value(self) -> date | None:
        """Return the due date of the oldest unpaid bill."""
        return local_due_date(self._account().get("due_at"), dt_util.get_default_time_zone())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Amount due, past-due flag, balance, and upcoming dates."""
        account = self._account()
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
            attributes["due_at_utc"] = datetime.fromtimestamp(account["due_at"], UTC).isoformat()
        return attributes


class BasePowerAssetIdSensor(BasePowerSensorBase):
    """Sensor for asset ID (diagnostic)."""

    _attr_name = "Asset ID"
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_asset_id"

    @property
    def native_value(self) -> str | None:
        """Return asset ID."""
        if self.coordinator.data:
            return self.coordinator.data.get("cycles", {}).get("asset_id")
        return None


class BasePowerGridPowerAmpsSensor(BasePowerSensorBase):
    """Sensor for grid current draw (amps)."""

    _attr_name = "Grid Current"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_grid_power_amps"

    @property
    def native_value(self) -> float | None:
        """Return grid current in amps."""
        if self.coordinator.data:
            return self.coordinator.data.get("grid", {}).get("current_power_amps")
        return None


class BasePowerDailyTotalGridSensor(BasePowerSensorBase):
    """Today's home usage kWh, the same sum as Home Usage Today (All Sources).

    Base reports home load here, not grid draw. None stays unknown.
    """

    _attr_name = "Daily Total (Home Usage)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_daily_total_grid"

    @property
    def native_value(self) -> float | None:
        """Return today's grid kWh, or unknown when usage energy has none."""
        if self.coordinator.data:
            return daily_grid_total_kwh(self.coordinator.data)
        return None


class BasePowerDailyAvgGridSensor(BasePowerSensorBase):
    """Average kWh of today's 15-minute usage intervals.

    This is daily total energy divided by the number of intervals today.
    None when today has no intervals.
    """

    _attr_name = "Daily Average Grid Interval"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_daily_avg_grid"

    @property
    def native_value(self) -> float | None:
        """Return today's average 15-min kWh, or unknown when there are no intervals."""
        if self.coordinator.data:
            return self.coordinator.data.get("derived", {}).get("daily_avg_kwh")
        return None


class BasePowerTotalHomeEnergySensor(BasePowerSensorBase):
    """Total home energy use. Base's home load already includes solar and battery."""

    _attr_name = "Total Home Energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_total_home_energy"

    @property
    def native_value(self) -> float | None:
        """Return total home energy consumption in kWh."""
        if self.coordinator.data:
            return self.coordinator.data.get("derived", {}).get("total_home_kwh")
        return None


class BasePowerSelfSufficiencySensor(BasePowerSensorBase):
    """Sensor for energy self-sufficiency percentage."""

    _attr_name = "Self-Sufficiency"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:leaf"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_self_sufficiency"

    @property
    def native_value(self) -> float | None:
        """Return % of home energy from solar + battery (vs grid)."""
        if self.coordinator.data:
            return self.coordinator.data.get("derived", {}).get("self_sufficiency_percent")
        return None


def _iso(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, UTC).isoformat()


class BasePowerBatteryStateSensor(BasePowerSensorBase):
    """Battery mode from the battery snapshot: on grid, outage, overcurrent."""

    _attr_name = "Battery State"
    _attr_translation_key = "battery_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(BATTERY_STATES.values())
    _attr_icon = "mdi:home-battery"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_battery_state"

    @property
    def native_value(self) -> str | None:
        """Return the battery mode."""
        if self.coordinator.data:
            return (self.coordinator.data.get("snapshot") or {}).get("state")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Observation time, off-grid battery percent, and overcurrent limit."""
        snapshot = (self.coordinator.data or {}).get("snapshot") or {}
        attributes: dict[str, object] = {}
        if snapshot.get("observed_at") is not None:
            attributes["observed_at"] = _iso(snapshot["observed_at"])
        if snapshot.get("state_of_energy_percent") is not None:
            attributes["state_of_energy_percent"] = snapshot["state_of_energy_percent"]
        if snapshot.get("overcurrent_limit_kw") is not None:
            attributes["overcurrent_limit_kw"] = snapshot["overcurrent_limit_kw"]
        return attributes


class BasePowerBackupHours750Sensor(BasePowerSensorBase):
    """Backup hours at a 750 W essentials load."""

    _attr_name = "Battery Backup Time (750 W Essentials)"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-clock-outline"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_backup_hours_750w"

    @property
    def native_value(self) -> float | None:
        """Return backup hours at 750 W."""
        if self.coordinator.data:
            return (self.coordinator.data.get("snapshot") or {}).get("backup_hours_750w")
        return None


class BasePowerEnergyMix24hSensor(BasePowerSensorBase):
    """One source in Base's rolling 24-hour energy mix.

    No state class: the value is a sliding window, not a daily total, so
    it must not feed long-term energy statistics.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_{key}_24h"

    @property
    def native_value(self) -> float | None:
        """Return kWh for this source over the last 24 hours."""
        if self.coordinator.data:
            mix = (self.coordinator.data.get("overview") or {}).get("energy_mix_24h")
            if mix is not None:
                return mix.get(self._key)
        return None


class BasePowerGridSupportEventsSensor(BasePowerSensorBase):
    """Grid support events in the last 24 hours."""

    _attr_name = "Grid Support Events (24 h)"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_grid_support_events_24h"

    @property
    def native_value(self) -> int | None:
        """Return the number of events."""
        if self.coordinator.data:
            return self.coordinator.data.get("derived", {}).get("grid_support_events_24h")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Start and end of the latest event."""
        intervals = ((self.coordinator.data or {}).get("overview") or {}).get("grid_support") or []
        if not intervals:
            return {}
        latest = max(intervals, key=lambda item: item.get("start") or 0)
        return {"last_start": _iso(latest.get("start")), "last_end": _iso(latest.get("end"))}


class BasePowerEnergyCostTodaySensor(BasePowerSensorBase):
    """Estimated grid energy cost since local midnight. Empty without Base billing.

    The Base app's daily overview shows the last 24 hours instead. That
    figure is the cost_last_24h attribute.
    """

    _attr_name = "Energy Cost Today"
    _attr_native_unit_of_measurement = "$"
    _attr_icon = "mdi:currency-usd"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_energy_cost_today"

    @property
    def native_value(self) -> float | None:
        """Return today's cost in dollars."""
        if self.coordinator.data:
            cents = (self.coordinator.data.get("overview") or {}).get("cost_today_cents")
            if cents is not None:
                return round(cents / 100, 2)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Cost over the last 24 hours, the window the Base app shows."""
        cents = ((self.coordinator.data or {}).get("overview") or {}).get("cost_24h_cents")
        return {"cost_last_24h": round(cents / 100, 2)} if cents is not None else {}


class BasePowerBillingCycleSensor(BasePowerSensorBase):
    """An amount from the current billing cycle. Empty without Base billing."""

    _attr_native_unit_of_measurement = "$"
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_cycle_{key}"

    @property
    def native_value(self) -> float | None:
        """Return the amount in dollars."""
        if self.coordinator.data:
            cents = (self.coordinator.data.get("billing_cycle") or {}).get(self._key)
            if cents is not None:
                return round(cents / 100, 2)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Cycle start and how current the data is."""
        cycle = (self.coordinator.data or {}).get("billing_cycle") or {}
        attributes: dict[str, object] = {}
        if cycle.get("service_period_start") is not None:
            attributes["service_period_start"] = _iso(cycle["service_period_start"])
        if cycle.get("data_through") is not None:
            attributes["data_through"] = _iso(cycle["data_through"])
        return attributes


class BasePowerHardwareSkuSensor(BasePowerSensorBase):
    """Battery hardware SKU from the battery account, with the plan name."""

    _attr_name = "Battery Hardware"
    _attr_icon = "mdi:barcode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_hardware_sku"

    @property
    def native_value(self) -> str | None:
        """Return the hardware SKU."""
        if self.coordinator.data:
            return (self.coordinator.data.get("battery_account") or {}).get("hardware_sku")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Plan name and equipment description."""
        account = (self.coordinator.data or {}).get("battery_account") or {}
        attributes: dict[str, object] = {}
        if account.get("plan_name"):
            attributes["plan_name"] = account["plan_name"]
        if account.get("equipment_description"):
            attributes["equipment_description"] = account["equipment_description"]
        batteries = ((self.coordinator.data or {}).get("dashboard") or {}).get("batteries") or []
        if batteries:
            # Base's newer model fields, one entry per battery
            attributes["battery_models"] = [
                f"{battery.get('generation') or 'unknown'} {battery.get('variant') or 'unknown'}"
                for battery in batteries
            ]
        return attributes


class BasePowerRunningTotalSensor(BasePowerSensorBase):
    """Energy total that never resets. Use it in the Energy dashboard.

    The daily sensors miss each day's last hour, which Base posts after
    midnight. This total adds every hour once, whenever it arrives.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        key: str,
        unique_key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_{unique_key}"

    @property
    def native_value(self) -> float | None:
        """Return the running total in kWh. Unknown until the first hour is counted."""
        if self.coordinator.data:
            return self.coordinator.data.get("derived", {}).get(self._key)
        return None


def _finite_number(value: Any) -> float | None:
    """Return value as a float, or None when it is missing, a bool, or not finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


class BasePowerGridVoltageSensor(BasePowerSensorBase):
    """Grid voltage from the 60-second grid poll."""

    _attr_name = "Grid Voltage"
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:sine-wave"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_grid_voltage"

    @property
    def native_value(self) -> float | None:
        """Return the latest grid voltage. Unknown when Base sent none."""
        if not self.coordinator.data:
            return None
        volts = _finite_number((self.coordinator.data.get("grid") or {}).get("voltage_volts"))
        return round(volts, 1) if volts is not None else None


class BasePowerLivePowerSensor(BasePowerSensorBase):
    """Live power flow in watts from the 60-second grid poll.

    Base sends kW. Power from Grid is negative while exporting. Battery
    Power is positive while the battery discharges.
    """

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        key: str,
        unique_key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_{unique_key}"

    @property
    def native_value(self) -> int | None:
        """Return watts. Unknown when the power flow is missing."""
        if not self.coordinator.data:
            return None
        kw = _finite_number((self.coordinator.data.get("grid") or {}).get(self._key))
        return round(kw * 1000) if kw is not None else None


# Sensors that only apply to homes billed by Base for energy
BILLING_SENSORS = (
    BasePowerBillAmountSensor,
    BasePowerBillDueDateSensor,
    BasePowerEnergyCostTodaySensor,
    BasePowerBillingCycleSensor,
)
