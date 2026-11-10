"""Shared base for Base Power entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERVICE_LOCATION_ID, DOMAIN
from .coordinator import BasePowerCoordinator

type BasePowerConfigEntry = ConfigEntry[BasePowerCoordinator]


def device_info(site_id: str) -> DeviceInfo:
    """The one device every entity of a site belongs to."""
    return DeviceInfo(
        identifiers={(DOMAIN, site_id)},
        name=f"Base Power {site_id}",
        manufacturer="Base Power",
        model="Home Battery System",
    )


class BasePowerEntity(CoordinatorEntity[BasePowerCoordinator]):
    """An entity of one Base Power site."""

    _attr_has_entity_name = True
    # True for entities fed by the 15-minute usage tier. They go
    # unavailable when that data stops updating, so an old value is not
    # shown as current.
    _usage_tier = False

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        unique_key: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        site_id = entry.data[CONF_SERVICE_LOCATION_ID]
        self._attr_unique_id = f"{site_id}_{unique_key}"
        self._attr_device_info = device_info(site_id)

    @property
    def available(self) -> bool:
        """Unavailable when the coordinator failed or this entity's data is stale."""
        if self._usage_tier and self.coordinator.usage_stale:
            return False
        return super().available
