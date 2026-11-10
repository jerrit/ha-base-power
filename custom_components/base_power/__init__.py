"""The Base Power integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .api import BasePowerApiClient
from .auth import BasePowerAuth
from .const import (
    CONF_CLIENT_TOKEN,
    CONF_SERVICE_LOCATION_ID,
    CONF_SESSION_ID,
    CONF_SESSION_JWT,
    DOMAIN,
    GRID_SCAN_INTERVAL_SECONDS,
    PLATFORMS,
    STORAGE_VERSION,
    totals_store_key,
)
from .coordinator import BasePowerCoordinator
from .entity import BasePowerConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: BasePowerConfigEntry) -> bool:
    """Set up Base Power from a config entry."""
    session = async_get_clientsession(hass)
    # Clerk sets cookies, so it gets a session of its own. Home Assistant
    # closes it when the entry unloads, and when setup fails.
    clerk_session = async_create_clientsession(hass)

    @callback
    def save_client_token(token: str) -> None:
        """Keep a client token Clerk rotated, so it is still valid after a restart."""
        data = {key: value for key, value in entry.data.items() if key != CONF_SESSION_JWT}
        data[CONF_CLIENT_TOKEN] = token
        hass.config_entries.async_update_entry(entry, data=data)

    auth = BasePowerAuth(
        session=clerk_session,
        client_token=entry.data[CONF_CLIENT_TOKEN],
        session_id=entry.data[CONF_SESSION_ID],
        on_client_token=save_client_token,
    )
    # Every API call asks auth for the token, so a refresh with many calls
    # never runs on an expired one.
    api_client = BasePowerApiClient(
        session=session, token_provider=auth.async_ensure_valid_token
    )

    coordinator = BasePowerCoordinator(
        hass=hass,
        api_client=api_client,
        auth=auth,
        service_location_id=entry.data[CONF_SERVICE_LOCATION_ID],
        config_entry=entry,
    )

    await coordinator.async_load_totals()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _remove_wifi_select(hass, entry)

    # Live grid and battery every 60 seconds. The coordinator refresh is the
    # 15-minute usage tier and does not repeat these two calls.
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            coordinator.async_poll_grid_status,
            timedelta(seconds=GRID_SCAN_INTERVAL_SECONDS),
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _remove_wifi_select(hass: HomeAssistant, entry: BasePowerConfigEntry) -> None:
    """Remove the WiFi Network select from versions before 1.14.

    Base now reports which network the battery is on, so there is nothing
    to choose.
    """
    registry = er.async_get(hass)
    unique_id = f"{entry.data[CONF_SERVICE_LOCATION_ID]}_wifi_ssid_select"
    entity_id = registry.async_get_entity_id("select", DOMAIN, unique_id)
    if entity_id:
        registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: BasePowerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BasePowerConfigEntry) -> None:
    """Delete the saved energy totals when the integration is removed."""
    await Store(hass, STORAGE_VERSION, totals_store_key(entry.entry_id)).async_remove()
