"""Binary sensor entities for Base Power integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up Base Power binary sensors from a config entry."""
    coordinator: BasePowerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        BasePowerSolarSensor(coordinator, entry),
        BasePowerBatteryConnectedSensor(coordinator, entry),
        BasePowerGridStatusSensor(coordinator, entry),
        BasePowerBatteryChargingSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class BasePowerBinarySensorBase(
    CoordinatorEntity[BasePowerCoordinator], BinarySensorEntity
):
    """Base class for Base Power binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data[CONF_SERVICE_LOCATION_ID])},
            "name": f"Base Power {entry.data[CONF_SERVICE_LOCATION_ID]}",
            "manufacturer": "Base Power",
            "model": "Home Battery System",
        }


class BasePowerSolarSensor(BasePowerBinarySensorBase):
    """Binary sensor for solar panel presence."""

    _attr_name = "Solar Connected"
    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_has_solar"

    @property
    def is_on(self) -> bool | None:
        """Return True if solar is connected."""
        if self.coordinator.data:
            return self.coordinator.data["dashboard"].get("has_solar", False)
        return None




class BasePowerBatteryConnectedSensor(BasePowerBinarySensorBase):
    """Binary sensor for battery WiFi connectivity."""

    _attr_name = "Battery Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:wifi"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_battery_connected"

    @property
    def is_on(self) -> bool | None:
        """Return True if battery is connected via WiFi."""
        if not self.coordinator.data:
            return None
        connected = (self.coordinator.data.get("grid") or {}).get("gateway_connected")
        if connected is None:
            return None
        return bool(connected)


class BasePowerGridStatusSensor(BasePowerBinarySensorBase):
    """Binary sensor for grid power status (on = grid up, off = outage)."""

    _attr_name = "Grid Power"
    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_grid_power"

    @property
    def is_on(self) -> bool | None:
        """Return True if grid power is available (no outage).

        Unknown when this poll has no grid telemetry. Guessing "on" reports
        a false all-clear during an outage.
        """
        if not self.coordinator.data:
            return None
        grid = self.coordinator.data.get("grid") or {}
        if not grid.get("available"):
            return None
        return grid.get("grid_is_up")

    @property
    def extra_state_attributes(self) -> dict[str, float | bool]:
        """Expose the voltage sample and outage flag used to decide is_on."""
        if not self.coordinator.data:
            return {}
        grid = self.coordinator.data.get("grid") or {}
        attributes: dict[str, float | bool] = {}
        if grid.get("voltage_volts") is not None:
            attributes["voltage_volts"] = round(float(grid["voltage_volts"]), 1)
        if grid.get("is_active_outage") is not None:
            attributes["active_outage"] = bool(grid["is_active_outage"])
        return attributes


class BasePowerBatteryChargingSensor(BasePowerBinarySensorBase):
    """Binary sensor for battery charging state (on = charging, off = discharging)."""

    _attr_name = "Battery Charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_icon = "mdi:battery-charging"

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_battery_charging"

    @property
    def is_on(self) -> bool | None:
        """Return True if battery SoC is increasing, False if decreasing, None if stable."""
        if self.coordinator.data:
            return self.coordinator.data.get("derived", {}).get("battery_charging")
        return None
