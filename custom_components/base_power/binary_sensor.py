"""Binary sensor entities for Base Power."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BasePowerCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class BasePowerBinarySensorDescription(BinarySensorEntityDescription):
    """Extends BinarySensorEntityDescription with a value extractor."""

    value_fn: Callable[[dict], bool | None]


BINARY_SENSOR_DESCRIPTIONS: tuple[BasePowerBinarySensorDescription, ...] = (
    BasePowerBinarySensorDescription(
        key="grid_connected",
        name="Grid Connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:transmission-tower",
        value_fn=lambda d: d.get("grid", {}).get("grid_connected"),
    ),
    BasePowerBinarySensorDescription(
        key="outage_active",
        name="Outage Active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-circle",
        value_fn=lambda d: d.get("grid", {}).get("outage_active"),
    ),
    BasePowerBinarySensorDescription(
        key="backup_active",
        name="Battery Backup Active",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:battery-charging",
        value_fn=lambda d: d.get("grid", {}).get("backup_active"),
    ),
    BasePowerBinarySensorDescription(
        key="has_solar",
        name="Solar Available",
        icon="mdi:solar-panel",
        value_fn=lambda d: d.get("dashboard", {}).get("has_solar"),
    ),
    BasePowerBinarySensorDescription(
        key="has_automatic_backup",
        name="Automatic Backup Enabled",
        icon="mdi:shield-home",
        value_fn=lambda d: d.get("dashboard", {}).get("has_automatic_backup"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Base Power binary sensors."""
    coordinator: BasePowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BasePowerBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class BasePowerBinarySensor(CoordinatorEntity[BasePowerCoordinator], BinarySensorEntity):
    """A binary sensor entity backed by the Base Power coordinator."""

    entity_description: BasePowerBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        description: BasePowerBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data.get("service_location_id", entry.entry_id))},
            name=entry.title,
            manufacturer="Base Power",
            model="Home Battery System",
            configuration_url="https://basepowercompany.com",
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
