"""Select entities for Base Power integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SERVICE_LOCATION_ID, CONF_WIFI_SSID
from .coordinator import BasePowerCoordinator

_AUTO = "Auto (strongest signal)"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Base Power select entities from a config entry."""
    coordinator: BasePowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BasePowerWifiSsidSelect(coordinator, entry)])


class BasePowerWifiSsidSelect(CoordinatorEntity[BasePowerCoordinator], SelectEntity):
    """Select entity for choosing which WiFi network to track."""

    _attr_has_entity_name = True
    _attr_name = "WiFi Network"
    _attr_icon = "mdi:wifi-settings"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: BasePowerCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_wifi_ssid_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.data[CONF_SERVICE_LOCATION_ID])},
            "name": f"Base Power {entry.data[CONF_SERVICE_LOCATION_ID]}",
            "manufacturer": "Base Power",
            "model": "Home Battery System",
        }

    def _scan(self) -> dict[str, int | None]:
        if self.coordinator.data:
            return self.coordinator.data.get("wifi", {}).get("scan", {})
        return {}

    @property
    def options(self) -> list[str]:
        """Return available networks sorted by signal strength."""
        scan = self._scan()
        opts: list[str] = [_AUTO]
        for ssid in sorted(scan, key=lambda s: scan[s] or 0, reverse=True):
            opts.append(ssid)
        # Keep the currently configured SSID in the list even if it fell off the scan
        configured = self._entry.options.get(CONF_WIFI_SSID, "")
        if configured and configured not in opts:
            opts.append(configured)
        return opts

    @property
    def current_option(self) -> str:
        """Return the currently selected option."""
        configured = self._entry.options.get(CONF_WIFI_SSID, "")
        return configured if configured else _AUTO

    async def async_select_option(self, option: str) -> None:
        """Persist the selection and trigger a coordinator refresh."""
        ssid = "" if option == _AUTO else option
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_WIFI_SSID: ssid},
        )
        await self.coordinator.async_request_refresh()
