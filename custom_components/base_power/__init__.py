"""The Base Power integration."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BasePowerApiClient
from .auth import BasePowerAuth
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_CLIENT_TOKEN,
    CONF_SESSION_ID,
    CONF_SERVICE_LOCATION_ID,
)
from .coordinator import BasePowerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Base Power from a config entry."""
    session = async_get_clientsession(hass)

    auth = BasePowerAuth(
        session=session,
        client_token=entry.data[CONF_CLIENT_TOKEN],
        session_id=entry.data[CONF_SESSION_ID],
    )

    api_client = BasePowerApiClient(session=session)

    coordinator = BasePowerCoordinator(
        hass=hass,
        api_client=api_client,
        auth=auth,
        service_location_id=entry.data[CONF_SERVICE_LOCATION_ID],
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
