"""Button entities for Base Power."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BasePowerAPI, BasePowerAPIError
from .const import DOMAIN
from .coordinator import BasePowerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Base Power buttons."""
    coordinator: BasePowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.data.get("service_location_id", entry.entry_id))},
        name=entry.title,
        manufacturer="Base Power",
        model="Home Battery System",
        configuration_url="https://basepowercompany.com",
    )
    async_add_entities(
        [
            ManualBackupButton(coordinator, entry, device_info),
            OvercurrentResetButton(coordinator, entry, device_info),
        ]
    )


class _BasePowerButton(ButtonEntity):
    """Base class for Base Power action buttons."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BasePowerCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
    ) -> None:
        self._api: BasePowerAPI = coordinator.api
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        raise NotImplementedError


class ManualBackupButton(_BasePowerButton):
    """Triggers the battery to enter manual backup mode."""

    _attr_name = "Trigger Manual Backup"
    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, coordinator, entry, device_info) -> None:
        super().__init__(coordinator, entry, device_info)
        self._attr_unique_id = f"{entry.entry_id}_manual_backup"

    async def async_press(self) -> None:
        try:
            await self._api.send_manual_backup()
        except BasePowerAPIError as err:
            raise HomeAssistantError(f"Failed to trigger manual backup: {err}") from err


class OvercurrentResetButton(_BasePowerButton):
    """Resets the overcurrent protection circuit."""

    _attr_name = "Reset Overcurrent Protection"
    _attr_icon = "mdi:restore-alert"

    def __init__(self, coordinator, entry, device_info) -> None:
        super().__init__(coordinator, entry, device_info)
        self._attr_unique_id = f"{entry.entry_id}_overcurrent_reset"

    async def async_press(self) -> None:
        try:
            await self._api.send_overcurrent_reset()
        except BasePowerAPIError as err:
            raise HomeAssistantError(f"Failed to reset overcurrent protection: {err}") from err
