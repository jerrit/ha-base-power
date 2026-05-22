"""Base Power Home Assistant integration."""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BasePowerAPI
from .auth import ClerkAuth, ClerkAuthError
from .const import (
    CONF_CLIENT_TOKEN,
    CONF_SERVICE_LOCATION_ID,
    CONF_SESSION_ID,
    DOMAIN,
)
from .coordinator import BasePowerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Base Power from a config entry."""
    session = async_get_clientsession(hass)

    auth = ClerkAuth(session)
    auth.restore_session(
        client_token=entry.data[CONF_CLIENT_TOKEN],
        session_id=entry.data[CONF_SESSION_ID],
    )

    api = BasePowerAPI(
        session=session,
        auth=auth,
        service_location_id=entry.data[CONF_SERVICE_LOCATION_ID],
    )

    try:
        await auth.refresh_jwt()
    except ClerkAuthError as err:
        raise ConfigEntryNotReady(f"Could not authenticate with Base Power: {err}") from err

    coordinator = BasePowerCoordinator(hass, api, auth)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
