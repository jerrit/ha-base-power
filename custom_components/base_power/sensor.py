"""Sensor entities for Base Power integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
    PERCENTAGE,
)
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
        """Return backup hours remaining."""
        if self.coordinator.data:
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
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT

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
    _attr_device_class = SensorDeviceClass.ENERGY
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
    _attr_device_class = SensorDeviceClass.ENERGY
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
