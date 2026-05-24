"""Sensor entities for Base Power integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
    PERCENTAGE,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
    ]

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
    """Sensor for battery backup hours remaining."""

    _attr_name = "Battery Backup Time"
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
        """Return estimated backup hours remaining."""
        if self.coordinator.data:
            estimated = self.coordinator.data.get("derived", {}).get("estimated_backup_hours")
            if estimated is not None:
                return estimated
            return self.coordinator.data["dashboard"].get("backup_hours")
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
    def native_value(self) -> int | None:
        """Return total system capacity in kWh."""
        if self.coordinator.data:
            return self.coordinator.data["derived"].get("capacity_kwh")
        return None


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
    """Sensor for grid-to-home energy."""

    _attr_name = "Grid to Home Energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:transmission-tower"

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
    """Sensor for current bill amount."""

    _attr_name = "Bill Amount"
    _attr_native_unit_of_measurement = "$"
    _attr_icon = "mdi:currency-usd"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_bill_amount"

    @property
    def native_value(self) -> float | None:
        """Return bill amount in dollars."""
        if self.coordinator.data:
            cents = self.coordinator.data.get("billing", {}).get("amount_cents")
            if cents is not None:
                return round(cents / 100, 2)
        return None


class BasePowerBillDueDateSensor(BasePowerSensorBase):
    """Sensor for bill due date."""

    _attr_name = "Bill Due Date"
    _attr_icon = "mdi:calendar-clock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_bill_due_date"

    @property
    def native_value(self) -> str | None:
        """Return bill due date."""
        if self.coordinator.data:
            return self.coordinator.data.get("billing", {}).get("due_date")
        return None


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
    """Sensor for current grid power draw (amps)."""

    _attr_name = "Grid Power"
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
        """Return current grid power in amps."""
        if self.coordinator.data:
            return self.coordinator.data.get("grid", {}).get("current_power_amps")
        return None


class BasePowerDailyTotalGridSensor(BasePowerSensorBase):
    """Sensor for daily total from grid status endpoint."""

    _attr_name = "Daily Total (Grid)"
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
        """Return daily total kWh from grid status."""
        if self.coordinator.data:
            return self.coordinator.data.get("grid", {}).get("daily_total_kwh_grid")
        return None


class BasePowerDailyAvgGridSensor(BasePowerSensorBase):
    """Sensor for daily average 15-min interval energy from grid."""

    _attr_name = "Daily Average Grid Interval"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_daily_avg_grid"

    @property
    def native_value(self) -> float | None:
        """Return daily average 15-min interval kWh from grid."""
        if self.coordinator.data:
            return self.coordinator.data.get("grid", {}).get("daily_avg_kwh_grid")
        return None
