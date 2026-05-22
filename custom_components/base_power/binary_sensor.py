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
        if self.coordinator.data:
            # If we have dashboard data with backup_seconds > 0, battery is connected
            return self.coordinator.data["dashboard"].get("backup_seconds", 0) > 0
        return None
